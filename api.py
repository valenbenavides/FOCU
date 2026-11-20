"""FOCU: servidor web (FastAPI). Sirve la API en /api y la página en /."""
from pathlib import Path

from dotenv import load_dotenv
_BASE = Path(__file__).parent
load_dotenv(_BASE / ".env")  # antes de importar db

import hmac
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal, Optional, Union

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import acceso
import cargas
import correo
import db
import seguridad
import requisitos
from agente import crear_chat, sistema_con_datos
import agente_vri
from convocatorias import normalizar_convocatoria
from llm import es_error_temporal
from matching import RUTA_JSON, cargar_convocatorias, comparar, esta_vigente, rankear
from schema import CATEGORIAS, ETIQUETAS, TIPO_FIJO, TIPOS_PERFIL, con_tipo_fijo
from scrapers import recolector

WEB = _BASE / "web"

_scraper = {"corriendo": False, "inicio": None, "informe": None}
_scraper_lock = threading.Lock()


def _tarea_scrapers():
    con = db.conectar()
    try:
        _scraper["informe"] = recolector.recolectar(con)
    except Exception as e:
        _scraper["informe"] = {"error": str(e)}
    finally:
        con.close()
        _scraper["corriendo"] = False


def iniciar_scrapers() -> bool:
    """Lanza la recolección si no hay una en curso. Devuelve False si ya estaba corriendo."""
    with _scraper_lock:
        if _scraper["corriendo"]:
            return False
        _scraper.update(corriendo=True, inicio=time.strftime("%Y-%m-%d %H:%M:%S"), informe=None)
    threading.Thread(target=_tarea_scrapers, daemon=True).start()
    return True


def _bucle_programado(horas: float):
    while True:
        time.sleep(horas * 3600)
        iniciar_scrapers()


def _sembrar_convocatorias(con):
    """Carga datos/convocatorias.json si la base está vacía y el archivo existe."""
    if db.contar_convocatorias(con) > 0 or not RUTA_JSON.exists():
        return
    for raw in json.loads(RUTA_JSON.read_text(encoding="utf-8")):
        db.guardar_convocatoria(con, normalizar_convocatoria(raw), fuente="manual", verificada=True)
    con.commit()
    print(f"[arranque] Convocatorias iniciales cargadas desde {RUTA_JSON.name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seguridad._secreto()
    con = db.conectar()
    db.iniciar_db(con)
    _sembrar_convocatorias(con)
    if not db.hay_admin(con):
        mail = os.environ.get("ADMIN_CORREO", "").strip()
        if mail:
            try:
                cargas.agregar_administrador(con, os.environ.get("ADMIN_NOMBRE", "Director VRI"), mail)
                con.commit()
                print(f"[arranque] Director creado: {mail}. Entra con ese correo; el código le llegará por correo.")
            except ValueError as e:
                print(f"[arranque] No se pudo crear el director inicial: {e}")
        else:
            print("[arranque] ATENCIÓN: no hay ningún director. Define ADMIN_CORREO en las variables de entorno.")
    con.close()
    horas = float(os.environ.get("SCRAPER_CADA_HORAS", "0") or 0)
    if horas > 0:
        threading.Thread(target=_bucle_programado, args=(horas,), daemon=True).start()
    yield


app = FastAPI(title="FOCU", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def cabeceras_seguras(request: Request, call_next):
    respuesta = await call_next(request)
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "DENY"
    respuesta.headers["Referrer-Policy"] = "same-origin"
    return respuesta


router = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)


def get_con():
    con = db.conectar()
    try:
        yield con
    finally:
        con.close()


def usuario_actual(cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                   con=Depends(get_con)) -> dict:
    if cred is None:
        raise HTTPException(401, "Falta el token. Inicia sesión en /api/login.")
    uid = seguridad.leer_token(cred.credentials)
    usuario = db.usuario_por_id(con, uid) if uid is not None else None
    if not usuario or not usuario["activo"]:
        raise HTTPException(401, "Token inválido, vencido o usuario desactivado.")
    return usuario


def solo_admin(u: dict = Depends(usuario_actual)) -> dict:
    if u["rol"] != "admin":
        raise HTTPException(403, "Solo para administradores de la VRI.")
    return u


def solo_estudiante(u: dict = Depends(usuario_actual)) -> dict:
    if u["rol"] != "estudiante":
        raise HTTPException(403, "Solo para estudiantes de semillero.")
    return u


def admin_o_director(u: dict = Depends(usuario_actual)) -> dict:
    """Quien puede cambiar su propio correo: la VRI y los directores."""
    if u["rol"] != "admin" and not u["es_director_grupo"]:
        raise HTTPException(403, "El correo de un integrante lo cambia su director de grupo o la VRI.")
    return u


def solo_director_grupo(u: dict = Depends(solo_estudiante)) -> dict:
    if not u["es_director_grupo"] or not u.get("grupo_id"):
        raise HTTPException(403, "Solo para el director de un grupo de investigación.")
    return u


def _ip(request: Request) -> str:
    adelante = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return adelante or (request.client.host if request.client else "desconocida")


Tipo = Literal[tuple(CATEGORIAS["tipo_beneficiario"])]
Area = Literal[tuple(CATEGORIAS["area_conocimiento"])]
Alcance = Literal[tuple(CATEGORIAS["alcance"])]
Fase = Literal[tuple(CATEGORIAS["fase_proyecto"])]


class SolicitarCodigoIn(BaseModel):
    correo: str = Field(min_length=5, max_length=120)


class LoginIn(BaseModel):
    correo: str = Field(min_length=5, max_length=120)
    codigo: str = Field(min_length=4, max_length=40)


class PerfilIn(BaseModel):
    tipo_beneficiario: Optional[Tipo] = None
    area_conocimiento: Optional[Area] = None
    alcance: Optional[Alcance] = None
    fase_proyecto: Optional[Fase] = None
    monto_requerido_cop: Optional[int] = Field(default=None, ge=0)
    descripcion_proyecto: Optional[str] = Field(default=None, max_length=2000)


class PostulacionIn(BaseModel):
    convocatoria_id: Union[str, int]
    titulo: Optional[str] = Field(default=None, max_length=200)
    nota: Optional[str] = Field(default=None, max_length=1000)


class RequisitoIn(BaseModel):
    estado: Literal["pendiente", "si", "no", "no_aplica"]
    observacion: Optional[str] = Field(default=None, max_length=500)


class DecidirPostulacionDirectorIn(BaseModel):
    aprobar: bool
    comentario: Optional[str] = Field(default=None, max_length=1000)


class ReenviarPostulacionIn(BaseModel):
    nota: Optional[str] = Field(default=None, max_length=1000)


class DecisionIn(BaseModel):
    estado: Literal["aprobada", "rechazada"]
    comentario: Optional[str] = Field(default=None, max_length=1000)


class EstudianteIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(min_length=5, max_length=120)
    semillero: str = Field(default="", max_length=120)
    grupo: str = Field(min_length=2, max_length=120)
    facultad: str = Field(min_length=2, max_length=120)
    descripcion: Optional[str] = Field(default=None, max_length=2000)


class DirectorIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(min_length=5, max_length=120)
    grupo_id: int
    facultad: str = Field(min_length=2, max_length=120)
    semillero: Optional[str] = Field(default=None, max_length=120)


class EstudianteEditIn(BaseModel):
    """Todos los campos son opcionales: solo se cambia lo que se envíe."""
    activo: Optional[bool] = None
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=120)
    correo: Optional[str] = Field(default=None, max_length=120)
    semillero: Optional[str] = Field(default=None, min_length=2, max_length=120)
    grupo: Optional[str] = Field(default=None, min_length=2, max_length=120)
    facultad: Optional[str] = Field(default=None, min_length=2, max_length=120)
    es_director: Optional[bool] = None


class AdminIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(min_length=5, max_length=120)


class RegistroSolicitarIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(min_length=5, max_length=120)
    facultad: str = Field(default="", max_length=160)
    grupo_id: int
    semillero: str = Field(default="", max_length=120)


class RegistroConfirmarIn(BaseModel):
    correo: str = Field(min_length=5, max_length=120)
    codigo: str = Field(min_length=4, max_length=40)


class DecidirSolicitudIn(BaseModel):
    aprobar: bool
    comentario: Optional[str] = Field(default=None, max_length=1000)


class MiembroIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(min_length=5, max_length=120)
    facultad: str = Field(min_length=2, max_length=120)
    semillero: str = Field(default="", max_length=120)
    descripcion: Optional[str] = Field(default=None, max_length=2000)


class MiembroEditIn(BaseModel):
    activo: bool


class GrupoIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=160)
    descripcion: str = Field(default="", max_length=2000)
    area: str = Field(default="", max_length=60)
    facultad: str = Field(min_length=2, max_length=120)
    director: str = Field(min_length=2, max_length=120)
    correo_director: str = Field(min_length=5, max_length=120)


class GrupoEditIn(BaseModel):
    """Solo se cambia lo que se envíe."""
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=160)
    descripcion: Optional[str] = Field(default=None, max_length=2000)
    area: Optional[str] = Field(default=None, max_length=60)
    facultad: Optional[str] = Field(default=None, max_length=120)
    director: Optional[str] = Field(default=None, max_length=120)
    correo_director: Optional[str] = Field(default=None, max_length=120)


class ChatIn(BaseModel):
    mensaje: str = Field(min_length=1, max_length=2000)
    conversacion_id: Optional[int] = None


class ConvocatoriaIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=300)
    entidad: str = Field(default="", max_length=200)
    fecha_limite: Optional[str] = Field(default=None, max_length=10)
    url: str = Field(default="", max_length=500)
    objetivo: str = Field(default="", max_length=2000)
    requisitos: str = Field(default="", max_length=3000)
    beneficios: str = Field(default="", max_length=2000)
    monto_min: Optional[int] = Field(default=None, ge=0)
    monto_max: Optional[int] = Field(default=None, ge=0)
    tipos_beneficiario: list[str] = []
    areas: list[str] = []
    fases: list[str] = []
    alcances: list[str] = []


class ConvocatoriaUrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)


class ConvocatoriaEstadoIn(BaseModel):
    activa: Optional[bool] = None
    verificada: Optional[bool] = None
    fecha_limite: Optional[str] = Field(default=None, max_length=10)
    recuperar: Optional[bool] = None


_sesiones: dict[int, dict] = {}


def _nuevo_agente(s: dict, u: dict):
    """Agente de la VRI (solo lectura) o del estudiante."""
    if u["rol"] == "admin":
        return agente_vri.crear_chat_vri(s["ctx"])
    return crear_chat(s["perfil"], s["resultados"])


def _sesion(u: dict, con=None) -> dict:
    s = _sesiones.get(u["id"])
    if s is None:
        s = {"perfil": con_tipo_fijo(u["perfil"]), "resultados": [], "conv_id": None, "ctx": {"con": None}}
        s["chat"] = _nuevo_agente(s, u)
        _sesiones[u["id"]] = s
    return s


def _cambiar_conversacion(s: dict, u: dict, con, conv_id: Optional[int]) -> None:
    """Deja al agente con el contexto de la conversación pedida (o vacío si es nueva)."""
    if s.get("conv_id") == conv_id and conv_id is not None:
        return
    s["chat"] = _nuevo_agente(s, u)
    s["conv_id"] = conv_id
    if conv_id is not None:
        for m in db.mensajes_de_conversacion(con, u["id"], conv_id, 20):
            s["chat"].mensajes.append({"role": "user" if m["rol"] == "usuario" else "assistant",
                                       "content": m["texto"]})


@router.get("/salud")
def salud():
    return {"ok": True}


@router.get("/meta")
def meta():
    """Categorías y etiquetas para armar los formularios del frontend."""
    return {"categorias": CATEGORIAS, "etiquetas": ETIQUETAS, "tipo_fijo": TIPO_FIJO,
            "tipos_perfil": TIPOS_PERFIL}


@router.get("/aviso")
def aviso():
    ruta = _BASE / "datos" / "aviso_datos.txt"
    texto = ruta.read_text(encoding="utf-8") if ruta.exists() else "Aviso de datos personales pendiente de redactar."
    return {"texto": texto}


MSG_CODIGO = ("Si tu correo está registrado en un grupo de investigación, te enviamos un código "
              f"de acceso. Vale {seguridad.MINUTOS_CODIGO} minutos y sirve una sola vez.")


MENSAJES_INGRESO = {
    "no_registrado": "Este correo no está registrado. Solicita tu registro.",
    "pendiente": "Tu solicitud de registro está pendiente: el director de tu grupo aún no la responde. "
                 "Cuando la apruebe podrás ingresar con este correo.",
    "rechazada": "Tu solicitud de registro no fue aprobada. Habla con el director de tu grupo.",
    "desactivado": "Tu cuenta está desactivada. Habla con el director de tu grupo o con la Vicerrectoría de Investigaciones.",
    "sin_grupo": "Tu cuenta no está asociada a ningún grupo de investigación. Habla con la Vicerrectoría de Investigaciones.",
}


@router.post("/login/solicitar")
def solicitar_codigo(datos: SolicitarCodigoIn, request: Request, con=Depends(get_con)):
    """Envía un código temporal si el correo puede ingresar; si no, dice por qué."""
    mail = seguridad.normalizar_correo(datos.correo)
    if (seguridad.limite_excedido("ip:" + _ip(request), 20, 900)
            or seguridad.limite_excedido("mail:" + mail, 3, 900)):
        raise HTTPException(429, "Pediste demasiados códigos. Espera unos minutos e inténtalo de nuevo.")
    estado = acceso.estado_de_correo(con, mail)
    if estado != "ok":
        return {"enviado": False, "estado": estado, "mensaje": MENSAJES_INGRESO[estado]}
    u, codigo = acceso.emitir_codigo(con, mail)
    correo.enviar(
        u["correo"], "Tu código de acceso a FOCU",
        f"Hola {u['nombre']},\n\nTu código de acceso a FOCU es: {codigo}\n\n"
        f"Vale {seguridad.MINUTOS_CODIGO} minutos y solo se puede usar una vez. "
        "Si no lo pediste tú, ignora este mensaje.\n\nUniversidad del Cauca · FOCU")
    return {"enviado": True, "estado": "ok", "mensaje": MSG_CODIGO}


@router.post("/login")
def login(datos: LoginIn, request: Request, con=Depends(get_con)):
    ip = _ip(request)
    if seguridad.login_bloqueado(ip):
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos.")
    u, _ = acceso.verificar_codigo(con, datos.correo, datos.codigo)
    if u:
        return {"token": seguridad.crear_token(u["id"]), "rol": u["rol"], "nombre": u["nombre"],
                "consentimiento": u["consentimiento"]}
    seguridad.registrar_fallo(ip)
    raise HTTPException(401, "Código no válido o vencido. Pide uno nuevo.")


def _etiqueta_perfil(p: dict) -> str:
    if p["rol"] == "admin":
        return "VRI · Administración"
    return ("Director/a · " if p["es_director_grupo"] else "Integrante · ") + (p.get("grupo") or "sin grupo")


