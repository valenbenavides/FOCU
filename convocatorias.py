"""Normaliza las convocatorias al mismo formato, vengan de carga manual, recolector o archivo."""
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
    return limpia or list(por_defecto)


def _fecha(v) -> str | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        return None


def _texto_corto(v, limite: int) -> str:
    return str(v or "").strip()[:limite]


def _lista_textos(v, max_items: int, limite: int) -> list[str]:
    if not isinstance(v, (list, tuple)):
        return []
    return [t for t in (_texto_corto(x, limite) for x in v) if t][:max_items]


def _url_http(v) -> str:
    u = str(v or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


def _detalle(raw: dict) -> dict:
    """Campos de detalle opcionales: cronograma, documentos y requisitos."""
    cronograma = []
    for f in (raw.get("cronograma") or [])[:30] if isinstance(raw.get("cronograma"), list) else []:
        if isinstance(f, dict) and _texto_corto(f.get("actividad"), 150):
            cronograma.append({"actividad": _texto_corto(f.get("actividad"), 150),
                               "fecha": _texto_corto(f.get("fecha"), 80), "fecha_iso": _fecha(f.get("fecha_iso"))})
    documentos = []
    for d in (raw.get("documentos") or [])[:40] if isinstance(raw.get("documentos"), list) else []:
        if isinstance(d, dict) and _url_http(d.get("url")):
            documentos.append({"titulo": _texto_corto(d.get("titulo"), 200) or "Documento", "url": _url_http(d["url"])})
    return {"estado_fuente": _texto_corto(raw.get("estado_fuente"), 80),
            "fecha_apertura": _fecha(raw.get("fecha_apertura")),
            "cronograma": cronograma, "documentos": documentos,
            "requisitos_lista": _lista_textos(raw.get("requisitos_lista"), 20, 400),
            "dirigida_a": _texto_corto(raw.get("dirigida_a"), 300),
            "ejes_tematicos": _lista_textos(raw.get("ejes_tematicos"), 10, 200),
            "criterios_evaluacion": _lista_textos(raw.get("criterios_evaluacion"), 10, 250),
            "duracion_meses": _a_entero(raw.get("duracion_meses")),
            "contrapartida": _texto_corto(raw.get("contrapartida"), 300),
            "leido_tdr": bool(raw.get("leido_tdr")), "tdr_url": _url_http(raw.get("tdr_url"))}


def normalizar_convocatoria(raw: dict, id_defecto: str | None = None) -> dict:
    """Devuelve la convocatoria con todos los campos válidos. ValueError si no tiene nombre."""
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
    conv.update(_detalle(raw))
    return conv