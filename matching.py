"""Compara el perfil con las convocatorias: filtro duro y puntaje ponderado con afinidad temática."""
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

PESOS = {"afinidad_tematica": 0.50, "area_conocimiento": 0.20, "monto": 0.10,
         "fase_proyecto": 0.10, "alcance": 0.10}

CALIBRACION_EMBED = {"gemini": (0.55, 0.80), "local": (0.20, 0.60)}
FACTOR_LEXICO = 1.5
TOPE_LEXICO = 0.9

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


MODELO_EMBED_GEMINI = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
MODELO_EMBED_LOCAL = os.environ.get(
    "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
_cache: dict[tuple[str, str, str], list[float]] = {}
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
        _cliente_gemini = genai.Client()
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


def _tokenizar(texto: str) -> set[str]:
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    return {p[:6] for p in palabras if p not in STOPWORDS_ES and len(p) > 2}


def afinidad_lexica(a: str, b: str) -> float:
    """Solapamiento de palabras entre dos textos (0-1); no depende de la IA."""
    ta, tb = _tokenizar(a), _tokenizar(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


def afinidades_tematicas(descripcion: str, objetivos: list[str]):
    """Para cada objetivo devuelve (puntaje 0-1, detalle), combinando embeddings calibrados y palabras."""
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


def generar_recomendacion(perfil: dict, conv: dict) -> str:
    """Pide a la IA una recomendación breve sobre el encaje del perfil con la convocatoria."""
    from llm import generar_texto
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


def cargar_convocatorias_json() -> list[dict]:
    """Lee datos/convocatorias.json; lista vacía si no existe."""
    if not RUTA_JSON.exists():
        return []
    return json.loads(RUTA_JSON.read_text(encoding="utf-8"))


def esta_vigente(conv: dict, hoy: date | None = None) -> bool:
    """Sin fecha de cierre se considera vigente; con fecha, hasta el último día inclusive."""
    f = str(conv.get("fecha_limite") or "")[:10]
    return not f or f >= (hoy or date.today()).isoformat()


def cargar_convocatorias() -> list[dict]:
    """Convocatorias activas, verificadas y vigentes de la base; si falla, las del JSON."""
    try:
        con = db.conectar()
        try:
            return [c for c in db.listar_convocatorias(con, solo_activas=True)
                    if c.get("verificada") and esta_vigente(c)]
        finally:
            con.close()
    except Exception as e:
        print(f"[matching] No se pudo leer la base, se usa el JSON: {e}")
        return cargar_convocatorias_json()


_PALABRAS_BECA = re.compile(
    r"\b(becas?|scholarships?|fellowships?|doctorados?|maestr[ií]as?|posgrados?|pregrados?|pasant[ií]as?|"
    r"movilidad|intercambios?|estudios en|cursos? de|summer|winter|study visits?|research grants?|"
    r"pol[ií]ticas? de formaci[oó]n|formaci[oó]n de alto nivel|teaching assistants?|"
    r"fulbright|chevening|colfuturo|icetex|daad)\b", re.IGNORECASE)


def es_beca(conv: dict) -> bool:
    """Heurística por palabras: ¿es una beca o apoyo para una persona y no para un proyecto?"""
    texto = f"{conv.get('nombre', '')}. {conv.get('entidad', '')}. {(conv.get('objetivo') or '')[:200]}"
    return bool(_PALABRAS_BECA.search(texto))


def comparar(perfil: dict, conv: dict, hoy: date | None = None, afinidad=None) -> dict:
    """Compara perfil y convocatoria; afinidad es (puntaje, detalle) calculado en lote por rankear()."""
    hoy = hoy or date.today()

    motivos = []
    if perfil.get("tipo_beneficiario") and perfil["tipo_beneficiario"] not in conv["tipos_beneficiario"]:
        motivos.append("No está dirigida a tu tipo de beneficiario.")
    if conv.get("fecha_limite") and date.fromisoformat(conv["fecha_limite"]) < hoy:
        motivos.append(f"Cerró el {conv['fecha_limite']}.")
    if motivos:
        return {"elegible": False, "porcentaje": 0, "detalle": {}, "motivos_exclusion": motivos}

    puntajes, detalle = {}, {}

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

    for campo in ("area_conocimiento", "fase_proyecto", "monto_requerido_cop", "alcance"):
        if not perfil.get(campo):
            detalle["monto" if campo == "monto_requerido_cop" else campo] = "no lo indicaste: no cuenta en el cálculo"

    from schema import ETIQUETAS
    nombre_campo = {"area_conocimiento": "área de conocimiento", "fase_proyecto": "fase del proyecto",
                    "alcance": "alcance del proyecto"}
    lista_conv = {"area_conocimiento": "areas", "fase_proyecto": "fases", "alcance": "alcances"}
    def _txt(campo, valores):
        return ", ".join(dict(ETIQUETAS.get(campo, [])).get(v, v) for v in valores)
    beca = es_beca(conv)
    faltantes = []
    if beca:
        faltantes.append("Esta es una beca o apoyo para personas, no para un proyecto: revisa en «Requisitos y documentos» el nivel de "
                         "estudios, el idioma, el promedio y los documentos que pide.")
    for campo in () if beca else ("area_conocimiento", "fase_proyecto", "alcance"):
        valores = conv.get(lista_conv[campo]) or []
        if detalle.get(campo) == "no coincide":
            actual = _txt(campo, [perfil[campo]])
            faltantes.append(f"Tu {nombre_campo[campo]} ({actual}) no figura entre las que cubre: {_txt(campo, valores)}.")
        elif not perfil.get(campo) and detalle.get(campo) != NO_ESP and valores:
            faltantes.append(f"Indica tu {nombre_campo[campo]} en tu perfil: esta convocatoria cubre {_txt(campo, valores)}.")
    msg_monto = None if beca else MENSAJES_BRECHA.get(("monto", detalle.get("monto")))
    if msg_monto:
        faltantes.append(msg_monto)
    elif not beca and not perfil.get("monto_requerido_cop") and (conv.get("monto_min") is not None or conv.get("monto_max") is not None):
        faltantes.append("Indica el monto que necesitas en tu perfil para saber si el monto de la convocatoria te alcanza.")
    if not perfil.get("descripcion_proyecto"):
        faltantes.append("Describe tu tema de interés o tu idea en tu perfil para medir qué tanto se parece al objetivo de la convocatoria.")

    peso_total = sum(PESOS[k] for k in puntajes)
    pct = round(100 * sum(PESOS[k] * v for k, v in puntajes.items()) / peso_total) if peso_total else 0
    def _cop(n):
        return "$ " + f"{int(n):,}".replace(",", ".") + " COP"

    def _lista(campo, valores):
        valores = list(valores or [])
        if len(valores) > 4:
            return f"{len(valores)} opciones (varias)"
        return _txt(campo, valores) or "—"

    def _corto(t, n):
        t = " ".join((t or "").split())
        return t if len(t) <= n else t[:n].rstrip() + "…"

    lo_m, hi_m = conv.get("monto_min"), conv.get("monto_max")
    rango = ("No especifica" if lo_m is None and hi_m is None
             else f"{_cop(lo_m) if lo_m is not None else '…'} a {_cop(hi_m) if hi_m is not None else '…'}")
    tu = {"afinidad_tematica": _corto(perfil.get("descripcion_proyecto"), 110) or "No escribiste la descripción",
          "area_conocimiento": _txt("area_conocimiento", [perfil["area_conocimiento"]]) if perfil.get("area_conocimiento") else "No indicado",
          "fase_proyecto": _txt("fase_proyecto", [perfil["fase_proyecto"]]) if perfil.get("fase_proyecto") else "No indicado",
          "alcance": _txt("alcance", [perfil["alcance"]]) if perfil.get("alcance") else "No indicado",
          "monto": _cop(perfil["monto_requerido_cop"]) if perfil.get("monto_requerido_cop") else "No indicado"}
    ella = {"afinidad_tematica": _corto(conv.get("objetivo") or conv.get("nombre"), 130) or "—",
            "area_conocimiento": "No especifica" if detalle.get("area_conocimiento") == NO_ESP else _lista("area_conocimiento", conv.get("areas")),
            "fase_proyecto": "No especifica" if detalle.get("fase_proyecto") == NO_ESP else _lista("fase_proyecto", conv.get("fases")),
            "alcance": "No especifica" if detalle.get("alcance") == NO_ESP else _lista("alcance", conv.get("alcances")),
            "monto": rango}
    nombres = {"afinidad_tematica": "Tema del proyecto", "area_conocimiento": "Área de conocimiento",
               "fase_proyecto": "Fase del proyecto", "alcance": "Alcance", "monto": "Monto"}
    desglose = []
    for k in ("afinidad_tematica", "area_conocimiento", "fase_proyecto", "alcance", "monto"):
        cuenta = k in puntajes
        if cuenta:
            peso_ef = PESOS[k] / peso_total
            res = (f"{round(100 * puntajes[k])} % de parecido" if k == "afinidad_tematica" else detalle.get(k, ""))
            aporte = round(100 * peso_ef * puntajes[k], 1)
        else:
            peso_ef, aporte = 0, 0
            if k == "afinidad_tematica":
                res = "No cuenta: falta la descripción de tu proyecto"
            elif detalle.get(k) == NO_ESP:
                res = "No cuenta: la convocatoria no lo especifica"
            else:
                res = "No cuenta: no lo indicaste"
        desglose.append({"clave": k, "criterio": nombres[k], "tu_perfil": tu[k], "convocatoria": ella[k],
                         "resultado": res, "peso": round(100 * peso_ef), "aporte": aporte, "cuenta": cuenta})

    return {"elegible": True, "porcentaje": pct, "calculable": bool(peso_total), "detalle": detalle,
            "puntajes": {k: round(100 * v) for k, v in puntajes.items()}, "faltantes": faltantes, "desglose": desglose,
            "es_beca": beca, "motivos_exclusion": []}


MENSAJES_BRECHA = {
    ("area_conocimiento", "no coincide"): "Tu área de conocimiento no figura entre las que cubre esta convocatoria.",
    ("fase_proyecto", "no coincide"): "Tu proyecto está en una fase distinta a las que financia esta convocatoria.",
    ("alcance", "no coincide"): "El alcance de tu proyecto no coincide con el de la convocatoria.",
    ("monto", "cubre solo parte de lo que necesitas"): "El monto máximo de la convocatoria es menor a lo que necesitas: tendrías que cubrir la diferencia con otra fuente o contrapartida.",
    ("monto", "por debajo del mínimo financiable"): "Lo que pides es menor al mínimo que financia esta convocatoria.",
}


def brechas(perfil: dict, conv: dict, hoy: date | None = None) -> list[str]:
    """Lista en lenguaje simple lo que le falta al perfil para encajar con la convocatoria."""
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
        afinidades = afinidades_tematicas(perfil["descripcion_proyecto"], objetivos)

    resultados = []
    for c, afinidad in zip(convocatorias, afinidades):
        r = comparar(perfil, c, afinidad=afinidad)
        resultados.append({"id": str(c["id"]), "nombre": c["nombre"], "entidad": c.get("entidad", ""),
                           "fecha_limite": c.get("fecha_limite"), "requisitos": c.get("requisitos", ""),
                           "beneficios": c.get("beneficios", ""), "objetivo": c.get("objetivo", ""),
                           "url": c.get("url", ""), "fuente": c.get("fuente", "manual"),
                           "verificada": c.get("verificada", True),
                           "recursos_totales": c.get("recursos_totales"), "monto_min": c.get("monto_min"),
                           "monto_max": c.get("monto_max"), "estado_fuente": c.get("estado_fuente", ""),
                           "fecha_apertura": c.get("fecha_apertura"), "cronograma": c.get("cronograma", []),
                           "documentos": c.get("documentos", []), "requisitos_lista": c.get("requisitos_lista", []),
                           "dirigida_a": c.get("dirigida_a", ""), "ejes_tematicos": c.get("ejes_tematicos", []),
                           "criterios_evaluacion": c.get("criterios_evaluacion", []),
                           "duracion_meses": c.get("duracion_meses"), "contrapartida": c.get("contrapartida", ""),
                           "leido_tdr": c.get("leido_tdr", False), "tdr_url": c.get("tdr_url", ""),
                           **r})
    resultados.sort(key=lambda x: (x.get("fecha_limite") is None, x.get("fecha_limite") or ""))
    resultados.sort(key=lambda x: (x["elegible"], x.get("calculable", False), x["porcentaje"]), reverse=True)
    return resultados