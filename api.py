"""FOCU: servidor web (FastAPI). Sirve la API en /api y la página (carpeta web/) en /.

Ejecutar en tu computador:   uvicorn api:app --reload --port 8000
Página:                      http://localhost:8000
Documentación de la API:     http://localhost:8000/docs   (permite probar cada ruta)"""
from pathlib import Path

from dotenv import load_dotenv
_BASE = Path(__file__).parent
load_dotenv(_BASE / ".env")   # debe ir antes de importar db, que lee DATABASE_URL

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal, Optional, Union

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import acceso
import cargas
import correo
import db
import seguridad
from agente import crear_chat
from convocatorias import normalizar_convocatoria
from llm import es_error_temporal
from matching import RUTA_JSON, RUTA_JSON_EJEMPLO, cargar_convocatorias, comparar, rankear
from schema import CATEGORIAS, ETIQUETAS
from scrapers import recolector

WEB = _BASE / "web"

# ---------------------------------------------------------------------------
# Recolección de convocatorias en segundo plano
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Arranque: tablas, convocatorias iniciales y primer administrador
# ---------------------------------------------------------------------------
def _sembrar_convocatorias(con):
    """Si no hay convocatorias, carga las del JSON (el tuyo o el de ejemplo)."""
    if db.contar_convocatorias(con) > 0:
        return
    ruta = RUTA_JSON if RUTA_JSON.exists() else RUTA_JSON_EJEMPLO
    for raw in json.loads(ruta.read_text(encoding="utf-8")):
        db.guardar_convocatoria(con, normalizar_convocatoria(raw), fuente="manual", verificada=True)
    con.commit()
    print(f"[arranque] Convocatorias iniciales cargadas desde {ruta.name}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seguridad._secreto()  # falla pronto y con un mensaje claro si falta SECRET_KEY
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


# ---------------------------------------------------------------------------
# Dependencias: conexión, usuario actual y roles
# ---------------------------------------------------------------------------
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
    # El rol y el estado se leen de la base (no del token): si la VRI desactiva
    # a un estudiante, pierde el acceso de inmediato.
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


def _ip(request: Request) -> str:
    # Detrás de un servicio como Render, la IP real llega en X-Forwarded-For.
    adelante = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return adelante or (request.client.host if request.client else "desconocida")


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
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
    nota: Optional[str] = Field(default=None, max_length=1000)


class DecisionIn(BaseModel):
    estado: Literal["aprobada", "rechazada"]
    comentario: Optional[str] = Field(default=None, max_length=1000)


class EstudianteIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(default="", max_length=120)
    semillero: str = Field(default="", max_length=120)
    grupo: str = Field(min_length=2, max_length=120)
    facultad: Optional[str] = Field(default=None, max_length=120)
    descripcion: Optional[str] = Field(default=None, max_length=2000)


class EstudianteEditIn(BaseModel):
    """Todos los campos son opcionales: solo se cambia lo que se envíe."""
    activo: Optional[bool] = None
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=120)
    correo: Optional[str] = Field(default=None, max_length=120)
    semillero: Optional[str] = Field(default=None, min_length=2, max_length=120)
    grupo: Optional[str] = Field(default=None, min_length=2, max_length=120)


class AdminIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    correo: str = Field(min_length=5, max_length=120)


class ChatIn(BaseModel):
    mensaje: str = Field(min_length=1, max_length=2000)


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


class ConvocatoriaEstadoIn(BaseModel):
    activa: Optional[bool] = None
    verificada: Optional[bool] = None


# ---------------------------------------------------------------------------
# Sesiones del agente (en memoria): perfil + ranking + chat de cada estudiante.
# Se pierden si el servidor se reinicia (el perfil sí queda guardado en la base).
# ---------------------------------------------------------------------------
_sesiones: dict[int, dict] = {}


