"""Lista de chequeo de una postulación: formato FOR-40 de la VRI o los requisitos de la propia convocatoria."""
import hashlib
import re

ESTADOS = ("pendiente", "si", "no", "no_aplica")

REQUISITOS = [
    {"clave": "aval_departamento", "titulo": "Aval del Jefe de Departamento", "formato": "FOR-1", "aval": True,
     "ayuda": "Una sola hoja por ambos lados, firmado en original, con nombre y dependencia de quien firma. "
              "Las horas de dedicación deben coincidir con la ficha resumen."},
    {"clave": "aval_facultad", "titulo": "Aval del Comité de Facultad", "formato": "FOR-33", "aval": True,
     "ayuda": "Una sola hoja por ambos lados, firmado en original. Su fecha debe ser posterior al aval del Departamento."},
    {"clave": "solicitud_aval", "titulo": "Solicitud de aval para registro del proyecto", "formato": "FOR-23",
     "ayuda": "Todos los campos diligenciados. Un solo director de proyecto, que es quien firma. Detallar cargo/dependencia y anexos."},
    {"clave": "ficha_resumen", "titulo": "Ficha resumen del proyecto", "formato": "FOR-6",
     "ayuda": "Todos los campos diligenciados. El presupuesto y las horas deben coincidir con el formato de presupuesto y el aval."},
    {"clave": "hojas_vida", "titulo": "Hojas de vida de los investigadores", "formato": "FOR-13",
     "ayuda": "De todos los integrantes, con el URL del CvLAC. GrupLAC y CvLAC actualizados."},
    {"clave": "presupuesto", "titulo": "Presupuesto global", "formato": "FOR-16",
     "ayuda": "Guiarse por el instructivo del formato. Desarrollo interno no tiene efectivo: solo valores en especie."},
    {"clave": "cronograma", "titulo": "Cronograma y responsabilidades", "formato": "",
     "ayuda": "En meses (1, 2, 3…), sin nombres de meses. Máximo 12 meses."},
    {"clave": "productos", "titulo": "Productos esperados", "formato": "",
     "ayuda": "Descritos en la ficha resumen, en «Resultados esperados» (artículos, libros, etc.)."},
    {"clave": "carta_entidades", "titulo": "Carta de entidades o investigadores externos", "formato": "", "opcional": True,
     "ayuda": "Si participan externos: carta firmada en original que diga que apoyan el proyecto. "
              "Si aportan dinero, debe decir el valor."},
    {"clave": "soportes_eticos", "titulo": "Soportes ético-legales", "formato": "", "opcional": True,
     "ayuda": "Si hay encuestas, entrevistas o colecta: consentimiento informado, modelo de encuesta, resoluciones."},
]

CLAVES = {r["clave"] for r in REQUISITOS}
OPCIONALES = {r["clave"] for r in REQUISITOS if r.get("opcional")}
AVALES = ("aval_departamento", "aval_facultad")

BASICOS = [
    {"clave": "b_identidad", "titulo": "Documento de identidad", "formato": "", "ayuda": ""},
    {"clave": "b_hoja_vida", "titulo": "Hoja de vida (con CvLAC si la convocatoria lo pide)", "formato": "", "ayuda": ""},
    {"clave": "b_formulario", "titulo": "Formulario o propuesta que pide la convocatoria, diligenciado", "formato": "", "ayuda": ""},
    {"clave": "b_terminos", "titulo": "Leí los términos de referencia completos y cumplo las condiciones", "formato": "", "ayuda": ""},
    {"clave": "b_aval", "titulo": "Carta de aval o respaldo institucional (si la convocatoria lo pide)", "formato": "",
     "ayuda": "", "opcional": True},
]
MAX_ITEMS = 25


def es_interna(conv: dict | None) -> bool:
    """Convocatoria interna de investigación de la VRI (la que usa el formato FOR-40)."""
    if not conv:
        return False
    entidad = (conv.get("entidad") or "").lower()
    nombre = (conv.get("nombre") or "").lower()
    de_la_u = "universidad del cauca" in entidad or "vicerrector" in entidad or "vri" in entidad.split()
    return de_la_u and bool(re.search(r"investigaci|proyecto|desarrollo interno", nombre))


def _trozos(texto: str) -> list[str]:
    partes = re.split(r"[\n•;]+|(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])", texto or "")
    return [p.strip(" -–·*") for p in partes if p and len(p.strip()) > 3]


def lista_para(conv: dict | None) -> list[dict]:
    """Los ítems de la lista de chequeo que le corresponden a esta convocatoria."""
    if conv is None or es_interna(conv):
        return REQUISITOS
    textos = [t for t in (conv.get("requisitos_lista") or []) if isinstance(t, str)]
    if not textos:
        textos = _trozos(conv.get("requisitos") or "")
    items, vistos = [], set()
    for t in textos:
        t = " ".join(t.split())
        if len(t) < 4 or t.lower() in vistos:
            continue
        vistos.add(t.lower())
        items.append({"clave": "c_" + hashlib.sha1(t.lower().encode("utf-8")).hexdigest()[:10],
                      "titulo": t[:220], "formato": "", "ayuda": ""})
        if len(items) >= MAX_ITEMS:
            break
    return items or BASICOS


def validar_cambio(estados: dict, clave: str, nuevo: str, items: list[dict] | None = None) -> None:
    """Lanza ValueError si el cambio no es válido. `estados` es {clave: estado} actual."""
    items = items or REQUISITOS
    por_clave = {r["clave"]: r for r in items}
    if clave not in por_clave:
        raise ValueError("Ese requisito no existe.")
    if nuevo not in ESTADOS:
        raise ValueError("Estado no válido.")
    if nuevo == "no_aplica" and not por_clave[clave].get("opcional"):
        raise ValueError("Ese requisito es obligatorio: no puede marcarse como «No aplica».")
    despues = {**estados, clave: nuevo}
    if "aval_facultad" in por_clave and despues.get("aval_facultad") == "si" and despues.get("aval_departamento") != "si":
        raise ValueError("El aval de Facultad va después del de Departamento: marca primero el del Departamento.")


def avales_faltantes(estados: dict, items: list[dict] | None = None) -> list[str]:
    """Títulos de los avales (solo existen en la lista interna) que aún no están en «Sí»."""
    return [r["titulo"] for r in (items or REQUISITOS) if r["clave"] in AVALES and estados.get(r["clave"]) != "si"]


def resumen(estados: dict, items: list[dict] | None = None) -> dict:
    items = items or REQUISITOS
    obligatorios = [r["clave"] for r in items if not r.get("opcional")]
    return {"departamento": estados.get("aval_departamento", "pendiente") if "aval_departamento" in {r["clave"] for r in items} else None,
            "facultad": estados.get("aval_facultad", "pendiente") if "aval_facultad" in {r["clave"] for r in items} else None,
            "cumplidos": sum(1 for c in obligatorios if estados.get(c) == "si"),
            "total": len(obligatorios)}