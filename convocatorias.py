"""Normalización de convocatorias: una sola función que usan la carga manual, el
recolector (scrapers) y la siembra inicial, para que todas tengan el mismo formato."""
import hashlib
from datetime import date

from schema import ALCANCES, AREAS, FASES, TIPOS_BENEFICIARIO

CAMPOS_TEXTO = ("entidad", "url", "objetivo", "requisitos", "beneficios")


def _a_entero(v) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if v >= 0 else None
    digitos = "".join(ch for ch in str(v) if ch.isdigit())
    return int(digitos) if digitos else None


def _lista(valor, validos: list[str], por_defecto: list[str]) -> list[str]:
    if valor is None:
        valor = []
    elif not isinstance(valor, (list, tuple, set)):
        valor = [valor]
    limpia = [v for v in dict.fromkeys(valor) if v in validos]
    # Si no se pudo determinar, se acepta cualquier valor: es mejor mostrar de más
    # (marcada como "sin verificar") que ocultarle una convocatoria a alguien.
    return limpia or list(por_defecto)


def _fecha(v) -> str | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        return None


def normalizar_convocatoria(raw: dict, id_defecto: str | None = None) -> dict:
    """Devuelve la convocatoria con todos los campos y valores válidos.
    Lanza ValueError si no tiene nombre."""
    nombre = str(raw.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("La convocatoria necesita un nombre.")
    cid = str(raw.get("id") or id_defecto or "").strip()
    if not cid:
        cid = "m-" + hashlib.sha1((nombre + str(raw.get("url") or "")).encode()).hexdigest()[:10]

    conv = {"id": cid, "nombre": nombre[:300], "fecha_limite": _fecha(raw.get("fecha_limite"))}
    for campo in CAMPOS_TEXTO:
        conv[campo] = str(raw.get(campo) or "").strip()
    conv["recursos_totales"] = _a_entero(raw.get("recursos_totales"))
    conv["monto_min"] = _a_entero(raw.get("monto_min"))
    conv["monto_max"] = _a_entero(raw.get("monto_max"))
    if conv["monto_min"] and conv["monto_max"] and conv["monto_min"] > conv["monto_max"]:
        conv["monto_min"], conv["monto_max"] = conv["monto_max"], conv["monto_min"]
    conv["tipos_beneficiario"] = _lista(raw.get("tipos_beneficiario"), TIPOS_BENEFICIARIO, TIPOS_BENEFICIARIO)
    conv["areas"] = _lista(raw.get("areas"), AREAS, ["multidisciplinar"])
    conv["fases"] = _lista(raw.get("fases"), FASES, FASES)
    conv["alcances"] = _lista(raw.get("alcances"), ALCANCES, ALCANCES)
    return conv