def _sesion(u: dict) -> dict:
    s = _sesiones.get(u["id"])
    if s is None:
        s = {"perfil": dict(u["perfil"]), "resultados": []}
        s["chat"] = crear_chat(s["perfil"], s["resultados"])
        _sesiones[u["id"]] = s
    return s


# ---------------------------------------------------------------------------
# Públicas
# ---------------------------------------------------------------------------
@router.get("/salud")
def salud():
    return {"ok": True}


@router.get("/meta")
def meta():
    """Categorías y etiquetas para armar los formularios del frontend."""
    return {"categorias": CATEGORIAS, "etiquetas": ETIQUETAS}


@router.get("/aviso")
def aviso():
    ruta = _BASE / "datos" / "aviso_datos.txt"
    texto = ruta.read_text(encoding="utf-8") if ruta.exists() else "Aviso de datos personales pendiente de redactar."
    return {"texto": texto}


MSG_CODIGO = ("Si tu correo está registrado en un grupo de investigación, te enviamos un código "
              f"de acceso. Vale {seguridad.MINUTOS_CODIGO} minutos y sirve una sola vez.")


@router.post("/login/solicitar")
def solicitar_codigo(datos: SolicitarCodigoIn, request: Request, con=Depends(get_con)):
    """Envía al correo un código temporal. La respuesta es siempre la misma, esté o no
    registrado el correo, para no revelar quién pertenece a la base."""
    mail = seguridad.normalizar_correo(datos.correo)
    if (seguridad.limite_excedido("ip:" + _ip(request), 20, 900)
            or seguridad.limite_excedido("mail:" + mail, 3, 900)):
        raise HTTPException(429, "Pediste demasiados códigos. Espera unos minutos e inténtalo de nuevo.")
    emitido = acceso.emitir_codigo(con, mail)
    if emitido:
        u, codigo = emitido
        correo.enviar(
            u["correo"], "Tu código de acceso a FOCU",
            f"Hola {u['nombre']},\n\nTu código de acceso a FOCU es: {codigo}\n\n"
            f"Vale {seguridad.MINUTOS_CODIGO} minutos y solo se puede usar una vez. "
            "Si no lo pediste tú, ignora este mensaje.\n\nUniversidad del Cauca · FOCU")
    return {"mensaje": MSG_CODIGO}


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


@router.get("/yo")
def yo(u: dict = Depends(usuario_actual)):
    """Datos del usuario con sesión iniciada (el frontend lo usa para saber qué pantalla mostrar)."""
    return {k: u[k] for k in ("id", "nombre", "correo", "rol", "semillero", "grupo",
                              "consentimiento", "perfil")}


