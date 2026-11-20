"""Recolecta convocatorias de las páginas de scrapers/fuentes.json y las guarda sin verificar."""
import hashlib
import io
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import db  # noqa: E402
from scrapers import detalle  # noqa: E402
from convocatorias import normalizar_convocatoria  # noqa: E402
from schema import ALCANCES, AREAS, FASES, TIPOS_BENEFICIARIO  # noqa: E402

RUTA_FUENTES = Path(__file__).parent / "fuentes.json"
USER_AGENT = "FOCU-Bot/0.1 (proyecto academico, Universidad del Cauca)"
MAX_TEXTO = 10000
MAX_PAGINAS_PDF = 6
MAX_PAGINAS_TDR = 80
MAX_BYTES_TDR = 15_000_000
MAX_TEXTO_TDR = 12000
VERSION_LECTOR = 5

PROMPT = """Eres un asistente que extrae datos de convocatorias de financiación (ciencia, tecnología, \
innovación, emprendimiento) a partir del texto de una página web o de un PDF.
Hoy es {hoy}. Devuelve SOLO un objeto JSON con estos campos:
- "es_convocatoria": true solo si el texto describe UNA convocatoria u oferta de financiación concreta \
(no una noticia, un listado, un reglamento general ni una página de navegación).
- "nombre": título de la convocatoria.
- "objetivo": de qué trata y qué busca financiar, en tus palabras (máximo 700 caracteres).
- "requisitos": resumen de quién puede participar y qué se exige (máximo 800 caracteres).
- "requisitos_lista": lista de los requisitos y condiciones UNO POR UNO, cada uno en una frase corta \
y fiel al texto (por ejemplo: tipo de entidad proponente, composición de la alianza, experiencia exigida, \
límite de proyectos por entidad, duración máxima, contrapartida). Máximo 15 elementos. Si no aparecen, [].
- "dirigida_a": a quién va dirigida, en una o dos frases (máximo 300 caracteres), o null.
- "ejes_tematicos": lista de líneas, ejes o temas que la convocatoria prioriza (máximo 10), o [].
- "beneficios": qué se recibe, por ejemplo monto o apoyos (máximo 400 caracteres).
- "fecha_limite": fecha de CIERRE en formato AAAA-MM-DD. Si solo aparece la fecha de apertura o no hay fecha, null.
- "monto_min" y "monto_max": enteros en pesos colombianos si el texto los indica; si no, null.
- "tipos_beneficiario": lista con valores de {tipos}.
- "areas": lista con valores de {areas}.
- "fases": lista con valores de {fases}.
- "alcances": lista con valores de {alcances}.
Reglas: no inventes datos; si algo no aparece, usa null o una lista vacía. Usa solo los valores permitidos.

TEXTO:
---
{texto}
---"""


PROMPT_TDR = """Eres un asistente que lee los TÉRMINOS DE REFERENCIA de una convocatoria de financiación \
(son el documento oficial con las reglas). Hoy es {hoy}. Devuelve SOLO un objeto JSON con estos campos:
- "requisitos_lista": lista de requisitos y condiciones de participación UNO POR UNO, cada uno en una frase \
corta y fiel al texto (quién puede presentarse, composición de la alianza, experiencia exigida, límite de \
proyectos por entidad, restricciones, documentos obligatorios). Máximo 20 elementos.
- "dirigida_a": a quién va dirigida, en una o dos frases (máximo 300 caracteres), o null.
- "ejes_tematicos": lista de líneas, ejes o temas priorizados (máximo 10), o [].
- "duracion_meses": duración máxima de los proyectos en meses (entero) o null.
- "contrapartida": qué contrapartida se exige (máximo 300 caracteres) o null.
- "criterios_evaluacion": lista de criterios de evaluación; si hay puntaje, inclúyelo, por ejemplo \
"Calidad técnica (30 puntos)". Máximo 10 elementos.
- "monto_min" y "monto_max": enteros en pesos colombianos por proyecto, si el texto los indica; si no, null.
- "fecha_limite": fecha de CIERRE de la convocatoria (último día para presentar propuestas) en formato \
AAAA-MM-DD, tomada del cronograma. Si solo aparece la apertura u otra fecha, null.
Reglas: no inventes datos; si algo no aparece, usa null o una lista vacía. Copia las cifras tal como están.

TEXTO (páginas seleccionadas del PDF):
---
{texto}
---"""


