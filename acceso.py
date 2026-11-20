"""Ingreso por correo y código temporal, y verificación de solicitudes de registro."""
import hmac
import time

import cargas
import db
import seguridad


def emitir_codigo(con, correo: str) -> tuple[dict, str] | None:
    """Crea un código temporal para un correo que puede ingresar; devuelve (usuario, código) o None."""
    mail = seguridad.normalizar_correo(correo)
    u = db.usuario_por_correo(con, mail)
    if not db.puede_ingresar(u):
        return None
    codigo = seguridad.generar_codigo_temporal()
    db.guardar_codigo_temporal(con, u["id"], seguridad.hash_codigo(codigo, mail),
                               int(time.time()) + seguridad.MINUTOS_CODIGO * 60)
    return u, codigo


def estado_de_correo(con, correo: str) -> str:
    """Estado de un correo: ok, desactivado, sin_grupo, pendiente, rechazada o no_registrado."""
    mail = seguridad.normalizar_correo(correo)
    u = db.usuario_por_correo(con, mail)
    if u:
        if db.puede_ingresar(u):
            return "ok"
        return "desactivado" if not u["activo"] else "sin_grupo"
    if db.solicitud_en_estado(con, mail, "pendiente"):
        return "pendiente"
    if db.solicitud_en_estado(con, mail, "rechazada"):
        return "rechazada"
    return "no_registrado"


def verificar_codigo(con, correo: str, codigo: str) -> tuple[dict | None, bool]:
    """Devuelve (usuario si el código es correcto, si hubo intento contra un usuario real). Un solo uso."""
    mail = seguridad.normalizar_correo(correo)
    u = db.usuario_por_correo(con, mail)
    info = db.datos_codigo(con, u["id"]) if u else None
    vigente = bool(
        u and u["activo"] and info and info["codigo_expira"]
        and info["codigo_expira"] >= time.time()
        and info["codigo_intentos"] < seguridad.MAX_INTENTOS_CODIGO)
    if vigente and hmac.compare_digest(info["codigo_hash"], seguridad.hash_codigo(codigo, mail)):
        db.guardar_codigo_temporal(con, u["id"], cargas._placeholder(), 0)
        return u, True
    if vigente:
        db.sumar_intento_codigo(con, u["id"])
    return None, vigente


def emitir_codigo_registro(con, nombre: str, correo: str, grupo_id: int, semillero: str) -> str:
    """Guarda una solicitud sin verificar y devuelve el código para enviarlo al correo."""
    mail = seguridad.normalizar_correo(correo)
    codigo = seguridad.generar_codigo_temporal()
    db.crear_solicitud_sin_verificar(
        con, nombre.strip(), mail, grupo_id, (semillero or "").strip(),
        seguridad.hash_codigo(codigo, mail), int(time.time()) + seguridad.MINUTOS_CODIGO * 60)
    return codigo


def verificar_codigo_registro(con, correo: str, codigo: str) -> dict | None:
    """Si el código es correcto, la solicitud pasa a pendiente y se devuelve. Un solo uso."""
    mail = seguridad.normalizar_correo(correo)
    sol = db.solicitud_en_estado(con, mail, "sin_verificar")
    info = db.datos_codigo_solicitud(con, sol["id"]) if sol else None
    vigente = bool(info and info["codigo_hash"] and info["codigo_expira"] and info["codigo_expira"] >= time.time()
                   and info["codigo_intentos"] < seguridad.MAX_INTENTOS_CODIGO)
    if vigente and hmac.compare_digest(info["codigo_hash"], seguridad.hash_codigo(codigo, mail)):
        db.marcar_solicitud(con, sol["id"], "pendiente")
        con.commit()
        sol["estado"] = "pendiente"
        return sol
    if vigente:
        db.sumar_intento_solicitud(con, sol["id"])
    return None