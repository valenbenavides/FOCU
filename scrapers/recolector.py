"""Recolector de convocatorias desde páginas oficiales.

Por cada fuente de scrapers/fuentes.json hace estos pasos:
  1. Abre la página de listado y reúne los enlaces que parecen convocatorias.
  2. Abre cada enlace y saca su texto (HTML o PDF).
  3. Le pide a la IA que extraiga los datos al formato de FOCU (sin inventar: si un dato
     no aparece, queda vacío) y que descarte las páginas que no son convocatorias.
  4. Guarda el resultado en la base como NO verificado: la interfaz lo avisa y la VRI
     puede revisarlo y marcarlo como verificado. Los verificados no se sobrescriben.

Buenas prácticas incluidas: respeta robots.txt, se identifica con un User-Agent,
espera entre peticiones y limita cuántos enlaces visita por fuente.

Como cada sitio cambia de formato, no se escribe un lector distinto por página: la IA
interpreta el texto. A cambio, hay que revisar los resultados."""
import hashlib
import io
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Permite ejecutar este archivo directamente: python scrapers/recolector.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:  # carga el .env antes de importar db (que lee DATABASE_URL al importarse)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import db  # noqa: E402
from convocatorias import normalizar_convocatoria  # noqa: E402
from schema import ALCANCES, AREAS, FASES, TIPOS_BENEFICIARIO  # noqa: E402

RUTA_FUENTES = Path(__file__).parent / "fuentes.json"
USER_AGENT = "FOCU-Bot/0.1 (proyecto academico, Universidad del Cauca)"
MAX_TEXTO = 7000        # caracteres que se le envían a la IA por página
MAX_PAGINAS_PDF = 6
VERSION_LECTOR = 2      # súbela cuando mejore la lectura: lo ya revisado se vuelve a revisar una vez

