"""Lógica del ingreso por correo + código temporal (sin FastAPI, para poder probarla)."""
import hmac
import time

import cargas
import db
import seguridad


def emitir_codigo(con, correo: str) -> tuple[dict, str] | None:
    """Si el correo pertenece a un director o a un integrante de un grupo, crea un código
    temporal y devuelve (usuario, código en claro) para enviarlo por correo. Si no, None."""
    mail = seguridad.normalizar_correo(correo)
    u = db.usuario_por_correo(con, mail)
    if not u or not u["activo"] or not (u["rol"] == "admin" or u.get("semillero_id")):
        return None
    codigo = seguridad.generar_codigo_temporal()
    db.guardar_codigo_temporal(con, u["id"], seguridad.hash_codigo(codigo, mail),
                               int(time.time()) + seguridad.MINUTOS_CODIGO * 60)
    return u, codigo


def verificar_codigo(con, correo: str, codigo: str) -> tuple[dict | None, bool]:
    """Devuelve (usuario si el código es correcto, hubo_intento_contra_usuario_real).
    El código es de un solo uso: al aceptarlo se invalida."""
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
