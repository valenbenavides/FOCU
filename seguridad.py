"""Códigos de acceso temporales, tokens y límites de intentos.

- Para ingresar, la persona escribe su correo (debe estar cargado en la base, dentro de un
  grupo de investigación) y recibe por correo un código de 6 dígitos que dura 15 minutos y
  sirve una sola vez. En la base solo se guarda su HMAC-SHA256 (con SECRET_KEY y el correo
  como sal): si alguien roba la base, no ve ningún código.
- Al ingresar, el backend entrega un token JWT que el frontend manda en cada petición.
- IMPORTANTE: si cambias SECRET_KEY, los tokens y códigos pendientes dejan de servir."""
import hashlib
import hmac
import os
import secrets
import time

import jwt  # pip install pyjwt

# Sin 0/O, 1/I/L para evitar confusiones al dictar o copiar un código.
ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
HORAS_TOKEN = 8
MINUTOS_CODIGO = 15
MAX_INTENTOS_CODIGO = 5


def _secreto() -> bytes:
    s = os.environ.get("SECRET_KEY", "")
    if len(s) < 32:
        raise RuntimeError(
            "Falta SECRET_KEY (mínimo 32 caracteres) en el archivo .env o en las variables del servidor. "
            'Genera una con: python -c "import secrets; print(secrets.token_hex(32))"')
    return s.encode()


def generar_codigo() -> str:
    raw = "".join(secrets.choice(ALFABETO) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def generar_codigo_temporal() -> str:
    """Código de 6 dígitos para enviar por correo."""
    return f"{secrets.randbelow(10 ** 6):06d}"


def normalizar_correo(correo: str) -> str:
    return (correo or "").strip().lower()


def normalizar_codigo(codigo: str) -> str:
    """Acepta minúsculas, espacios o sin guion: 'k7f2 9qxm' == 'K7F2-9QXM'."""
    return "".join(ch for ch in codigo.upper() if ch.isalnum())


def hash_codigo(codigo: str, sal: str = "") -> str:
    """`sal` = correo de la persona: así dos personas con el mismo código de 6 dígitos
    no producen el mismo hash."""
    msg = (normalizar_correo(sal) + "|" + normalizar_codigo(codigo)).encode()
    return hmac.new(_secreto(), msg, hashlib.sha256).hexdigest()


def crear_token(usuario_id: int) -> str:
    payload = {"sub": str(usuario_id), "exp": int(time.time()) + HORAS_TOKEN * 3600}
    return jwt.encode(payload, _secreto(), algorithm="HS256")


def leer_token(token: str) -> int | None:
    """Devuelve el id de usuario, o None si el token es inválido o venció."""
    try:
        return int(jwt.decode(token, _secreto(), algorithms=["HS256"])["sub"])
    except Exception:
        return None


# --- Límite de solicitudes de código (en memoria; por correo y por IP) -----
_solicitudes: dict[str, list[float]] = {}


def limite_excedido(clave: str, maximo: int, ventana_s: int) -> bool:
    """Cuenta el intento y dice si ya se pasó del máximo dentro de la ventana."""
    ahora = time.time()
    recientes = [t for t in _solicitudes.get(clave, []) if ahora - t < ventana_s]
    excedido = len(recientes) >= maximo
    if not excedido:
        recientes.append(ahora)
    _solicitudes[clave] = recientes
    return excedido


# --- Límite de intentos fallidos de login (en memoria, por IP) --------------
VENTANA_S, MAX_FALLOS = 300, 10
_fallos: dict[str, list[float]] = {}


def login_bloqueado(ip: str) -> bool:
    ahora = time.time()
    recientes = [t for t in _fallos.get(ip, []) if ahora - t < VENTANA_S]
    _fallos[ip] = recientes
    return len(recientes) >= MAX_FALLOS


def registrar_fallo(ip: str) -> None:
    _fallos.setdefault(ip, []).append(time.time())