# ---------------------------------------------------------------------------
# Estudiante
# ---------------------------------------------------------------------------
@router.post("/consentimiento")
def aceptar_consentimiento(u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """El estudiante acepta el tratamiento de datos personales (Ley 1581 de 2012)."""
    con.execute("UPDATE usuarios SET consentimiento = 1 WHERE id = ?", (u["id"],))
    con.commit()
    return {"consentimiento": True}


@router.put("/perfil")
def guardar_perfil(datos: PerfilIn, u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """Reemplaza el perfil completo. Los campos que no se envíen quedan vacíos."""
    perfil = datos.model_dump(exclude_none=True)
    db.guardar_perfil(con, u["id"], perfil)
    s = _sesiones.get(u["id"])
    if s:  # mantiene sincronizado al agente con el perfil nuevo
        s["perfil"].clear()
        s["perfil"].update(perfil)
        s["resultados"].clear()
    return {"perfil": perfil}


@router.get("/convocatorias")
def listar_convocatorias(u: dict = Depends(usuario_actual), con=Depends(get_con)):
    """Catálogo de convocatorias activas, sin ranking. Los estudiantes solo ven las que la
    VRI ya revisó y dio su visto bueno."""
    convs = db.listar_convocatorias(con, solo_activas=True)
    return convs if u["rol"] == "admin" else [c for c in convs if c.get("verificada")]


@router.post("/convocatorias/buscar")
def buscar_convocatorias(u: dict = Depends(solo_estudiante)):
    """Ranking con % de compatibilidad según el perfil guardado del estudiante."""
    if not u["perfil"]:
        raise HTTPException(400, "Primero completa tu perfil.")
    return rankear(u["perfil"], cargar_convocatorias())


@router.post("/postulaciones", status_code=201)
def crear_postulacion(datos: PostulacionIn, u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    """El estudiante registra a qué convocatoria quiere postularse; queda 'solicitada'
    hasta que la VRI la apruebe o rechace."""
    if not u["consentimiento"]:
        raise HTTPException(403, "Debes aceptar el tratamiento de datos antes de postularte.")
    cid = str(datos.convocatoria_id)
    conv = db.obtener_convocatoria(con, cid)
    if not conv or not conv.get("activa", True) or not conv.get("verificada"):
        raise HTTPException(404, "Esa convocatoria no existe o aún no tiene el visto bueno de la VRI.")
    try:
        nuevo_id = con.insertar(
            "INSERT INTO postulaciones (usuario_id, convocatoria_id, nota) VALUES (?, ?, ?)",
            (u["id"], cid, datos.nota))
        con.commit()
    except db.ErrorIntegridad:
        raise HTTPException(409, "Ya registraste esta convocatoria.")
    correo.enviar_a_varios(
        db.correos_administradores(con), f"Nueva solicitud de postulación: {conv['nombre']}",
        f"{u['nombre']} ({u['semillero'] or 'sin semillero'}, {u['grupo'] or 'sin grupo'}) quiere "
        f"postularse a «{conv['nombre']}».\n"
        + (f"Nota del estudiante: {datos.nota}\n" if datos.nota else "")
        + "\nEntra a FOCU, sección Postulaciones, para darle el visto bueno o rechazarla.")
    return {"id": nuevo_id, "estado": "solicitada"}


def _con_nombre_de_convocatoria(filas: list[dict], con) -> list[dict]:
    convs = {c["id"]: c for c in db.listar_convocatorias(con, solo_activas=False)}
    for f in filas:
        c = convs.get(f["convocatoria_id"], {})
        f["convocatoria"] = c.get("nombre", "(convocatoria retirada)")
        f["entidad"] = c.get("entidad", "")
        f["fecha_limite"] = c.get("fecha_limite")
    return filas


@router.get("/postulaciones")
def mis_postulaciones(u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    return _con_nombre_de_convocatoria(db.listar_postulaciones(con, usuario_id=u["id"]), con)


@router.post("/chat")
def chat(datos: ChatIn, u: dict = Depends(solo_estudiante), con=Depends(get_con)):
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(503, "El servidor no tiene configurada la GROQ_API_KEY.")
    s = _sesion(u)
    try:
        texto = s["chat"].send_message(datos.mensaje).text
    except Exception as e:
        if es_error_temporal(e):
            raise HTTPException(503, "El servicio de IA está saturado. Intenta de nuevo en unos segundos.")
        raise HTTPException(500, f"Error al consultar el modelo: {e}")
    db.guardar_perfil(con, u["id"], s["perfil"])  # el agente puede haber actualizado el perfil
    return {"respuesta": texto, "perfil": s["perfil"], "resultados": s["resultados"]}


@router.delete("/chat")
def reiniciar_chat(u: dict = Depends(solo_estudiante)):
    _sesiones.pop(u["id"], None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Administrador (VRI): personas
# ---------------------------------------------------------------------------
@router.get("/admin/plantilla")
def descargar_plantilla(u: dict = Depends(solo_admin)):
    ruta = _BASE / "plantilla_semilleros.xlsx"
    if not ruta.exists():
        raise HTTPException(404, "No se encontró plantilla_semilleros.xlsx en el servidor.")
    return FileResponse(ruta, filename="plantilla_semilleros.xlsx")


@router.post("/admin/cargar-excel")
def cargar_excel(archivo: UploadFile = File(...), u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Sube el Excel de grupos, semilleros y estudiantes. Devuelve el código de cada
    estudiante NUEVO: se muestra una sola vez, hay que entregarlo o descargarlo ya."""
    if not (archivo.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "El archivo debe ser .xlsx")
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
    """Agrega UN integrante a mano (si el grupo o el semillero no existen, los crea).
    Podrá ingresar con su correo."""
    try:
        creado = cargas.agregar_estudiante(con, datos.nombre, datos.correo, datos.semillero, datos.grupo,
                                          datos.facultad or "", datos.descripcion or "")
    except ValueError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    con.commit()
    return creado


@router.patch("/admin/estudiantes/{usuario_id}")
def editar_estudiante(usuario_id: int, datos: EstudianteEditIn, u: dict = Depends(solo_admin),
                      con=Depends(get_con)):
    """Corrige los datos de un estudiante, lo cambia de semillero o activa/desactiva su acceso.
    Solo se modifica lo que se envíe."""
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
        con.execute("UPDATE usuarios SET correo = ? WHERE id = ?",
                    (cambios["correo"].strip() or None, usuario_id))
    if "semillero" in cambios or "grupo" in cambios:
        semillero = (cambios.get("semillero") or objetivo["semillero"] or "").strip()
        grupo = (cambios.get("grupo") or objetivo["grupo"] or "").strip()
        if not semillero or not grupo:
            raise HTTPException(400, "Para cambiar de semillero indica el semillero y el grupo.")
        grupo_id = db.obtener_o_crear_grupo(con, grupo)
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


# ---------------------------------------------------------------------------
# Administrador (VRI): postulaciones
# ---------------------------------------------------------------------------
@router.get("/admin/postulaciones")
def listar_postulaciones(estado: Optional[Literal["solicitada", "aprobada", "rechazada"]] = Query(None),
                         u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Todas las intenciones de postulación, con estudiante, semillero, grupo y convocatoria."""
    return _con_nombre_de_convocatoria(db.listar_postulaciones(con, estado=estado), con)


@router.patch("/admin/postulaciones/{postulacion_id}")
def decidir_postulacion(postulacion_id: int, datos: DecisionIn, u: dict = Depends(solo_admin),
                        con=Depends(get_con)):
    """La VRI aprueba o rechaza (con comentario opcional)."""
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


# ---------------------------------------------------------------------------
# Administrador (VRI): convocatorias y recolección automática
# ---------------------------------------------------------------------------
@router.get("/admin/convocatorias")
def admin_convocatorias(u: dict = Depends(solo_admin), con=Depends(get_con)):
    """Todas las convocatorias (también las ocultas), con su fuente y si están verificadas."""
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


@router.patch("/admin/convocatorias/{cid}")
def estado_convocatoria(cid: str, datos: ConvocatoriaEstadoIn, u: dict = Depends(solo_admin),
                        con=Depends(get_con)):
    """Oculta o muestra una convocatoria, o la marca como verificada."""
    if datos.activa is None and datos.verificada is None:
        raise HTTPException(400, "No enviaste ningún cambio.")
    if not db.cambiar_estado_convocatoria(con, cid, datos.activa, datos.verificada):
        raise HTTPException(404, "Esa convocatoria no existe.")
    return {"id": cid}


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
    fuentes = [{k: f.get(k) for k in ("id", "nombre", "url_listado", "activa", "nota")}
               for f in recolector.cargar_fuentes()]
    return {**_scraper, "fuentes": fuentes}


app.include_router(router)

# ---------------------------------------------------------------------------
# Página web (archivos estáticos)
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/", include_in_schema=False)
def inicio():
    return FileResponse(WEB / "index.html")