@router.get("/yo")
def yo(u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """Datos del usuario con sesión iniciada."""
    datos = {k: u[k] for k in ("id", "nombre", "correo", "rol", "semillero", "grupo", "grupo_id",
                               "es_director_grupo", "consentimiento", "perfil", "facultad")}
    datos["perfiles"] = [{"id": p["id"], "etiqueta": _etiqueta_perfil(p), "actual": p["id"] == u["id"]}
                         for p in db.usuarios_por_correo(con, u["correo"]) if db.puede_ingresar(p)] if u.get("correo") else []
    return datos


class CambioPerfilIn(BaseModel):
    usuario_id: int


@router.post("/yo/perfil")
def cambiar_perfil(datos: CambioPerfilIn, u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """Cambia a otro perfil del mismo correo y devuelve un token nuevo."""
    if not u.get("correo"):
        raise HTTPException(400, "Tu cuenta no tiene correo.")
    destino = next((p for p in db.usuarios_por_correo(con, u["correo"])
                    if p["id"] == datos.usuario_id and db.puede_ingresar(p)), None)
    if not destino:
        raise HTTPException(404, "Ese perfil no existe o no está disponible.")
    return {"token": seguridad.crear_token(destino["id"]), "rol": destino["rol"], "nombre": destino["nombre"],
            "consentimiento": destino["consentimiento"]}


class YoEditIn(BaseModel):
    """Datos que la persona puede actualizar de sí misma."""
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=120)
    facultad: Optional[str] = Field(default=None, min_length=2, max_length=120)
    semillero: Optional[str] = Field(default=None, min_length=2, max_length=120)


@router.patch("/yo")
def actualizar_yo(datos: YoEditIn, u: dict = Depends(usuario_actual), con=Depends(get_con)):
    cambios = datos.model_dump(exclude_none=True)
    if not cambios:
        raise HTTPException(400, "No enviaste ningún cambio.")
    if "nombre" in cambios:
        con.execute("UPDATE usuarios SET nombre = ? WHERE id = ?", (cambios["nombre"].strip(), u["id"]))
    if "facultad" in cambios and u["rol"] != "admin":
        con.execute("UPDATE usuarios SET facultad = ? WHERE id = ?", (cambios["facultad"].strip(), u["id"]))
    if "semillero" in cambios and u["rol"] != "admin":
        if not u.get("grupo_id"):
            raise HTTPException(400, "No perteneces a un grupo: pide a la VRI que te asigne uno.")
        sid = db.obtener_o_crear_semillero(con, cambios["semillero"].strip(), u["grupo_id"])
        con.execute("UPDATE usuarios SET semillero_id = ? WHERE id = ?", (sid, u["id"]))
    con.commit()
    return {"actualizado": list(cambios)}


class CampoContactoIn(BaseModel):
    titulo: str = Field(min_length=1, max_length=60)
    valor: str = Field(min_length=1, max_length=300)


class ContactoIn(BaseModel):
    campos: list[CampoContactoIn] = Field(max_length=20)


@router.get("/contacto")
def ver_contacto(con=Depends(get_con)):
    """Datos de contacto del pie de página. Son públicos: los edita la VRI en Mi información."""
    return {"campos": db.obtener_ajuste(con, "contacto", [])}


@router.put("/admin/contacto")
def guardar_contacto(datos: ContactoIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    campos = [{"titulo": c.titulo.strip(), "valor": c.valor.strip()} for c in datos.campos
              if c.titulo.strip() and c.valor.strip()]
    db.guardar_ajuste(con, "contacto", campos)
    return {"campos": campos}


_cambios_correo: dict[int, dict] = {}


class CorreoNuevoIn(BaseModel):
    correo: str = Field(min_length=5, max_length=120)


class CorreoCodigoIn(BaseModel):
    codigo: str = Field(min_length=4, max_length=40)


@router.post("/yo/correo")
def pedir_cambio_correo(datos: CorreoNuevoIn, u: dict = Depends(admin_o_director), con=Depends(get_con)):
    nuevo = seguridad.normalizar_correo(datos.correo)
    if not cargas.RE_CORREO.match(nuevo):
        raise HTTPException(400, "El correo no es válido.")
    if nuevo == (u["correo"] or "").lower():
        raise HTTPException(400, "Ese ya es tu correo.")
    otro = db.usuario_en_grupo_por_correo(con, nuevo, u.get("grupo_id") if u["rol"] != "admin" else None)
    if otro and otro["id"] != u["id"]:
        raise HTTPException(409, "Ese correo ya pertenece a otra persona.")
    previo = _cambios_correo.get(u["id"])
    if previo and time.time() - previo["pedido"] < 60:
        raise HTTPException(429, "Espera un minuto antes de pedir otro código.")
    codigo = seguridad.generar_codigo_temporal()
    _cambios_correo[u["id"]] = {"correo": nuevo, "hash": seguridad.hash_codigo(codigo, nuevo), "pedido": time.time(),
                                "expira": time.time() + seguridad.MINUTOS_CODIGO * 60, "intentos": 0}
    correo.enviar(nuevo, "Confirma tu nuevo correo en FOCU",
                  f"Hola {u['nombre']},\n\nTu código para confirmar este correo en FOCU es: {codigo}\n\n"
                  f"Vale {seguridad.MINUTOS_CODIGO} minutos y solo se puede usar una vez. "
                  "Si no lo pediste tú, ignora este mensaje.\n\nUniversidad del Cauca · FOCU")
    return {"enviado": True, "correo": nuevo}


@router.post("/yo/correo/confirmar")
def confirmar_cambio_correo(datos: CorreoCodigoIn, u: dict = Depends(admin_o_director), con=Depends(get_con)):
    p = _cambios_correo.get(u["id"])
    if not p or p["expira"] < time.time() or p["intentos"] >= seguridad.MAX_INTENTOS_CODIGO:
        _cambios_correo.pop(u["id"], None)
        raise HTTPException(400, "El código venció o ya no es válido. Pide uno nuevo.")
    if not hmac.compare_digest(p["hash"], seguridad.hash_codigo(datos.codigo.strip(), p["correo"])):
        p["intentos"] += 1
        raise HTTPException(401, "Código incorrecto.")
    otro = db.usuario_en_grupo_por_correo(con, p["correo"], u.get("grupo_id") if u["rol"] != "admin" else None)
    if otro and otro["id"] != u["id"]:
        _cambios_correo.pop(u["id"], None)
        raise HTTPException(409, "Ese correo ya pertenece a otra persona.")
    con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", (p["correo"], u["id"]))
    con.commit()
    _cambios_correo.pop(u["id"], None)
    return {"correo": p["correo"]}


@router.post("/consentimiento")
def aceptar_consentimiento(u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """El estudiante acepta el tratamiento de datos personales (Ley 1581 de 2012)."""
    con.execute("UPDATE usuarios SET consentimiento = 1 WHERE id = ?", (u["id"],))
    con.commit()
    return {"consentimiento": True}


@router.put("/perfil")
def guardar_perfil(datos: PerfilIn, u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """Reemplaza el perfil completo. Los campos que no se envíen quedan vacíos."""
    perfil = con_tipo_fijo(datos.model_dump(exclude_none=True))
    db.guardar_perfil(con, u["id"], perfil)
    s = _sesiones.get(u["id"])
    if s:
        s["perfil"].clear()
        s["perfil"].update(perfil)
        s["resultados"].clear()
    return {"perfil": perfil}


@router.get("/convocatorias")
def listar_convocatorias(u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """Convocatorias activas con visto bueno que aún no cierran."""
    convs = db.listar_convocatorias(con, solo_activas=True)
    return convs if u["rol"] == "admin" else [c for c in convs if c.get("verificada") and esta_vigente(c)]


@router.post("/convocatorias/buscar")
def buscar_convocatorias(u: dict = Depends(solo_estudiante)):
    """Ranking con % de compatibilidad según el perfil guardado del estudiante."""
    if not u["perfil"]:
        raise HTTPException(400, "Primero completa tu perfil.")
    return rankear(con_tipo_fijo(u["perfil"]), cargar_convocatorias())


@router.post("/postulaciones", status_code=201)
def crear_postulacion(datos: PostulacionIn, u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """Inicia la postulación del grupo; la revisa el director y luego la VRI."""
    if not u["consentimiento"]:
        raise HTTPException(403, "Debes aceptar el tratamiento de datos antes de postularte.")
    cid = str(datos.convocatoria_id)
    conv = db.obtener_convocatoria(con, cid)
    if not conv or not conv.get("activa", True) or not conv.get("verificada"):
        raise HTTPException(404, "Esa convocatoria no existe o aún no tiene el visto bueno de la VRI.")
    if not esta_vigente(conv):
        raise HTTPException(409, "Esa convocatoria ya cerró.")
    if not u.get("grupo_id"):
        raise HTTPException(400, "Tu cuenta no está asociada a un grupo de investigación.")
    previa = db.postulacion_del_grupo(con, u["grupo_id"], cid)
    if previa:
        raise HTTPException(409, f"Tu grupo ya tiene esta postulación (la inició {previa['estudiante']}). "
                                 "Búscala en la pestaña Postulaciones.")
    correos_dir = db.correos_directores_de_grupo(con, u["grupo_id"])
    pasa_por_director = bool(correos_dir) and not u["es_director_grupo"]
    etapa = "pendiente" if pasa_por_director else "aprobada"
    try:
        nuevo_id = con.insertar(
            "INSERT INTO postulaciones (usuario_id, grupo_id, convocatoria_id, titulo, nota, etapa_grupo) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (u["id"], u["grupo_id"], cid, (datos.titulo or "").strip() or None, datos.nota, etapa))
        con.commit()
    except db.ErrorIntegridad:
        raise HTTPException(409, "Tu grupo ya tiene esta postulación. Búscala en la pestaña Postulaciones.")
    if pasa_por_director:
        _avisar_postulacion_a_directores(correos_dir, u, conv["nombre"], datos.nota)
    else:
        _avisar_postulacion_a_vri(con, u, conv["nombre"], datos.nota)
    return {"id": nuevo_id, "estado": "solicitada",
            "fase": "en_director" if pasa_por_director else "en_vri"}


def _avisar_postulacion_a_directores(correos: list[str], u: dict, convocatoria: str, nota: Optional[str]) -> None:
    correo.enviar_a_varios(
        correos, f"Nueva postulación en tu grupo: {convocatoria}",
        f"{u['nombre']} quiere postularse a «{convocatoria}».\n"
        + (f"Nota: {nota}\n" if nota else "")
        + "\nEntra a FOCU, sección «Postulaciones de mi grupo», para aprobarla o devolverla con una observación.")


def _avisar_postulacion_a_vri(con, u: dict, convocatoria: str, nota: Optional[str]) -> None:
    correo.enviar_a_varios(
        db.correos_administradores(con), f"Nueva solicitud de postulación: {convocatoria}",
        f"{u['nombre']} ({u['grupo'] or 'sin grupo'}) quiere postularse a «{convocatoria}».\n"
        + (f"Nota: {nota}\n" if nota else "")
        + "\nEntra a FOCU, sección Postulaciones, para darle el visto bueno o rechazarla.")


def _con_nombre_de_convocatoria(filas: list[dict], con) -> list[dict]:
    convs = {c["id"]: c for c in db.listar_convocatorias(con, solo_activas=False)}
    estados = db.estados_requisitos(con, [f["id"] for f in filas])
    for f in filas:
        c = convs.get(f["convocatoria_id"], {})
        f["avales"] = requisitos.resumen(estados.get(f["id"], {}), requisitos.lista_para(c or None))
        f["convocatoria"] = c.get("nombre", "(convocatoria retirada)")
        f["entidad"] = c.get("entidad", "")
        f["fecha_limite"] = c.get("fecha_limite")
    return filas


@router.get("/postulaciones")
def mis_postulaciones(u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """Las postulaciones de mi grupo: las ve todo el grupo."""
    if u.get("grupo_id"):
        filas = db.listar_postulaciones(con, grupo_id=u["grupo_id"])
    else:
        filas = db.listar_postulaciones(con, usuario_id=u["id"])
    return _con_nombre_de_convocatoria(filas, con)


def _postulacion_visible(u: dict, postulacion_id: int, con, para_editar: bool) -> dict:
    """La postulación, si esta persona puede verla."""
    p = db.obtener_postulacion(con, postulacion_id)
    if not p:
        raise HTTPException(404, "Postulación no encontrada.")
    if u["rol"] == "admin":
        if para_editar:
            raise HTTPException(403, "La VRI revisa la lista, pero no la llena.")
        return p
    del_grupo = bool(u.get("grupo_id")) and p["grupo_id"] == u["grupo_id"]
    if not del_grupo:
        raise HTTPException(404, "Postulación no encontrada.")
    if para_editar:
        if p["estado"] == "aprobada":
            raise HTTPException(409, "Esa postulación ya fue aprobada por la VRI.")
        if not _puede_llenar_requisitos(u, p, con):
            raise HTTPException(403, "Solo quien inició la postulación y el director del grupo marcan estos requisitos.")
    return p


def _puede_llenar_requisitos(u: dict, p: dict, con) -> bool:
    """Director e iniciador llenan la lista hasta que la VRI aprueba; los avales solo el director."""
    if u["rol"] == "admin" or p["estado"] == "aprobada":
        return False
    if u.get("es_director_grupo") and u.get("grupo_id") and p["grupo_id"] == u["grupo_id"]:
        return True
    return p["usuario_id"] == u["id"]


def _es_director_de(u: dict, p: dict) -> bool:
    return bool(u.get("es_director_grupo") and u.get("grupo_id") and p["grupo_id"] == u["grupo_id"])


@router.get("/postulaciones/{postulacion_id}/requisitos")
def ver_requisitos(postulacion_id: int, u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """Lista de chequeo de la postulación (formato FOR-40 de la VRI) con el estado de cada ítem."""
    p = _postulacion_visible(u, postulacion_id, con, para_editar=False)
    guardados = db.obtener_requisitos(con, postulacion_id)
    conv = db.obtener_convocatoria(con, p["convocatoria_id"])
    lista = requisitos.lista_para(conv)
    items = []
    for r in lista:
        g = guardados.get(r["clave"], {})
        items.append({**r, "solo_director": r["clave"] in requisitos.AVALES and not _es_director_de(u, p),
                      "estado": g.get("estado", "pendiente"), "observacion": g.get("observacion") or "",
                      "actualizado": g.get("actualizado"), "actualizado_por": g.get("actualizado_por")})
    estados = {r["clave"]: r["estado"] for r in items}
    return {"postulacion": {"id": p["id"], "estudiante": p["estudiante"], "fase": p["fase"],
                            "convocatoria_id": p["convocatoria_id"]},
            "convocatoria": (conv or {}).get("nombre", ""), "interna": lista is requisitos.REQUISITOS,
            "requisitos": items, "resumen": requisitos.resumen(estados, lista),
            "puede_editar": _puede_llenar_requisitos(u, p, con)}


@router.put("/postulaciones/{postulacion_id}/requisitos/{clave}")
def marcar_requisito(postulacion_id: int, clave: str, datos: RequisitoIn,
                     u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """El director del grupo marca un requisito como sí, no, pendiente o no aplica."""
    p = _postulacion_visible(u, postulacion_id, con, para_editar=True)
    lista = requisitos.lista_para(db.obtener_convocatoria(con, p["convocatoria_id"]))
    if clave in requisitos.AVALES and not _es_director_de(u, p):
        raise HTTPException(403, "Los avales los marca el director de tu grupo.")
    actuales = {c: v["estado"] for c, v in db.obtener_requisitos(con, postulacion_id).items()}
    try:
        requisitos.validar_cambio(actuales, clave, datos.estado, lista)
    except ValueError as e:
        raise HTTPException(400, str(e))
    obs = (datos.observacion or "").strip() or None
    db.guardar_requisito(con, postulacion_id, clave, datos.estado, obs, u["id"])
    return {"clave": clave, "estado": datos.estado,
            "resumen": requisitos.resumen({**actuales, clave: datos.estado}, lista)}


@router.post("/postulaciones/{postulacion_id}/reenviar")
def reenviar_postulacion(postulacion_id: int, datos: ReenviarPostulacionIn,
                         u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """Corrige y reenvía una postulación devuelta o rechazada."""
    p = db.obtener_postulacion(con, postulacion_id)
    if not p or not u.get("grupo_id") or p["grupo_id"] != u["grupo_id"]:
        raise HTTPException(404, "Postulación no encontrada.")
    if p["fase"] not in ("devuelta", "rechazada"):
        raise HTTPException(409, "Esa postulación no está devuelta ni rechazada.")
    conv = db.obtener_convocatoria(con, p["convocatoria_id"]) or {}
    if p["fase"] == "rechazada" and conv and not esta_vigente(conv):
        raise HTTPException(409, "Esa convocatoria ya cerró: no se puede volver a postular.")
    nota = datos.nota if datos.nota is not None else p["nota"]
    correos_dir = db.correos_directores_de_grupo(con, u["grupo_id"])
    pasa_por_director = bool(correos_dir) and not u["es_director_grupo"]
    etapa = "pendiente" if pasa_por_director else "aprobada"
    if p["fase"] == "rechazada":
        con.execute("UPDATE postulaciones SET estado = 'solicitada', resuelta = NULL, etapa_grupo = ?, nota = ?, "
                    "comentario_director = NULL, decidida_director = NULL WHERE id = ?", (etapa, nota, postulacion_id))
    else:
        con.execute("UPDATE postulaciones SET etapa_grupo = ?, nota = ? WHERE id = ?", (etapa, nota, postulacion_id))
    con.commit()
    nombre_conv = conv.get("nombre", "la convocatoria")
    if pasa_por_director:
        _avisar_postulacion_a_directores(correos_dir, u, nombre_conv, nota)
    else:
        _avisar_postulacion_a_vri(con, u, nombre_conv, nota)
    return {"id": postulacion_id, "fase": "en_director" if pasa_por_director else "en_vri"}


@router.delete("/postulaciones/{postulacion_id}")
def cancelar_postulacion(postulacion_id: int, u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """El iniciador o el director cancelan la postulación mientras no esté aprobada."""
    p = db.obtener_postulacion(con, postulacion_id)
    if not p or not u.get("grupo_id") or p["grupo_id"] != u["grupo_id"]:
        raise HTTPException(404, "Postulación no encontrada.")
    if not (u["es_director_grupo"] or p["usuario_id"] == u["id"]):
        raise HTTPException(403, "Solo quien la inició o el director del grupo puede cancelarla.")
    if p["estado"] == "aprobada":
        raise HTTPException(409, "La VRI ya aprobó esta postulación: para retirarla, habla con la VRI.")
    db.eliminar_postulacion(con, postulacion_id)
    if p["usuario_id"] != u["id"] and p.get("correo"):
        conv = db.obtener_convocatoria(con, p["convocatoria_id"]) or {}
        correo.enviar(p["correo"], "Se canceló tu postulación en FOCU",
                      f"Hola {p['estudiante']},\n\n{u['nombre']} canceló la postulación de tu grupo a "
                      f"«{conv.get('nombre', 'la convocatoria')}».\n\nUniversidad del Cauca · FOCU")
    return {"cancelada": True}


@router.delete("/admin/postulaciones/{postulacion_id}")
def admin_eliminar_postulacion(postulacion_id: int, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """La VRI puede eliminar cualquier postulación (también las aprobadas)."""
    p = db.obtener_postulacion(con, postulacion_id)
    if not p:
        raise HTTPException(404, "Postulación no encontrada.")
    db.eliminar_postulacion(con, postulacion_id)
    if p.get("correo"):
        conv = db.obtener_convocatoria(con, p["convocatoria_id"]) or {}
        correo.enviar(p["correo"], "La VRI eliminó una postulación de tu grupo",
                      f"Hola {p['estudiante']},\n\nLa Vicerrectoría de Investigaciones eliminó la postulación de tu "
                      f"grupo a «{conv.get('nombre', 'la convocatoria')}». Si tienes dudas, comunícate con la VRI."
                      "\n\nUniversidad del Cauca · FOCU")
    return {"eliminada": True}


@router.post("/chat")
def chat(datos: ChatIn, u: dict = Depends(usuario_actual), con=Depends(get_con)):
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(503, "El servidor no tiene configurada la GROQ_API_KEY.")
    conv_id = datos.conversacion_id
    if conv_id is not None and not db.conversacion_es_de(con, u["id"], conv_id):
        raise HTTPException(404, "Esa conversación ya no existe (el historial se borra a los 4 días).")
    s = _sesion(u, con)
    _cambiar_conversacion(s, u, con, conv_id)
    s["ctx"]["con"] = con
    if u["rol"] != "admin":
        s["chat"].mensajes[0]["content"] = sistema_con_datos(
            u["nombre"], u.get("grupo") or "", u.get("facultad") or "", s["perfil"])
    try:
        texto = s["chat"].send_message(datos.mensaje).text
    except Exception as e:
        if es_error_temporal(e):
            raise HTTPException(503, "El servicio de IA está saturado. Intenta de nuevo en unos segundos.")
        raise HTTPException(500, f"Error al consultar el modelo: {e}")
    if u["rol"] != "admin":
        db.guardar_perfil(con, u["id"], s["perfil"])
    if conv_id is None:
        conv_id = db.crear_conversacion(con, u["id"], datos.mensaje)
        s["conv_id"] = conv_id
    db.guardar_mensaje_chat(con, u["id"], "usuario", datos.mensaje, conv_id)
    db.guardar_mensaje_chat(con, u["id"], "asistente", texto, conv_id)
    return {"respuesta": texto, "perfil": s["perfil"], "resultados": s["resultados"], "conversacion_id": conv_id}


@router.get("/chat/conversaciones")
def conversaciones_chat(u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """El historial: conversaciones de esta persona; las de más de 4 días sin uso se borran."""
    return {"conversaciones": db.listar_conversaciones(con, u["id"]), "dias": db.CHAT_DIAS}


@router.get("/chat/conversaciones/{conv_id}")
def conversacion_chat(conv_id: int, u: dict = Depends(usuario_actual), con=Depends(get_con)):
    if not db.conversacion_es_de(con, u["id"], conv_id):
        raise HTTPException(404, "Esa conversación ya no existe.")
    return {"mensajes": db.mensajes_de_conversacion(con, u["id"], conv_id)}


@router.delete("/chat/conversaciones/{conv_id}")
def borrar_conversacion_chat(conv_id: int, u: dict = Depends(usuario_actual), con=Depends(get_con)):
    db.borrar_conversacion(con, u["id"], conv_id)
    s = _sesiones.get(u["id"])
    if s and s.get("conv_id") == conv_id:
        s["conv_id"] = None
        s["chat"] = _nuevo_agente(s, u)
    return {"ok": True}


@router.delete("/chat")
def reiniciar_chat(u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """Borra todo el historial de la persona."""
    _sesiones.pop(u["id"], None)
    db.borrar_chat(con, u["id"])
    return {"ok": True}


MSG_REGISTRO = ("Si tu correo puede registrarse, te enviamos un código para confirmarlo. "
                f"Vale {seguridad.MINUTOS_CODIGO} minutos y sirve una sola vez.")


@router.get("/grupos")
def grupos_publicos(con=Depends(get_con)):
    """Lista pública de grupos para la pantalla de registro (sin correos)."""
    return [{k: g[k] for k in ("id", "nombre", "descripcion", "area", "facultad", "director", "categoria", "linea")}
            for g in db.listar_grupos(con)]


@router.post("/registro/solicitar")
def registro_solicitar(datos: RegistroSolicitarIn, request: Request, con=Depends(get_con)):
    """Paso 1: envía un código al correo para comprobar que es suyo."""
    mail = seguridad.normalizar_correo(datos.correo)
    if not cargas.RE_CORREO.match(mail):
        raise HTTPException(400, "El correo no es válido.")
    if (seguridad.limite_excedido("ip:" + _ip(request), 20, 900)
            or seguridad.limite_excedido("reg:" + mail, 3, 900)):
        raise HTTPException(429, "Hiciste demasiados intentos. Espera unos minutos e inténtalo de nuevo.")
    grupo = db.obtener_grupo(con, datos.grupo_id)
    if not grupo:
        raise HTTPException(404, "Ese grupo no existe.")
    if grupo.get("facultad") and not cargas.mismo_texto(grupo["facultad"], datos.facultad):
        raise HTTPException(400, "Ese grupo no pertenece a la facultad que elegiste.")
    if db.usuario_en_grupo_por_correo(con, mail, grupo["id"]):
        correo.enviar(mail, "Ya tienes cuenta en FOCU",
                      f"Hola {datos.nombre.strip()},\n\nEse correo ya está registrado en FOCU. Para entrar, "
                      "escribe tu correo en la pantalla de ingreso y te enviaremos un código.\n\nUniversidad del Cauca · FOCU")
    elif db.solicitud_en_estado(con, mail, "pendiente"):
        correo.enviar(mail, "Tu solicitud en FOCU está en revisión",
                      f"Hola {datos.nombre.strip()},\n\nTu solicitud para unirte a un grupo ya está en revisión. "
                      "Te avisaremos por correo cuando tengas respuesta.\n\nUniversidad del Cauca · FOCU")
    else:
        codigo = acceso.emitir_codigo_registro(con, datos.nombre, mail, grupo["id"], datos.semillero)
        correo.enviar(mail, "Confirma tu correo en FOCU",
                      f"Hola {datos.nombre.strip()},\n\nTu código para confirmar la solicitud de registro en "
                      f"«{grupo['nombre']}» es: {codigo}\n\nVale {seguridad.MINUTOS_CODIGO} minutos y solo se "
                      "puede usar una vez. Si no lo pediste tú, ignora este mensaje.\n\nUniversidad del Cauca · FOCU")
    return {"mensaje": MSG_REGISTRO}


@router.post("/registro/confirmar")
def registro_confirmar(datos: RegistroConfirmarIn, request: Request, con=Depends(get_con)):
    """Paso 2: con el código correcto, la solicitud llega al director o a la VRI."""
    ip = _ip(request)
    if seguridad.login_bloqueado(ip):
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos.")
    sol = acceso.verificar_codigo_registro(con, datos.correo, datos.codigo)
    if not sol:
        seguridad.registrar_fallo(ip)
        raise HTTPException(401, "Código no válido o vencido. Vuelve a enviar la solicitud para recibir otro.")
    destinos = db.correos_directores_de_grupo(con, sol["grupo_id"])
    a_quien = "el director de tu grupo"
    if not destinos:
        destinos = db.correos_administradores(con)
        a_quien = "la Vicerrectoría de Investigaciones (tu grupo aún no tiene un director con correo registrado)"
    correo.enviar_a_varios(
        destinos, f"Solicitud de registro en FOCU: {sol['nombre']}",
        f"{sol['nombre']} ({sol['correo']}) pidió unirse al grupo «{sol['grupo']}»"
        + (f", semillero {sol['semillero']}" if sol.get("semillero") else "") + ".\n\n"
        "Entra a FOCU, sección de solicitudes, para aprobarla o rechazarla.\n\nUniversidad del Cauca · FOCU")
    return {"mensaje": f"Listo. Enviamos tu solicitud a {a_quien}. Cuando la apruebe te avisaremos por correo "
                       "y podrás ingresar con ese mismo correo.", "grupo": sol["grupo"]}


def _decidir_solicitud(con, sol: dict, datos: DecidirSolicitudIn, quien: dict) -> dict:
    if sol["estado"] != "pendiente":
        raise HTTPException(409, "Esa solicitud ya fue respondida.")
    comentario = (datos.comentario or "").strip() or None
    if datos.aprobar:
        try:
            r = cargas.aprobar_solicitud(con, sol)
        except ValueError as e:
            con.rollback()
            raise HTTPException(409, str(e))
        db.marcar_solicitud(con, sol["id"], "aprobada", comentario, quien["id"])
        con.commit()
        correo.enviar(sol["correo"], "Tu registro en FOCU fue aprobado",
                      f"Hola {sol['nombre']},\n\nTu solicitud para unirte a «{sol['grupo']}» fue aprobada. "
                      "Ya puedes ingresar a FOCU: escribe tu correo y te enviaremos un código."
                      + (f"\n\nComentario: {comentario}" if comentario else "")
                      + "\n\nUniversidad del Cauca · FOCU")
        return {"estado": "aprobada", "ya_existia": r.get("ya_existia", False)}
    db.marcar_solicitud(con, sol["id"], "rechazada", comentario, quien["id"])
    con.commit()
    correo.enviar(sol["correo"], "Tu solicitud en FOCU no fue aprobada",
                  f"Hola {sol['nombre']},\n\nTu solicitud para unirte a «{sol['grupo']}» no fue aprobada."
                  + (f"\n\nComentario: {comentario}" if comentario else "")
                  + "\n\nSi crees que es un error, habla con el director de tu grupo."
                  "\n\nUniversidad del Cauca · FOCU")
    return {"estado": "rechazada"}


@router.get("/grupo/solicitudes")
def grupo_solicitudes(estado: Optional[Literal["pendiente", "aprobada", "rechazada"]] = Query("pendiente"),
                      u: dict = Depends(solo_director_grupo), con=Depends(get_con)):
    return db.listar_solicitudes(con, estado=estado, grupo_id=u["grupo_id"])


@router.post("/grupo/solicitudes/{solicitud_id}/decidir")
def grupo_decidir(solicitud_id: int, datos: DecidirSolicitudIn, u: dict = Depends(solo_director_grupo),
                  con=Depends(get_con)):
    sol = db.obtener_solicitud(con, solicitud_id)
    if not sol or sol["grupo_id"] != u["grupo_id"]:
        raise HTTPException(404, "Solicitud no encontrada.")
    return _decidir_solicitud(con, sol, datos, u)


@router.get("/grupo/postulaciones")
def grupo_postulaciones(etapa: Literal["pendiente", "devuelta", "aprobada"] = Query("pendiente"),
                        u: dict = Depends(solo_director_grupo), con=Depends(get_con)):
    """Postulaciones de las personas de mi grupo (las mías van directo a la VRI)."""
    filas = db.listar_postulaciones(con, grupo_id=u["grupo_id"], etapa_grupo=etapa)
    return _con_nombre_de_convocatoria([f for f in filas if f["usuario_id"] != u["id"]], con)


@router.post("/grupo/postulaciones/{postulacion_id}/decidir")
def grupo_decidir_postulacion(postulacion_id: int, datos: DecidirPostulacionDirectorIn,
                              u: dict = Depends(solo_director_grupo), con=Depends(get_con)):
    """El director aprueba (pasa a la VRI) o la devuelve con una observación obligatoria."""
    p = db.obtener_postulacion(con, postulacion_id)
    if not p or p["grupo_id"] != u["grupo_id"] or p["usuario_id"] == u["id"]:
        raise HTTPException(404, "Postulación no encontrada en tu grupo.")
    if p["etapa_grupo"] != "pendiente":
        raise HTTPException(409, "Esa postulación ya fue respondida.")
    comentario = (datos.comentario or "").strip() or None
    if not datos.aprobar and not comentario:
        raise HTTPException(400, "Escribe una observación para devolverla.")
    con.execute("UPDATE postulaciones SET etapa_grupo = ?, comentario_director = ?, "
                "decidida_director = CURRENT_TIMESTAMP WHERE id = ?",
                ("aprobada" if datos.aprobar else "devuelta", comentario, postulacion_id))
    con.commit()
    conv = db.obtener_convocatoria(con, p["convocatoria_id"]) or {}
    nombre_conv = conv.get("nombre", "la convocatoria")
    if datos.aprobar:
        _avisar_postulacion_a_vri(con, {"nombre": p["estudiante"], "grupo": p["grupo"]}, nombre_conv, p["nota"])
    if p.get("correo"):
        if datos.aprobar:
            correo.enviar(p["correo"], "Tu director aprobó tu postulación",
                          f"Hola {p['estudiante']},\n\nEl director de tu grupo aprobó tu postulación a «{nombre_conv}». "
                          "Ahora la revisa la VRI." + (f"\nComentario: {comentario}" if comentario else ""))
        else:
            correo.enviar(p["correo"], "Tu postulación fue devuelta",
                          f"Hola {p['estudiante']},\n\nEl director de tu grupo devolvió tu postulación a «{nombre_conv}» "
                          f"con esta observación:\n{comentario}\n\nEntra a FOCU, «Postulaciones», para corregirla y reenviarla.")
    return {"id": postulacion_id, "fase": "en_vri" if datos.aprobar else "devuelta"}


@router.get("/grupo/miembros")
def grupo_miembros(u: dict = Depends(solo_director_grupo), con=Depends(get_con)):
    return {"grupo": db.obtener_grupo(con, u["grupo_id"]), "miembros": db.listar_miembros_de_grupo(con, u["grupo_id"])}


@router.post("/grupo/miembros", status_code=201)
def grupo_agregar_miembro(datos: MiembroIn, u: dict = Depends(solo_director_grupo), con=Depends(get_con)):
    """El director agrega a mano a alguien de su grupo (sin esperar una solicitud)."""
    grupo = db.obtener_grupo(con, u["grupo_id"])
    try:
        creado = cargas.agregar_estudiante(con, datos.nombre, datos.correo, datos.semillero, grupo["nombre"],
                                          datos.facultad.strip(),
                                          (datos.descripcion or "").strip() or grupo.get("descripcion") or "")
    except ValueError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    con.commit()
    if datos.correo.strip():
        correo.enviar(datos.correo.strip(), "Te agregaron a FOCU",
                      f"Hola {datos.nombre.strip()},\n\n{u['nombre']} te agregó al grupo «{grupo['nombre']}» en FOCU. "
                      "Para entrar, escribe tu correo en la pantalla de ingreso y te enviaremos un código."
                      "\n\nUniversidad del Cauca · FOCU")
    return creado


@router.patch("/grupo/miembros/{usuario_id}")
def grupo_editar_miembro(usuario_id: int, datos: MiembroEditIn, u: dict = Depends(solo_director_grupo),
                         con=Depends(get_con)):
    """Activa o desactiva el acceso de alguien de su grupo."""
    objetivo = db.usuario_por_id(con, usuario_id)
    if (not objetivo or objetivo["rol"] != "estudiante" or objetivo.get("grupo_id") != u["grupo_id"]
            or objetivo["es_director_grupo"]):
        raise HTTPException(404, "Esa persona no está en tu grupo.")
    con.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (int(datos.activo), usuario_id))
    con.commit()
    return {"id": usuario_id, "activo": datos.activo}


@router.delete("/grupo/miembros/{usuario_id}")
def grupo_eliminar_miembro(usuario_id: int, u: dict = Depends(solo_director_grupo), con=Depends(get_con)):
    """El director elimina a alguien de su grupo."""
    objetivo = db.usuario_por_id(con, usuario_id)
    if (not objetivo or objetivo["rol"] != "estudiante" or objetivo.get("grupo_id") != u["grupo_id"]
            or objetivo["es_director_grupo"]):
        raise HTTPException(404, "Esa persona no está en tu grupo.")
    usos = con.execute("SELECT COUNT(*) AS n FROM postulaciones WHERE usuario_id = ?", (usuario_id,)).fetchone()["n"]
    if usos:
        raise HTTPException(409, f"Esta persona inició {usos} postulación(es) y no se puede eliminar. "
                                 "Desactiva su acceso en su lugar.")
    con.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    con.commit()
    return {"id": usuario_id, "eliminado": True}


@router.get("/admin/grupos")
def admin_grupos(u: dict = Depends(solo_admin), con=Depends(get_con)):
    return db.listar_grupos(con)


@router.post("/admin/grupos", status_code=201)
def admin_crear_grupo(datos: GrupoIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    try:
        gid, nuevo = db.guardar_grupo(con, datos.nombre, datos.descripcion, cargas._area_valida(datos.area),
                                      datos.facultad)
        if datos.director.strip():
            cargas.asignar_director(con, gid, datos.director, datos.correo_director)
    except ValueError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    con.commit()
    return {"id": gid, "nuevo": nuevo}


@router.patch("/admin/grupos/{grupo_id}")
def admin_editar_grupo(grupo_id: int, datos: GrupoEditIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    if not db.obtener_grupo(con, grupo_id):
        raise HTTPException(404, "Grupo no encontrado.")
    cambios = datos.model_dump(exclude_none=True)
    if not cambios:
        raise HTTPException(400, "No enviaste ningún cambio.")
    try:
        if "nombre" in cambios:
            nuevo = cambios["nombre"].strip()
            otro = con.execute("SELECT id FROM grupos WHERE LOWER(nombre) = LOWER(?) AND id <> ?",
                               (nuevo, grupo_id)).fetchone()
            if otro:
                raise ValueError("Ya existe otro grupo con ese nombre.")
            con.execute("UPDATE grupos SET nombre = ? WHERE id = ?", (nuevo, grupo_id))
        for campo in ("descripcion", "area", "facultad"):
            if campo in cambios:
                valor = cambios[campo].strip()
                if campo == "area":
                    valor = cargas._area_valida(valor)
                con.execute(f"UPDATE grupos SET {campo} = ? WHERE id = ?", (valor or None, grupo_id))
        if cambios.get("director", "").strip():
            cargas.asignar_director(con, grupo_id, cambios["director"], cambios.get("correo_director", ""))
        elif "correo_director" in cambios:
            previos = db.directores_de_grupo(con, grupo_id)
            if not previos:
                raise ValueError("Primero indica el nombre del director.")
            cargas.asignar_director(con, grupo_id, previos[0]["nombre"], cambios["correo_director"])
    except ValueError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    con.commit()
    return {"id": grupo_id, "cambios": list(cambios)}


@router.delete("/admin/grupos/{grupo_id}")
def admin_eliminar_grupo(grupo_id: int, u: dict = Depends(solo_admin), con=Depends(get_con)):
    motivo = db.eliminar_grupo(con, grupo_id)
    if motivo:
        raise HTTPException(409, motivo)
    return {"id": grupo_id, "eliminado": True}


@router.get("/admin/solicitudes")
def admin_solicitudes(estado: Optional[Literal["pendiente", "aprobada", "rechazada"]] = Query("pendiente"),
                      u: dict = Depends(solo_admin), con=Depends(get_con)):
    return db.listar_solicitudes(con, estado=estado)


@router.post("/admin/solicitudes/{solicitud_id}/decidir")
def admin_decidir(solicitud_id: int, datos: DecidirSolicitudIn, u: dict = Depends(solo_admin),
                  con=Depends(get_con)):
    sol = db.obtener_solicitud(con, solicitud_id)
    if not sol:
        raise HTTPException(404, "Solicitud no encontrada.")
    return _decidir_solicitud(con, sol, datos, u)


@router.get("/admin/plantilla")
def descargar_plantilla(u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Plantilla de personas con «grupo» como lista desplegable de los grupos que existen."""
    ruta = _BASE / "plantilla_personas.xlsx"
    if not ruta.exists():
        raise HTTPException(404, "No se encontró la plantilla en el servidor.")
    return Response(cargas.plantilla_con_grupos(con, ruta),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_personas.xlsx"',
                             "Cache-Control": "no-store"})


@router.post("/admin/cargar-excel")
def cargar_excel(archivo: UploadFile = File(...), u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Sube el archivo de personas (estudiantes y directores). Los grupos deben existir."""
    if not (archivo.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "El archivo no tiene el formato de la plantilla. Descarga la plantilla y úsala.")
    contenido = archivo.file.read(5 * 1024 * 1024 + 1)
    if len(contenido) > 5 * 1024 * 1024:
        raise HTTPException(413, "El archivo supera los 5 MB.")
    try:
        return cargas.procesar_excel(con, contenido)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/admin/semilleros")
def listar_semilleros(u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Grupos con sus semilleros: sirve para llenar las listas desplegables."""
    return db.listar_semilleros(con)


@router.get("/admin/estudiantes")
def listar_estudiantes(u: dict = Depends(solo_admin), con=Depends(get_con)):
    return db.listar_estudiantes(con)


@router.post("/admin/estudiantes", status_code=201)
def agregar_estudiante(datos: EstudianteIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Agrega un integrante a un grupo existente."""
    try:
        creado = cargas.agregar_estudiante(con, datos.nombre, datos.correo, datos.semillero, datos.grupo,
                                          datos.facultad or "", datos.descripcion or "", crear_grupo=False)
    except ValueError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    con.commit()
    return creado


@router.post("/admin/directores", status_code=201)
def agregar_director(datos: DirectorIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Agrega un director a un grupo existente."""
    try:
        r = cargas.asignar_director(con, datos.grupo_id, datos.nombre, datos.correo, adicional=True,
                                    semillero=datos.semillero or "")
        con.execute("UPDATE grupos SET facultad = ? WHERE id = ?", (datos.facultad.strip(), datos.grupo_id))
    except ValueError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    con.commit()
    return r


@router.delete("/admin/estudiantes/{usuario_id}")
def eliminar_estudiante(usuario_id: int, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Elimina a una persona. Si ya tiene postulaciones se conserva y se sugiere desactivarla."""
    objetivo = db.usuario_por_id(con, usuario_id)
    if not objetivo or objetivo["rol"] != "estudiante":
        raise HTTPException(404, "Estudiante no encontrado.")
    usos = con.execute("SELECT COUNT(*) AS n FROM postulaciones WHERE usuario_id = ?", (usuario_id,)).fetchone()["n"]
    if usos:
        raise HTTPException(409, f"Esta persona inició {usos} postulación(es) y no se puede eliminar. "
                                 "Desactiva su acceso en su lugar.")
    if objetivo["es_director_grupo"]:
        raise HTTPException(409, "Es director/a de su grupo. Cambia el director en la pestaña Grupos "
                                 "o elimina el grupo.")
    con.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    con.commit()
    return {"id": usuario_id, "eliminado": True}


@router.patch("/admin/estudiantes/{usuario_id}")
def editar_estudiante(usuario_id: int, datos: EstudianteEditIn, u: dict = Depends(solo_admin),
                      con=Depends(get_con)):
    """Edita datos, semillero o acceso de un estudiante."""
    objetivo = db.usuario_por_id(con, usuario_id)
    if not objetivo or objetivo["rol"] != "estudiante":
        raise HTTPException(404, "Estudiante no encontrado.")
    cambios = datos.model_dump(exclude_none=True)
    if not cambios:
        raise HTTPException(400, "No enviaste ningún cambio.")

    if "activo" in cambios:
        con.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (int(cambios["activo"]), usuario_id))
    if "nombre" in cambios:
        con.execute("UPDATE usuarios SET nombre = ? WHERE id = ?", (cambios["nombre"].strip(), usuario_id))
    if "correo" in cambios:
        nuevo = cambios["correo"].strip()
        if nuevo:
            if not cargas.RE_CORREO.match(nuevo):
                raise HTTPException(400, "El correo no es válido.")
            otro = db.usuario_en_grupo_por_correo(con, nuevo, objetivo.get("grupo_id") if objetivo["rol"] != "admin" else None)
            if otro and otro["id"] != usuario_id:
                raise HTTPException(409, "Ese correo ya pertenece a otra persona de este grupo.")
        con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", (nuevo or None, usuario_id))
    if "facultad" in cambios:
        con.execute("UPDATE usuarios SET facultad = ? WHERE id = ?", (cambios["facultad"].strip(), usuario_id))
    if "es_director" in cambios:
        con.execute("UPDATE usuarios SET es_director_grupo = ? WHERE id = ?",
                    (int(cambios["es_director"]), usuario_id))
    if "semillero" in cambios or "grupo" in cambios:
        semillero = (cambios.get("semillero") or objetivo["semillero"] or "").strip()
        grupo = (cambios.get("grupo") or objetivo["grupo"] or "").strip()
        if not semillero or not grupo:
            raise HTTPException(400, "Para cambiar de semillero indica el semillero y el grupo.")
        g = cargas.grupo_existente(con, grupo)
        if not g:
            raise HTTPException(400, f"El grupo «{grupo}» no existe. Elígelo de la lista.")
        grupo_id = g["id"]
        semillero_id = db.obtener_o_crear_semillero(con, semillero, grupo_id)
        con.execute("UPDATE usuarios SET semillero_id = ? WHERE id = ?", (semillero_id, usuario_id))
    con.commit()
    return {"id": usuario_id, "cambios": list(cambios)}


@router.post("/admin/administradores", status_code=201)
def agregar_administrador(datos: AdminIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Agrega a otra persona de la VRI como directora. Entra con su correo."""
    try:
        creado = cargas.agregar_administrador(con, datos.nombre, datos.correo)
    except ValueError as e:
        con.rollback()
        raise HTTPException(500, str(e))
    con.commit()
    return creado


@router.get("/admin/postulaciones")
def listar_postulaciones(estado: Optional[Literal["solicitada", "aprobada", "rechazada"]] = Query(None),
                         u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Postulaciones que ya aprobó el director del grupo (las demás aún no llegan a la VRI)."""
    return _con_nombre_de_convocatoria(
        db.listar_postulaciones(con, estado=estado, etapa_grupo="aprobada"), con)


@router.patch("/admin/postulaciones/{postulacion_id}")
def decidir_postulacion(postulacion_id: int, datos: DecisionIn, u: dict = Depends(solo_admin),
                        con=Depends(get_con)):
    """La VRI aprueba o rechaza (con comentario opcional). Solo si el director ya la aprobó."""
    previa = db.obtener_postulacion(con, postulacion_id)
    if previa and previa["etapa_grupo"] != "aprobada":
        raise HTTPException(409, "El director del grupo aún no aprueba esta postulación.")
    if previa and datos.estado == "aprobada":
        estados = {c: v["estado"] for c, v in db.obtener_requisitos(con, postulacion_id).items()}
        falta = requisitos.avales_faltantes(estados, requisitos.lista_para(db.obtener_convocatoria(con, previa["convocatoria_id"])))
        if falta:
            raise HTTPException(409, "No se puede dar el visto bueno: faltan avales en «Sí» (" + "; ".join(falta) + ").")
    cur = con.execute(
        "UPDATE postulaciones SET estado = ?, comentario_vri = ?, resuelta = CURRENT_TIMESTAMP "
        "WHERE id = ?", (datos.estado, datos.comentario, postulacion_id))
    con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Postulación no encontrada.")
    p = con.execute("SELECT usuario_id, convocatoria_id FROM postulaciones WHERE id = ?",
                    (postulacion_id,)).fetchone()
    est = db.usuario_por_id(con, p["usuario_id"]) if p else None
    if est and est.get("correo"):
        conv = db.obtener_convocatoria(con, p["convocatoria_id"]) or {}
        veredicto = "recibió el visto bueno de la VRI" if datos.estado == "aprobada" else "no fue aprobada por la VRI"
        correo.enviar(est["correo"], f"Tu postulación {datos.estado}",
                      f"Hola {est['nombre']},\n\nTu solicitud para «{conv.get('nombre', 'la convocatoria')}» "
                      f"{veredicto}.\n" + (f"Comentario: {datos.comentario}\n" if datos.comentario else ""))
    return {"id": postulacion_id, "estado": datos.estado}


@router.get("/admin/convocatorias")
def admin_convocatorias(u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Todas las convocatorias, tras limpiar las vencidas y las que llevan meses sin revisar."""
    db.mantenimiento_convocatorias(con)
    return db.listar_convocatorias(con, solo_activas=False)


@router.post("/admin/convocatorias", status_code=201)
def crear_convocatoria(datos: ConvocatoriaIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Agrega una convocatoria a mano (queda verificada). Es el respaldo si un scraper falla."""
    try:
        conv = normalizar_convocatoria(datos.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.guardar_convocatoria(con, conv, fuente="manual", verificada=True)
    con.commit()
    return {"id": conv["id"]}


@router.post("/admin/convocatorias/desde-url", status_code=201)
def crear_convocatoria_desde_url(datos: ConvocatoriaUrlIn, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Agrega una convocatoria desde su dirección y la deja sin verificar."""
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(503, "El servidor no tiene configurada la GROQ_API_KEY (la IA lee las páginas).")
    try:
        conv = recolector.agregar_por_url(con, datos.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"No se pudo leer la página: {e}")
    cierre = str(conv.get("fecha_limite") or "")[:10]
    return {"id": conv["id"], "nombre": conv["nombre"], "fecha_limite": conv.get("fecha_limite"),
            "vencida": bool(cierre) and cierre < date.today().isoformat()}


@router.patch("/admin/convocatorias/{cid}")
def estado_convocatoria(cid: str, datos: ConvocatoriaEstadoIn, u: dict = Depends(solo_admin),
                        con=Depends(get_con)):
    """Oculta o muestra una convocatoria, o la marca como verificada."""
    if datos.recuperar:
        if not db.recuperar_convocatoria(con, cid):
            raise HTTPException(404, "Esa convocatoria no existe.")
    if datos.activa is None and datos.verificada is None and datos.fecha_limite is None:
        if datos.recuperar:
            return {"id": cid}
        raise HTTPException(400, "No enviaste ningún cambio.")
    if datos.fecha_limite is not None:
        if datos.fecha_limite and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datos.fecha_limite):
            raise HTTPException(400, "La fecha debe ser AAAA-MM-DD.")
        try:
            if datos.fecha_limite:
                date.fromisoformat(datos.fecha_limite)
        except ValueError:
            raise HTTPException(400, "Esa fecha no existe.")
        if not db.poner_fecha_convocatoria(con, cid, datos.fecha_limite or None):
            raise HTTPException(404, "Esa convocatoria no existe.")
    if datos.activa is not None or datos.verificada is not None:
        if not db.cambiar_estado_convocatoria(con, cid, datos.activa, datos.verificada):
            raise HTTPException(404, "Esa convocatoria no existe.")
    return {"id": cid}


@router.delete("/admin/convocatorias/{cid}")
def eliminar_convocatoria(cid: str, u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Elimina una convocatoria (si no tiene postulaciones). El recolector no la vuelve a traer."""
    motivo = db.eliminar_convocatoria(con, cid)
    if motivo:
        raise HTTPException(409, motivo)
    return {"id": cid, "eliminada": True}


@router.post("/admin/scrapers/ejecutar", status_code=202)
def ejecutar_scrapers(u: dict = Depends(solo_admin)):
    """Lanza la recolección de convocatorias desde las fuentes oficiales (tarda unos minutos)."""
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(503, "El servidor no tiene configurada la GROQ_API_KEY (la IA lee las páginas).")
    if not iniciar_scrapers():
        raise HTTPException(409, "Ya hay una recolección en curso.")
    return {"iniciada": True}


@router.get("/admin/scrapers/estado")
def estado_scrapers(u: dict = Depends(solo_admin)):
    fuentes = [{k: f.get(k) for k in ("id", "nombre", "url_listado", "urls_directas", "activa", "nota")}
               for f in recolector.cargar_fuentes()]
    return {**_scraper, "fuentes": fuentes}


app.include_router(router)

app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/", include_in_schema=False)
def inicio():
    return FileResponse(WEB / "index.html")