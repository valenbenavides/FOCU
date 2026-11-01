"""Comparación perfil vs. convocatoria.
1) Filtro duro (código): tipo de beneficiario y fecha límite.
2) Puntaje ponderado: campos exactos (código) + afinidad temática (embeddings + señal
   léxica). Si los embeddings no están disponibles, la afinidad se calcula solo con la
   señal léxica; si además no hay texto, ese campo se ignora y el matching sigue
   funcionando solo con los campos exactos.

Embeddings (EMBEDDINGS_PROVIDER en el .env):
  - "gemini": por API de Google (necesita GEMINI_API_KEY). Gasta casi nada de memoria,
              por eso es lo recomendado en servidores pequeños o gratuitos.
  - "local":  modelo en el propio servidor con fastembed (pip install fastembed). Sin API,
              pero necesita ~1 GB de RAM: no cabe en un servidor gratuito de 512 MB.
  - "none":   solo señal léxica.
  - "auto" (por defecto): gemini si hay GEMINI_API_KEY; si no, local si está instalado; si no, none."""
import json
import math
import os
import re
import time
from datetime import date
from pathlib import Path

import db

BASE = Path(__file__).parent
RUTA_JSON = BASE / "datos" / "convocatorias.json"
RUTA_JSON_EJEMPLO = BASE / "datos" / "convocatorias_ejemplo.json"

# Variable MACRO: el tema del proyecto (descripción / línea de investigación) frente al objetivo
# de la convocatoria. Variables MICRO: área, monto, fase y alcance. Suman 1.0.
PESOS = {"afinidad_tematica": 0.50, "area_conocimiento": 0.20, "monto": 0.10,
         "fase_proyecto": 0.10, "alcance": 0.10}

# Calibración de la similitud coseno de los embeddings a una escala 0-1 ABSOLUTA:
# por debajo de "bajo" = sin relación (0); por encima de "alto" = muy relacionado (1).
# Son valores aproximados: se afinan viendo el detalle "similitud cruda" de cada tarjeta.
CALIBRACION_EMBED = {"gemini": (0.55, 0.80), "local": (0.20, 0.60)}
FACTOR_LEXICO = 1.5   # el solapamiento de palabras es bajo aunque el tema coincida: se escala
TOPE_LEXICO = 0.9     # coincidir en palabras nunca es prueba de un 100 % temático

# Cuánto pesa el embedding vs. la coincidencia léxica dentro de la afinidad temática.
PESO_EMBEDDING, PESO_LEXICO = 0.7, 0.3