PROMPT = """Eres un asistente que extrae datos de convocatorias de financiación (ciencia, tecnología, \
innovación, emprendimiento) a partir del texto de una página web o de un PDF.
Hoy es {hoy}. Devuelve SOLO un objeto JSON con estos campos:
- "es_convocatoria": true solo si el texto describe UNA convocatoria u oferta de financiación concreta \
(no una noticia, un listado, un reglamento general ni una página de navegación).
- "nombre": título de la convocatoria.
- "objetivo": de qué trata y qué busca financiar, en tus palabras (máximo 400 caracteres).
- "requisitos": resumen de quién puede participar y qué se exige (máximo 600 caracteres).
- "beneficios": qué se recibe, por ejemplo monto o apoyos (máximo 300 caracteres).
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


# ---------------------------------------------------------------------------
# Descarga y lectura
# ---------------------------------------------------------------------------
def cargar_fuentes() -> list[dict]:
    return json.loads(RUTA_FUENTES.read_text(encoding="utf-8"))


def descargar(url: str, timeout: int = 25):
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "es"}, timeout=timeout)
    r.raise_for_status()
    return r


_robots: dict[str, robotparser.RobotFileParser] = {}


def permitido(url: str) -> bool:
    """Respeta robots.txt. Si no se puede leer, se asume permitido."""
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
    """Primera fecha que aparezca en un texto: 'Octubre 23, 2026', '23 de octubre de 2026',
    '2026-10-23' o '23/10/2026'. None si no hay."""
    t = (texto or "").lower()
    meses = "|".join(MESES)
    for patron, orden in ((rf"(\d{{1,2}})\s+(?:de\s+)?({meses})\s+(?:de\s+)?(\d{{4}})", "dmy"),
                          (rf"({meses})\s+(\d{{1,2}}),?\s+(\d{{4}})", "mdy"),
                          (r"(\d{4})-(\d{2})-(\d{2})", "ymd"), (r"(\d{1,2})/(\d{1,2})/(\d{4})", "dmy_num")):
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


def _fila_de(a):
    """Contenedor más cercano del enlace que parezca una fila de listado (tr, li, article)."""
    for padre in a.parents:
        if padre.name in ("tr", "li", "article"):
            return padre
    return None


def extraer_enlaces(html: str, base_url: str, fuente: dict, limitar: bool = True) -> list[tuple]:
    """Enlaces del mismo sitio cuyo texto o dirección coincide con `patron_enlaces`.
    Devuelve (enlace, texto, fecha_apertura); la fecha se lee de la fila del listado si aparece."""
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
            continue   # enlaces del menú, pie de página, etc.
        vistos.add(href)
        salida.append((href, texto, fecha_en_texto(fila.get_text(" ", strip=True)) if fila else None))
    if not salida and fuente.get("solo_filas_de_tabla"):
        # la página no usa tabla: se reintenta sin esa restricción
        return extraer_enlaces(html, base_url, {**fuente, "solo_filas_de_tabla": False}, limitar)
    return salida[: int(fuente.get("max_enlaces", 10))] if limitar else salida


def url_de_pagina(url: str, n: int, parametro: str = "page") -> str:
    """Dirección de la página n (0 = la primera) de un listado paginado."""
    if n == 0:
        return url
    return url + ("&" if "?" in url else "?") + f"{parametro}={n}"


def texto_de_respuesta(resp, limite: int | None = MAX_TEXTO) -> str:
    """Texto limpio de una página HTML o de un PDF, recortado a `limite` (None = completo)."""
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


# ---------------------------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------------------------
_RE_CIERRE = re.compile(r"^\s*(?:fecha\s+(?:l[ií]mite\s+)?de\s+)?cierre(?:\s+de\s+(?:la\s+)?convocatoria)?\s*:?\s*(.*)$", re.I)
_RE_RECURSOS = re.compile(r"(?:recursos\s+disponibles|total\s+de\s+recursos|bolsa\s+de\s+recursos)\D{0,15}\$\s*([\d.,]+)", re.I)


def _pesos(texto: str) -> int | None:
    """'$28.094.792.444,00' -> 28094792444 (formato colombiano: punto = miles, coma = decimales)."""
    digitos = re.sub(r"\D", "", texto.split(",")[0])
    return int(digitos) if digitos else None


def datos_clave(texto: str) -> dict:
    """Fecha de cierre y recursos leídos directamente del texto completo de la página, sin IA.
    Las páginas largas ponen el cronograma después de miles de caracteres: la IA solo ve el
    inicio, así que estos dos datos se buscan aquí y se le entregan (o se imponen) al final."""
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


def procesar_enlace(url: str, fuente: dict, descargar_fn, extraer_fn) -> dict | None:
    """Devuelve la convocatoria normalizada, o None si la página no es una convocatoria."""
    completo = texto_de_respuesta(descargar_fn(url), limite=None)
    if len(completo) < 200:
        return None  # casi sin contenido (página vacía o cargada con JavaScript)
    clave = datos_clave(completo)
    texto = completo[:MAX_TEXTO]
    if clave:
        texto = "DATOS LEÍDOS DE LA PÁGINA: " + json.dumps(clave, ensure_ascii=False) + "\n" + texto
    datos = extraer_fn(texto, url)
    if not datos.get("es_convocatoria"):
        return None
    datos["url"] = url
    datos["entidad"] = fuente.get("entidad", "")
    datos["id"] = "web-" + hashlib.sha1(url.encode()).hexdigest()[:10]
    if clave.get("fecha_limite"):
        datos["fecha_limite"] = clave["fecha_limite"]   # lo leído de la página manda sobre la IA
    if clave.get("recursos_totales"):
        datos["recursos_totales"] = clave["recursos_totales"]
        frase = f"Recursos disponibles de la convocatoria: ${clave['recursos_totales']:,} COP.".replace(",", ".")
        beneficios = str(datos.get("beneficios") or "").strip()
        datos["beneficios"] = (frase + " " + beneficios).strip()[:600]
    return normalizar_convocatoria(datos)


def recolectar(con, fuentes: list[dict] | None = None, descargar_fn=None, extraer_fn=None,
               pausa: float = 1.0, revisar_robots: bool = True) -> dict:
    """Recorre las fuentes y guarda lo que encuentra. Devuelve un informe por fuente.
    Los parámetros descargar_fn / extraer_fn existen para poder probarlo sin internet."""
    descargar_fn = descargar_fn or descargar
    extraer_fn = extraer_fn or extraer_con_llm
    fuentes = fuentes if fuentes is not None else cargar_fuentes()
    informe = {"inicio": datetime.now().isoformat(timespec="seconds"), "fuentes": []}

    for f in fuentes:
        res = {"fuente": f.get("id", "?"), "nombre": f.get("nombre", ""), "enlaces": 0,
               "guardadas": 0, "descartadas": 0, "vencidas": 0, "omitidas": 0, "errores": []}
        informe["fuentes"].append(res)
        if not f.get("activa", True) or not f.get("url_listado"):
            res["errores"].append("Fuente desactivada o sin dirección (revisar fuentes.json).")
            continue
        try:
            if revisar_robots and not permitido(f["url_listado"]):
                res["errores"].append("El sitio no permite el acceso automático (robots.txt).")
                continue
            enlaces = []
            for n in range(int(f.get("paginas", 1))):
                url_pag = url_de_pagina(f["url_listado"], n, f.get("parametro_pagina", "page"))
                resp = descargar_fn(url_pag)
                enlaces += extraer_enlaces(resp.text, getattr(resp, "url", url_pag), f, limitar=False)
        except Exception as e:
            res["errores"].append(f"No se pudo abrir el listado: {e}")
            continue
        hoy = date.today()
        dias_max = f.get("max_antiguedad_dias")
        nuevos, vistos = [], set()
        for href, texto, apertura in enlaces:
            if href in vistos:
                continue
            vistos.add(href)
            if db.enlace_visto(con, href, VERSION_LECTOR):
                res["omitidas"] += 1          # ya revisada en una corrida anterior
            elif dias_max and apertura and (hoy - apertura).days > int(dias_max):
                res["omitidas"] += 1          # abrió hace demasiado: ni se gasta la IA
            else:
                nuevos.append(href)
        nuevos = nuevos[: int(f.get("max_enlaces", 10))]

        res["enlaces"] = len(nuevos)
        for href in nuevos:
            try:
                if revisar_robots and not permitido(href):
                    res["descartadas"] += 1
                    continue
                conv = procesar_enlace(href, f, descargar_fn, extraer_fn)
                if conv is None:
                    res["descartadas"] += 1
                    db.marcar_enlace(con, href, "descartada", VERSION_LECTOR)
                elif conv.get("fecha_limite") and conv["fecha_limite"] < hoy.isoformat():
                    res["vencidas"] += 1      # ya cerró: no sirve para postular
                    previa = db.obtener_convocatoria(con, conv["id"])
                    if previa and not previa["verificada"]:   # una versión vieja sin fecha: se retira
                        db.cambiar_estado_convocatoria(con, conv["id"], activa=False)
                    db.marcar_enlace(con, href, "vencida", VERSION_LECTOR)
                else:
                    db.guardar_convocatoria(con, conv, fuente=f["id"], verificada=False,
                                            respetar_verificadas=True)
                    db.marcar_enlace(con, href, "guardada", VERSION_LECTOR)
                    res["guardadas"] += 1
                con.commit()
            except Exception as e:
                res["errores"].append(f"{href}: {e}")
            if pausa:
                time.sleep(pausa)

    informe["fin"] = datetime.now().isoformat(timespec="seconds")
    informe["total_guardadas"] = sum(r["guardadas"] for r in informe["fuentes"])
    return informe


if __name__ == "__main__":
    # Ejecución manual:  python scrapers/recolector.py
    conexion = db.conectar()
    db.iniciar_db(conexion)
    print(json.dumps(recolectar(conexion), ensure_ascii=False, indent=2))
    conexion.close()