def cargar_fuentes() -> list[dict]:
    return json.loads(RUTA_FUENTES.read_text(encoding="utf-8"))


def descargar(url: str, timeout: int = 25):
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "es"}, timeout=timeout)
    r.raise_for_status()
    return r


_robots: dict[str, robotparser.RobotFileParser] = {}


def permitido(url: str) -> bool:
    """Indica si robots.txt permite leer la dirección (si no se puede leer, se permite)."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return rp.can_fetch(USER_AGENT, url)


MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
         "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def fecha_en_texto(texto: str) -> date | None:
    """Primera fecha del texto (varios formatos) o None."""
    t = (texto or "").lower()
    meses = "|".join(MESES)
    for patron, orden in ((rf"(\d{{1,2}})\s+(?:de\s+)?({meses})\s+(?:de\s+)?(\d{{4}})", "dmy"),
                          (rf"({meses})\s+(\d{{1,2}}),?\s+(\d{{4}})", "mdy"),
                          (r"(\d{4})-(\d{2})-(\d{2})", "ymd"), (r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", "dmy_num")):
        m = re.search(patron, t)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            if orden == "mdy":
                return date(int(c), MESES[a], int(b))
            if orden == "dmy":
                return date(int(c), MESES[b], int(a))
            if orden == "ymd":
                return date(int(a), int(b), int(c))
            return date(int(c), int(b), int(a))
        except ValueError:
            continue
    return None


_RE_PLAZO = re.compile(r"plazo\s+de\s+(?:solicitud|postulaci[oó]n|inscripci[oó]n|presentaci[oó]n)|"
                       r"application\s+deadline|fecha\s+l[ií]mite|deadline", re.I)
_RE_FECHA_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")


_PALABRAS_VACIAS = {"de", "del", "la", "el", "en", "y", "para", "the", "of", "in", "and", "for", "a", "an", "to",
                    "daad", "programme", "programmes", "program", "programs", "programa", "programas",
                    "scholarship", "scholarships", "beca", "becas", "colombia", "germany", "alemania"}


def _palabras(texto: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúñü0-9]+", (texto or "").lower())
            if len(w) > 2 and w not in _PALABRAS_VACIAS}


def _posicion_del_titulo(completo: str, nombre: str) -> tuple[int, int]:
    """Posición y largo del título del programa en la página, o (-1, 0)."""
    exacto = completo.lower().find(nombre.strip().lower())
    if exacto >= 0:
        return exacto, len(nombre.strip())
    objetivo = _palabras(re.sub(r"\(.*?\)|[–-].*$", " ", nombre)) or _palabras(nombre)
    if not objetivo:
        return -1, 0
    mejor, pos, largo, cursor = 0.0, -1, 0, 0
    for linea in completo.splitlines(True):
        txt = linea.strip()
        if 3 <= len(txt) <= 120:
            comunes = len(objetivo & _palabras(txt))
            puntaje = comunes / max(len(objetivo), len(_palabras(txt)) or 1)
            if puntaje > mejor and puntaje >= 0.6:
                mejor, pos, largo = puntaje, cursor, len(linea)
        cursor += len(linea)
    return pos, largo


def plazo_de_programa(completo: str, nombre: str, hoy: date | None = None) -> str | None:
    """Plazo AAAA-MM-DD de un programa dentro de un listado: la próxima fecha futura o la última."""
    if not nombre or not completo:
        return None
    hoy = hoy or date.today()
    i, largo = _posicion_del_titulo(completo, nombre)
    if i < 0:
        return None
    ventana = completo[i + largo: i + largo + 700]
    m = _RE_PLAZO.search(ventana)
    if not m:
        return None
    resto = ventana[m.end(): m.end() + 260]
    fechas = []
    for f in _RE_FECHA_NUM.finditer(resto):
        try:
            if f.group(1):
                fechas.append(date(int(f.group(3)), int(f.group(2)), int(f.group(1))))
            else:
                fechas.append(date(int(f.group(4)), int(f.group(5)), int(f.group(6))))
        except ValueError:
            continue
    if not fechas:
        return None
    futuras = [d for d in fechas if d >= hoy]
    return (min(futuras) if futuras else max(fechas)).isoformat()


def _fila_de(a):
    """Fila (tr, li, article) que contiene el enlace."""
    for padre in a.parents:
        if padre.name in ("tr", "li", "article"):
            return padre
    return None


def extraer_enlaces(html: str, base_url: str, fuente: dict, limitar: bool = True) -> list[tuple]:
    """Enlaces (url, texto, fecha_apertura) que coinciden con patron_enlaces."""
    soup = BeautifulSoup(html, "lxml")
    patron = re.compile(fuente.get("patron_enlaces") or "convocatoria", re.I)
    solo_href = bool(fuente.get("patron_solo_en_enlace"))
    dominio = urlparse(base_url).netloc.replace("www.", "")
    vistos, salida = set(), []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        u = urlparse(href)
        if u.scheme not in ("http", "https"):
            continue
        if u.netloc.replace("www.", "") != dominio and not fuente.get("permitir_dominios_externos"):
            continue
        texto = a.get_text(" ", strip=True)
        if not (patron.search(href) or (not solo_href and patron.search(texto))):
            continue
        if href.rstrip("/") == base_url.rstrip("/") or href in vistos:
            continue
        fila = _fila_de(a)
        if fuente.get("solo_filas_de_tabla") and not any(p.name == "tr" for p in a.parents):
            continue
        vistos.add(href)
        salida.append((href, texto, fecha_en_texto(fila.get_text(" ", strip=True)) if fila else None))
    if not salida and fuente.get("solo_filas_de_tabla"):
        return extraer_enlaces(html, base_url, {**fuente, "solo_filas_de_tabla": False}, limitar)
    return salida[: int(fuente.get("max_enlaces", 10))] if limitar else salida


def url_de_pagina(url: str, n: int, parametro: str = "page") -> str:
    """Dirección de la página n del listado."""
    if n == 0:
        return url
    return url + ("&" if "?" in url else "?") + f"{parametro}={n}"


def texto_de_respuesta(resp, limite: int | None = MAX_TEXTO) -> str:
    """Texto limpio de una página HTML o PDF, recortado a limite."""
    tipo = (resp.headers.get("content-type") or "").lower()
    if "pdf" in tipo or str(getattr(resp, "url", "")).lower().endswith(".pdf"):
        from pypdf import PdfReader
        lector = PdfReader(io.BytesIO(resp.content))
        texto = "\n".join((pg.extract_text() or "") for pg in lector.pages[:MAX_PAGINAS_PDF])
    else:
        soup = BeautifulSoup(resp.text, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header", "form", "noscript", "aside"]):
            t.decompose()
        texto = soup.get_text("\n", strip=True)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{2,}", "\n", texto)
    return texto[:limite] if limite else texto


def extraer_con_llm(texto: str, url: str) -> dict:
    from llm import generar_json
    return generar_json(PROMPT.format(
        hoy=date.today().isoformat(), tipos=TIPOS_BENEFICIARIO, areas=AREAS,
        fases=FASES, alcances=ALCANCES, texto=texto))


def extraer_tdr_con_llm(texto: str, url: str) -> dict:
    from llm import generar_json
    return generar_json(PROMPT_TDR.format(hoy=date.today().isoformat(), texto=texto))


def leer_tdr(doc: dict, descargar_fn, extraer_tdr_fn) -> dict:
    """Lee los términos de referencia (PDF) con IA; {} si falla."""
    resp = descargar_fn(doc["url"])
    if len(getattr(resp, "content", b"") or b"") > MAX_BYTES_TDR:
        return {}
    from pypdf import PdfReader
    lector = PdfReader(io.BytesIO(resp.content))
    paginas = [(pg.extract_text() or "") for pg in lector.pages[:MAX_PAGINAS_TDR]]
    texto = detalle.seleccionar_paginas(paginas, MAX_TEXTO_TDR)
    if len(texto) < 300:
        return {}
    datos = dict(extraer_tdr_fn(texto, doc["url"]) or {})
    cierre = datos_clave("\n".join(paginas)).get("fecha_limite")
    if cierre:
        datos["fecha_limite"] = cierre
    return datos


def _mezclar_tdr(datos: dict, tdr: dict, doc: dict) -> None:
    """Los términos de referencia mandan sobre el resumen de la página."""
    for campo in ("requisitos_lista", "ejes_tematicos", "criterios_evaluacion"):
        if isinstance(tdr.get(campo), list) and tdr[campo]:
            datos[campo] = tdr[campo]
    for campo in ("dirigida_a", "contrapartida", "duracion_meses"):
        if tdr.get(campo):
            datos[campo] = tdr[campo]
    for campo in ("monto_min", "monto_max"):
        if tdr.get(campo) and not datos.get(campo):
            datos[campo] = tdr[campo]
    cierre = str(tdr.get("fecha_limite") or "")[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cierre) and not datos.get("fecha_limite"):
        datos["fecha_limite"] = cierre
    datos["leido_tdr"] = True
    datos["tdr_url"] = doc["url"]


_RE_CIERRE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?(?:fecha\s+(?:l[ií]mite\s+)?de\s+)?cierre(?:\s+de\s+(?:la\s+)?convocatoria)?\s*:?\s*(.*)$", re.I)
_RE_RECURSOS = re.compile(r"(?:recursos\s+disponibles|total\s+de\s+recursos|bolsa\s+de\s+recursos)\D{0,15}\$\s*([\d.,]+)", re.I)


_RE_SIN_ABIERTAS = re.compile(
    r"no\s+hay\s+convocatorias\s+abiertas|no\s+open\s+calls|no\s+calls\s+(?:are\s+)?open|"
    r"there\s+are\s+no\s+open\s+calls", re.I)

_RE_CERRADA = re.compile(r"convocatoria\s+(?:cerrada|finalizada)|call\s+(?:is\s+)?closed|"
                         r"applications?\s+[^.\n]{0,60}?\b(?:are|is)\s+(?:now\s+)?closed|"
                         r"(?:postulaciones|inscripciones)\s+(?:cerradas|finalizadas)", re.I)


def _pesos(texto: str) -> int | None:
    """Convierte '$28.094.792.444,00' en entero."""
    digitos = re.sub(r"\D", "", texto.split(",")[0])
    return int(digitos) if digitos else None


def datos_clave(texto: str) -> dict:
    """Fecha de cierre y recursos leídos del texto con reglas, sin IA."""
    clave: dict = {}
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    for i, ln in enumerate(lineas):
        m = _RE_CIERRE.match(ln)
        if not m or len(ln) > 120:
            continue
        f = fecha_en_texto(" ".join([m.group(1)] + lineas[i + 1:i + 3]))
        if f:
            clave["fecha_limite"] = f.isoformat()
            break
    m = _RE_RECURSOS.search(texto.replace("\n", " "))
    if m and _pesos(m.group(1)):
        clave["recursos_totales"] = _pesos(m.group(1))
    return clave


def _agregar_detalle(datos: dict, resp, texto: str) -> None:
    """Agrega cronograma, documentos y estado copiados de la página."""
    tipo = (resp.headers.get("content-type") or "").lower()
    if "pdf" in tipo or str(getattr(resp, "url", "")).lower().endswith(".pdf"):
        return
    base = str(getattr(resp, "url", "") or datos.get("url", ""))
    datos["documentos"] = detalle.extraer_documentos(resp.text, base)
    datos["cronograma"] = detalle.extraer_cronograma(resp.text, fecha_en_texto)
    datos["estado_fuente"] = detalle.estado_de_pagina(texto)
    apertura = detalle.fecha_de(datos["cronograma"], "apertura")
    cierre = detalle.fecha_de(datos["cronograma"], "cierre")
    if apertura:
        datos["fecha_apertura"] = apertura
    if cierre and not datos.get("fecha_limite"):
        datos["fecha_limite"] = cierre


class SinCambios(Exception):
    """La página no cambió desde la última lectura."""


def procesar_enlace(url: str, fuente: dict, descargar_fn, extraer_fn, extraer_tdr_fn=None,
                    huella_fn=None) -> dict | None:
    """Convocatoria normalizada o None si la página no lo es; huella_fn lanza SinCambios si no cambió."""
    resp = descargar_fn(url)
    completo = texto_de_respuesta(resp, limite=None)
    if len(completo) < 200:
        return None
    if huella_fn and huella_fn(hashlib.sha1(completo.encode("utf-8", "ignore")).hexdigest()[:16]):
        raise SinCambios()
    if _RE_SIN_ABIERTAS.search(completo):
        return None
    if _RE_CERRADA.search(completo[:1500]):
        return None
    clave = datos_clave(completo)
    texto = completo[:MAX_TEXTO]
    if clave:
        texto = "DATOS LEÍDOS DE LA PÁGINA: " + json.dumps(clave, ensure_ascii=False) + "\n" + texto
    datos = extraer_fn(texto, url)
    if not datos.get("es_convocatoria"):
        return None
    datos["url"] = url
    datos["entidad"] = fuente.get("entidad", "")
    _agregar_detalle(datos, resp, completo)
    datos["id"] = "web-" + hashlib.sha1(url.encode()).hexdigest()[:10]
    if clave.get("fecha_limite"):
        datos["fecha_limite"] = clave["fecha_limite"]
    elif not datos.get("fecha_limite"):
        datos["fecha_limite"] = plazo_de_programa(completo, datos.get("nombre", "")) or datos.get("fecha_limite")
    vigente = not datos.get("fecha_limite") or str(datos["fecha_limite"])[:10] >= date.today().isoformat()
    tdr = detalle.elegir_tdr(datos.get("documentos")) if (extraer_tdr_fn and vigente
                                                           and fuente.get("leer_tdr", True)) else None
    if tdr:
        try:
            _mezclar_tdr(datos, leer_tdr(tdr, descargar_fn, extraer_tdr_fn), tdr)
        except Exception:
            pass
    if clave.get("recursos_totales"):
        datos["recursos_totales"] = clave["recursos_totales"]
        frase = f"Recursos disponibles de la convocatoria: ${clave['recursos_totales']:,} COP.".replace(",", ".")
        beneficios = str(datos.get("beneficios") or "").strip()
        datos["beneficios"] = (frase + " " + beneficios).strip()[:600]
    return normalizar_convocatoria(datos)


def agregar_por_url(con, url: str, descargar_fn=None, extraer_fn=None, extraer_tdr_fn=None,
                    revisar_robots: bool = True) -> dict:
    """Agrega una convocatoria por su dirección, sin verificar. Lanza ValueError si la página no sirve."""
    url = (url or "").strip()
    if not re.match(r"^https?://\S+$", url, re.I):
        raise ValueError("Escribe una dirección completa que empiece por https://")
    descargar_fn = descargar_fn or descargar
    extraer_fn = extraer_fn or extraer_con_llm
    if extraer_tdr_fn is None and extraer_fn is extraer_con_llm:
        extraer_tdr_fn = extraer_tdr_con_llm
    if revisar_robots and not permitido(url):
        raise ValueError("Ese sitio no permite la lectura automática. Agrégala a mano.")
    sitio = urlparse(url).netloc.lower().removeprefix("www.")
    try:
        conv = procesar_enlace(url, {"entidad": sitio, "leer_tdr": True}, descargar_fn, extraer_fn, extraer_tdr_fn)
    except requests.RequestException as e:
        raise ValueError(f"No se pudo abrir la página: {e}")
    if conv is None:
        raise ValueError("No encontré una convocatoria abierta en esa página. Revisa que sea la página de la "
                         "convocatoria (no un listado) o agrégala a mano.")
    _respetar_fecha_manual(con, conv)
    cierre = str(conv.get("fecha_limite") or "")[:10]
    if cierre and cierre < (date.today() - timedelta(days=db.DIAS_VENCIDA)).isoformat():
        raise ValueError(f"Esa convocatoria cerró el {cierre}, hace más de {db.DIAS_VENCIDA} días: ya no se guarda.")
    db.guardar_convocatoria(con, conv, fuente="enlace", verificada=False, respetar_verificadas=True)
    db.marcar_enlace(con, url, "guardada", VERSION_LECTOR)
    con.commit()
    return conv


def _cierre_en_pdf(doc: dict, descargar_fn) -> str | None:
    """Fecha de cierre del cronograma de un PDF, con reglas."""
    resp = descargar_fn(doc["url"])
    if len(getattr(resp, "content", b"") or b"") > MAX_BYTES_TDR:
        return None
    from pypdf import PdfReader
    lector = PdfReader(io.BytesIO(resp.content))
    paginas = [(pg.extract_text() or "") for pg in lector.pages[:MAX_PAGINAS_TDR]]
    return datos_clave("\n".join(paginas)).get("fecha_limite")


def completar_fechas(con, descargar_fn=None, pausa: float = 0.5) -> int:
    """Busca con reglas (sin IA) la fecha de cierre de las convocatorias que no la tienen; devuelve cuántas completó."""
    descargar_fn = descargar_fn or descargar
    try:
        listados = {f["id"]: f.get("url_listado") for f in cargar_fuentes() if f.get("url_listado")}
    except Exception:
        listados = {}
    texto_listado: dict[str, str] = {}
    n = 0
    for c in db.listar_convocatorias(con, solo_activas=False):
        if c.get("fecha_limite") or c.get("fecha_manual") or not str(c.get("url") or "").startswith("http"):
            continue
        try:
            con.asegurar()
            completo = texto_de_respuesta(descargar_fn(c["url"]), limite=None)
            fecha = datos_clave(completo).get("fecha_limite") or plazo_de_programa(completo, c.get("nombre", ""))
            if not fecha:
                tdr = detalle.elegir_tdr(c.get("documentos"))
                fecha = _cierre_en_pdf(tdr, descargar_fn) if tdr else None
            if not fecha and listados.get(c.get("fuente")):
                fid = c["fuente"]
                if fid not in texto_listado:
                    try:
                        texto_listado[fid] = texto_de_respuesta(descargar_fn(listados[fid]), limite=None)
                    except Exception:
                        texto_listado[fid] = ""
                fecha = plazo_de_programa(texto_listado[fid], c.get("nombre", ""))
            if fecha and db.poner_fecha_convocatoria(con, c["id"], fecha, manual=False):
                n += 1
        except Exception:
            pass
        if pausa:
            time.sleep(pausa)
    return n


def _respetar_fecha_manual(con, conv: dict | None) -> None:
    """Conserva la fecha de cierre escrita a mano por la VRI."""
    if not conv:
        return
    previa = db.obtener_convocatoria(con, conv["id"])
    if previa and previa.get("fecha_manual") and previa.get("fecha_limite"):
        conv["fecha_limite"] = previa["fecha_limite"]
        conv["fecha_manual"] = True


def recolectar(con, fuentes: list[dict] | None = None, descargar_fn=None, extraer_fn=None,
               pausa: float = 1.0, revisar_robots: bool = True, extraer_tdr_fn=None) -> dict:
    """Recorre las fuentes, guarda lo encontrado y devuelve un informe por fuente."""
    descargar_fn = descargar_fn or descargar
    extraer_fn = extraer_fn or extraer_con_llm
    if extraer_tdr_fn is None and extraer_fn is extraer_con_llm:
        extraer_tdr_fn = extraer_tdr_con_llm
    fuentes = fuentes if fuentes is not None else cargar_fuentes()
    informe = {"inicio": datetime.now().isoformat(timespec="seconds"), "fuentes": []}

    for f in fuentes:
        res = {"fuente": f.get("id", "?"), "nombre": f.get("nombre", ""), "enlaces": 0,
               "guardadas": 0, "descartadas": 0, "vencidas": 0, "omitidas": 0, "errores": []}
        informe["fuentes"].append(res)
        directas = [u for u in (f.get("urls_directas") or []) if str(u).startswith("http")]
        if not f.get("activa", True) or not (f.get("url_listado") or directas):
            res["errores"].append("Fuente desactivada o sin dirección (revisar fuentes.json).")
            continue
        try:
            enlaces = []
            if directas:
                enlaces = [(u, "", None) for u in directas]
            else:
                if revisar_robots and not permitido(f["url_listado"]):
                    res["errores"].append("El sitio no permite el acceso automático (robots.txt).")
                    continue
                for n in range(int(f.get("paginas", 1))):
                    url_pag = url_de_pagina(f["url_listado"], n, f.get("parametro_pagina", "page"))
                    resp = descargar_fn(url_pag)
                    enlaces += extraer_enlaces(resp.text, getattr(resp, "url", url_pag), f, limitar=False)
        except Exception as e:
            res["errores"].append(f"No se pudo abrir el listado: {e}")
            continue
        con.asegurar()
        hoy = date.today()
        dias_max = f.get("max_antiguedad_dias")
        nuevos, vistos = [], set()
        for href, texto, apertura in enlaces:
            if href in vistos:
                continue
            vistos.add(href)
            if db.enlace_eliminado(con, href):
                res["omitidas"] += 1
            elif not directas and db.enlace_visto(con, href, VERSION_LECTOR):
                res["omitidas"] += 1
            elif dias_max and apertura and (hoy - apertura).days > int(dias_max):
                res["omitidas"] += 1
            else:
                nuevos.append(href)
        nuevos = nuevos[: int(f.get("max_enlaces", 10))]

        res["enlaces"] = len(nuevos)
        for href in nuevos:
            try:
                con.asegurar()
                if revisar_robots and not permitido(href):
                    res["descartadas"] += 1
                    continue
                huella = {}

                def _huella(h, _href=href, _huella=huella):
                    _huella["h"] = h
                    return bool(directas) and db.enlace_visto(con, f"{_href}#h={h}", VERSION_LECTOR)

                conv = procesar_enlace(href, f, descargar_fn, extraer_fn, extraer_tdr_fn, huella_fn=_huella)
                con.asegurar()
                _respetar_fecha_manual(con, conv)
                if conv is None:
                    res["descartadas"] += 1
                    previa = db.obtener_convocatoria(con, "web-" + hashlib.sha1(href.encode()).hexdigest()[:10])
                    if previa and not previa["verificada"]:
                        db.cambiar_estado_convocatoria(con, previa["id"], activa=False)
                    db.marcar_enlace(con, href, "descartada", VERSION_LECTOR)
                elif conv.get("fecha_limite") and conv["fecha_limite"] < hoy.isoformat():
                    res["vencidas"] += 1
                    if conv["fecha_limite"] >= (hoy - timedelta(days=db.DIAS_VENCIDA)).isoformat():
                        db.guardar_convocatoria(con, conv, fuente=f["id"], verificada=False,
                                                respetar_verificadas=True)
                    db.marcar_enlace(con, href, "vencida", VERSION_LECTOR)
                else:
                    db.guardar_convocatoria(con, conv, fuente=f["id"], verificada=False,
                                            respetar_verificadas=True)
                    db.marcar_enlace(con, href, "guardada", VERSION_LECTOR)
                    res["guardadas"] += 1
                if directas and huella.get("h"):
                    db.marcar_enlace(con, f"{href}#h={huella['h']}", "leida", VERSION_LECTOR)
                con.commit()
            except SinCambios:
                res["omitidas"] += 1
            except Exception as e:
                res["errores"].append(f"{href}: {e}")
            if pausa:
                time.sleep(pausa)

    try:
        informe["fechas_completadas"] = completar_fechas(con, descargar_fn, pausa=pausa)
    except Exception as e:
        informe["fechas_completadas"] = 0
        informe.setdefault("avisos", []).append(f"No se pudieron completar fechas: {e}")
    try:
        limpieza = db.mantenimiento_convocatorias(con)
        informe["purgadas"] = limpieza["vencidas_borradas"] + limpieza["borradas"]
    except Exception:
        informe["purgadas"] = 0
    informe["fin"] = datetime.now().isoformat(timespec="seconds")
    informe["total_guardadas"] = sum(r["guardadas"] for r in informe["fuentes"])
    return informe


if __name__ == "__main__":
    conexion = db.conectar()
    db.iniciar_db(conexion)
    print(json.dumps(recolectar(conexion), ensure_ascii=False, indent=2))
    conexion.close()