STOPWORDS_ES = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por", "un",
    "para", "con", "no", "una", "su", "al", "lo", "como", "más", "o", "pero", "sus",
    "le", "ya", "este", "si", "porque", "esta", "entre", "cuando", "muy", "sin",
    "sobre", "tambien", "me", "hasta", "hay", "donde", "quien", "desde", "todo", "nos",
    "durante", "todos", "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante",
    "ellos", "e", "esto", "mi", "antes", "algunos", "que", "unos", "yo", "otro", "otras",
    "otra", "el", "tanto", "esa", "estos", "mucho", "quienes", "nada", "muchos", "cual",
    "poco", "ella", "estar", "estas", "algunas", "algo", "nosotros",
}


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
MODELO_EMBED_GEMINI = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
MODELO_EMBED_LOCAL = os.environ.get(
    "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
_cache: dict[tuple[str, str, str], list[float]] = {}   # (proveedor, rol, texto) -> vector
_modelo_local = None
_cliente_gemini = None


def proveedor_embeddings() -> str:
    p = os.environ.get("EMBEDDINGS_PROVIDER", "auto").strip().lower()
    if p in ("gemini", "local", "none"):
        return p
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    try:
        import fastembed  # noqa: F401
        return "local"
    except ImportError:
        return "none"


def _embeddings_gemini(faltan: list[str], rol: str) -> list[list[float]]:
    global _cliente_gemini
    from google import genai
    from google.genai import types
    if _cliente_gemini is None:
        _cliente_gemini = genai.Client()   # lee GEMINI_API_KEY del entorno
    # Asimétrico: RETRIEVAL_QUERY para lo que escribe el usuario y RETRIEVAL_DOCUMENT
    # para el texto de cada convocatoria.
    task = "RETRIEVAL_QUERY" if rol == "consulta" else "RETRIEVAL_DOCUMENT"
    salida: list[list[float]] = []
    for i in range(0, len(faltan), 50):
        lote = faltan[i:i + 50]
        for intento in range(3):
            try:
                r = _cliente_gemini.models.embed_content(
                    model=MODELO_EMBED_GEMINI, contents=lote,
                    config=types.EmbedContentConfig(task_type=task))
                salida.extend([list(e.values) for e in r.embeddings])
                break
            except Exception as e:
                temporal = any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
                if temporal and intento < 2:
                    time.sleep(2 ** intento)
                    continue
                raise
    return salida


def _embeddings_local(faltan: list[str]) -> list[list[float]]:
    global _modelo_local
    if _modelo_local is None:
        from fastembed import TextEmbedding
        _modelo_local = TextEmbedding(model_name=MODELO_EMBED_LOCAL)
    return [[float(x) for x in v] for v in _modelo_local.embed(faltan)]


def _embeddings(textos: list[str], rol: str, proveedor: str) -> list[list[float]]:
    faltan = list(dict.fromkeys(t for t in textos if (proveedor, rol, t) not in _cache))
    if faltan:
        vectores = _embeddings_gemini(faltan, rol) if proveedor == "gemini" else _embeddings_local(faltan)
        for t, v in zip(faltan, vectores):
            _cache[(proveedor, rol, t)] = v
    return [_cache[(proveedor, rol, t)] for t in textos]


def _coseno(a, b) -> float:
    prod = sum(x * y for x, y in zip(a, b))
    norma = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return prod / norma if norma else 0.0


# ---------------------------------------------------------------------------
# Afinidad léxica (respaldo sin IA, y señal complementaria con IA)
# ---------------------------------------------------------------------------
def _tokenizar(texto: str) -> set[str]:
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    # Se compara por las primeras 6 letras: "investigación" ~ "investigaciones", "agrícola" ~ "agrícolas".
    return {p[:6] for p in palabras if p not in STOPWORDS_ES and len(p) > 2}


def afinidad_lexica(a: str, b: str) -> float:
    """Coeficiente de solapamiento de palabras (0-1). Simple pero explicable: no
    depende de la IA y sirve de respaldo si los embeddings fallan."""
    ta, tb = _tokenizar(a), _tokenizar(b)
    if not ta or not tb:
        return 0.0
    # Media geométrica de los tamaños (no el mínimo): una descripción corta frente a un texto
    # largo ya no da casi 100 % solo porque todas sus palabras aparecen en alguna parte.
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


def afinidades_tematicas(descripcion: str, objetivos: list[str]):
    """Devuelve, para cada objetivo, (puntaje 0-1, texto_detalle).

    El puntaje combina la similitud coseno calibrada a escala absoluta (así una convocatoria
    sin relación NO llega a 100 % solo por ser la mejor del lote) con el solapamiento de
    palabras. Si los embeddings no están disponibles, usa solo las palabras."""
    if not objetivos:
        return []

    lexicos = [min(TOPE_LEXICO, afinidad_lexica(descripcion, obj) * FACTOR_LEXICO) for obj in objetivos]
    sims, abs_norm = None, None

    proveedor = proveedor_embeddings()
    if proveedor != "none":
        try:
            v_desc = _embeddings([descripcion], "consulta", proveedor)[0]
            v_objs = _embeddings(objetivos, "documento", proveedor)
            sims = [_coseno(v_desc, v) for v in v_objs]
            bajo, alto = CALIBRACION_EMBED.get(proveedor, (0.3, 0.7))
            abs_norm = [max(0.0, min(1.0, (x - bajo) / (alto - bajo))) for x in sims]
        except Exception as e:
            print(f"[matching] Embeddings ({proveedor}) no disponibles, se usa solo señal léxica: {e}")
            sims, abs_norm = None, None

    resultados = []
    for i in range(len(objetivos)):
        if abs_norm is None:
            puntaje = lexicos[i]
            detalle = f"coincidencia de palabras {round(lexicos[i] * 100)} % (sin embeddings)"
        else:
            puntaje = PESO_EMBEDDING * abs_norm[i] + PESO_LEXICO * lexicos[i]
            detalle = (f"similitud cruda {sims[i]:.2f} -> {round(abs_norm[i] * 100)} % + "
                       f"palabras {round(lexicos[i] * 100)} % = {round(puntaje * 100)} %")
        resultados.append((puntaje, detalle))
    return resultados


# ---------------------------------------------------------------------------
# IA: recomendaciones en lenguaje natural
# ---------------------------------------------------------------------------
def generar_recomendacion(perfil: dict, conv: dict) -> str:
    """Pide a la IA una recomendación breve (máx. 3 frases) sobre qué tan bien encaja el
    perfil con esta convocatoria y qué debería reforzar el usuario. Se llama bajo demanda
    (un botón) para no gastar cuota en convocatorias que el usuario no abre."""
    from llm import generar_texto  # import perezoso: el resto funciona sin la librería de IA
    prompt = (
        "Eres un asesor de financiación de la Universidad del Cauca. "
        f"Perfil del usuario: {json.dumps(perfil, ensure_ascii=False)}. "
        f"Convocatoria: {conv.get('nombre', '')} ({conv.get('entidad', '')}). "
        f"Objetivo de la convocatoria: {conv.get('objetivo', '')}. "
        f"Requisitos: {conv.get('requisitos') or 'No especificados'}. "
        "En máximo 3 frases y en español, dile al usuario qué tan bien encaja su "
        "perfil y qué debería preparar o reforzar concretamente para cumplir los "
        "requisitos. No repitas literalmente los requisitos ni el objetivo."
    )
    try:
        return generar_texto(prompt)
    except Exception as e:
        return f"No fue posible generar la recomendación con IA en este momento ({e})."


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def cargar_convocatorias_json() -> list[dict]:
    """Respaldo: lee el JSON de convocatorias (el tuyo, o el de ejemplo)."""
    ruta = RUTA_JSON if RUTA_JSON.exists() else RUTA_JSON_EJEMPLO
    return json.loads(ruta.read_text(encoding="utf-8"))


def cargar_convocatorias() -> list[dict]:
    """Convocatorias activas Y con visto bueno de la VRI, desde la base de datos; si la
    base falla, desde el JSON."""
    try:
        con = db.conectar()
        try:
            return [c for c in db.listar_convocatorias(con, solo_activas=True) if c.get("verificada")]
        finally:
            con.close()
    except Exception as e:
        print(f"[matching] No se pudo leer la base, se usa el JSON: {e}")
        return cargar_convocatorias_json()


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------
def comparar(perfil: dict, conv: dict, hoy: date | None = None, afinidad=None) -> dict:
    """afinidad: tupla opcional (puntaje 0-1, detalle) ya calculada en lote por
    rankear(). Si no se pasa, ese campo simplemente se omite."""
    hoy = hoy or date.today()

    # Filtro duro
    motivos = []
    if perfil.get("tipo_beneficiario") and perfil["tipo_beneficiario"] not in conv["tipos_beneficiario"]:
        motivos.append("No está dirigida a tu tipo de beneficiario.")
    if conv.get("fecha_limite") and date.fromisoformat(conv["fecha_limite"]) < hoy:
        motivos.append(f"Cerró el {conv['fecha_limite']}.")
    if motivos:
        return {"elegible": False, "porcentaje": 0, "detalle": {}, "motivos_exclusion": motivos}

    # Puntaje ponderado (los campos que el usuario no ha dado se ignoran)
    puntajes, detalle = {}, {}

    # Un criterio solo puntúa si la convocatoria lo especifica. Si no dice nada (listas completas,
    # sin montos, "multidisciplinar"), no se premia con 100 %: se muestra como "no especificado".
    from schema import ALCANCES, AREAS, FASES
    NO_ESP = "la convocatoria no lo especifica"

    if perfil.get("area_conocimiento"):
        if "multidisciplinar" in conv["areas"] or set(conv["areas"]) >= set(AREAS):
            detalle["area_conocimiento"] = NO_ESP
        else:
            ok = perfil["area_conocimiento"] in conv["areas"]
            puntajes["area_conocimiento"] = 1.0 if ok else 0.0
            detalle["area_conocimiento"] = "coincide" if ok else "no coincide"

    if perfil.get("fase_proyecto"):
        if set(conv["fases"]) >= set(FASES):
            detalle["fase_proyecto"] = NO_ESP
        else:
            ok = perfil["fase_proyecto"] in conv["fases"]
            puntajes["fase_proyecto"] = 1.0 if ok else 0.0
            detalle["fase_proyecto"] = "coincide" if ok else "no coincide"

    if perfil.get("monto_requerido_cop"):
        m, lo, hi = perfil["monto_requerido_cop"], conv.get("monto_min"), conv.get("monto_max")
        if lo is None and hi is None:
            detalle["monto"] = NO_ESP
        elif (lo is None or m >= lo) and (hi is None or m <= hi):
            puntajes["monto"], detalle["monto"] = 1.0, "dentro del rango"
        elif hi is not None and m > hi:
            puntajes["monto"], detalle["monto"] = 0.5, "cubre solo parte de lo que necesitas"
        else:
            puntajes["monto"], detalle["monto"] = 0.0, "por debajo del mínimo financiable"

    if perfil.get("alcance"):
        if set(conv["alcances"]) >= set(ALCANCES):
            detalle["alcance"] = NO_ESP
        else:
            ok = perfil["alcance"] in conv["alcances"]
            puntajes["alcance"] = 1.0 if ok else 0.0
            detalle["alcance"] = "coincide" if ok else "no coincide"

    if afinidad is not None:
        puntaje_af, detalle_af = afinidad
        puntajes["afinidad_tematica"] = puntaje_af
        detalle["afinidad_tematica"] = detalle_af

    peso_total = sum(PESOS[k] for k in puntajes)
    pct = round(100 * sum(PESOS[k] * v for k, v in puntajes.items()) / peso_total) if peso_total else 0
    return {"elegible": True, "porcentaje": pct, "detalle": detalle, "motivos_exclusion": []}


MENSAJES_BRECHA = {
    ("area_conocimiento", "no coincide"): "Tu área de conocimiento no figura entre las que cubre esta convocatoria.",
    ("fase_proyecto", "no coincide"): "Tu proyecto está en una fase distinta a las que financia esta convocatoria.",
    ("alcance", "no coincide"): "El alcance de tu proyecto no coincide con el de la convocatoria.",
    ("monto", "cubre solo parte de lo que necesitas"): "El monto máximo de la convocatoria es menor a lo que necesitas: tendrías que cubrir la diferencia con otra fuente o contrapartida.",
    ("monto", "por debajo del mínimo financiable"): "Lo que pides es menor al mínimo que financia esta convocatoria.",
}


def brechas(perfil: dict, conv: dict, hoy: date | None = None) -> list[str]:
    """Lista, en lenguaje simple, de lo que le falta al perfil para encajar con la
    convocatoria (versión inicial: compara los campos estructurados)."""
    r = comparar(perfil, conv, hoy=hoy)
    if not r["elegible"]:
        return list(r["motivos_exclusion"])
    salida = []
    for campo, valor in r["detalle"].items():
        msg = MENSAJES_BRECHA.get((campo, valor))
        if msg:
            salida.append(msg)
    return salida


def rankear(perfil: dict, convocatorias=None):
    if convocatorias is None:
        convocatorias = cargar_convocatorias()

    afinidades = [None] * len(convocatorias)
    if perfil.get("descripcion_proyecto"):
        objetivos = [f"{c.get('nombre', '')}. {c.get('objetivo', '')}" for c in convocatorias]
        # Se calculan TODAS las afinidades en una sola pasada: así la normalización
        # compara las convocatorias entre sí, no una por una contra un umbral fijo.
        afinidades = afinidades_tematicas(perfil["descripcion_proyecto"], objetivos)

    resultados = []
    for c, afinidad in zip(convocatorias, afinidades):
        r = comparar(perfil, c, afinidad=afinidad)
        resultados.append({"id": str(c["id"]), "nombre": c["nombre"], "entidad": c.get("entidad", ""),
                           "fecha_limite": c.get("fecha_limite"), "requisitos": c.get("requisitos", ""),
                           "beneficios": c.get("beneficios", ""), "objetivo": c.get("objetivo", ""),
                           "url": c.get("url", ""), "fuente": c.get("fuente", "manual"),
                           "verificada": c.get("verificada", True), **r})
    resultados.sort(key=lambda x: (x["elegible"], x["porcentaje"]), reverse=True)
    return resultados