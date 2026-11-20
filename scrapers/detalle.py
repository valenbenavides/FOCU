"""Extrae cronograma, documentos y estado de una página de convocatoria, sin IA."""
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

EXTENSIONES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".odt", ".ods")
MAX_DOCUMENTOS = 40
MAX_CRONOGRAMA = 30
_RE_ACTIVIDAD = re.compile(
    r"apertura|cierre|resultado|publicaci[oó]n|adenda|presentaci[oó]n|evaluaci[oó]n|subsana|observaci|"
    r"inicio|finalizaci[oó]n|recepci[oó]n|selecci[oó]n|socializaci[oó]n|inscripci[oó]n|plazo|aclaraci", re.I)
_RE_ESTADO = re.compile(r"\b(abierta|cerrada|en evaluaci[oó]n|en proceso de evaluaci[oó]n|adjudicada|"
                        r"resultados publicados|pr[oó]xima|suspendida|desierta)\b", re.I)
_RE_ENLACE_GENERICO = re.compile(
    r"^(aqu[ií]|aca|acá|ver|descargar?|click|clic|haz\s+clic|pdf|enlace|link|documento)\b.{0,20}$", re.I)
_RE_TDR = re.compile(r"\b(tdr|t[eé]rminos?\s+de\s+referencia|bases\s+de\s+la\s+convocatoria)\b", re.I)
_RE_NO_TDR = re.compile(r"respuesta|observaci|adenda|listado|resultado|anexo", re.I)
_PALABRAS_CLAVE = ("requisito", "podrán participar", "podran participar", "dirigid", "proponente", "alianza",
                   "cronograma", "recursos", "monto", "valor máximo", "duración", "contrapartida",
                   "criterios de evaluación", "criterios de evaluacion", "puntaje", "elegib", "no podrán",
                   "objetivo", "línea", "eje", "condiciones")


def _limpio(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def _sin_menus(soup: BeautifulSoup) -> BeautifulSoup:
    for t in soup(["script", "style", "nav", "footer", "header", "form", "noscript", "aside"]):
        t.decompose()
    return soup


def extraer_documentos(html: str, base_url: str) -> list[dict]:
    """Documentos enlazados en la página."""
    soup = _sin_menus(BeautifulSoup(html, "lxml"))
    vistos, salida = set(), []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        u = urlparse(href)
        if u.scheme not in ("http", "https") or not u.path.lower().endswith(EXTENSIONES):
            continue
        if href in vistos:
            continue
        vistos.add(href)
        titulo = _limpio(a.get_text(" ")) or unquote(u.path.rsplit("/", 1)[-1])
        if _RE_ENLACE_GENERICO.match(titulo) and a.parent is not None:
            contexto = _limpio(a.parent.get_text(" "))
            if contexto and len(contexto) <= 160:
                titulo = contexto
        salida.append({"titulo": titulo[:200], "url": href})
        if len(salida) >= MAX_DOCUMENTOS:
            break
    return salida


def extraer_cronograma(html: str, fecha_fn) -> list[dict]:
    """Filas actividad-fecha del cronograma."""
    soup = _sin_menus(BeautifulSoup(html, "lxml"))
    filas, vistos = [], set()

    def agregar(actividad: str, fecha_texto: str):
        actividad, fecha_texto = _limpio(actividad), _limpio(fecha_texto)
        if not actividad or len(actividad) > 150:
            return
        f = fecha_fn(fecha_texto)
        if not f or (actividad, f) in vistos:
            return
        vistos.add((actividad, f))
        filas.append({"actividad": actividad, "fecha": fecha_texto[:80], "fecha_iso": f.isoformat()})

    for tr in soup.find_all("tr"):
        celdas = [_limpio(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
        celdas = [c for c in celdas if c]
        if len(celdas) >= 2:
            agregar(celdas[0], " ".join(celdas[1:]))
    if not filas:
        lineas = [_limpio(x) for x in soup.get_text("\n").splitlines() if _limpio(x)]
        for i, ln in enumerate(lineas[:-1]):
            if _RE_ACTIVIDAD.search(ln) and len(ln) <= 100 and not fecha_fn(ln):
                agregar(ln, lineas[i + 1])
    return filas[:MAX_CRONOGRAMA]


def estado_de_pagina(texto: str) -> str:
    """Estado que muestra la página, si aparece."""
    estados = [m.group(1).capitalize() for m in _RE_ESTADO.finditer(texto[:3000])]
    return " · ".join(dict.fromkeys(estados))[:80]


def fecha_de(cronograma: list[dict], palabra: str) -> str | None:
    """Fecha ISO de la primera actividad que empieza con `palabra`."""
    for f in cronograma:
        if f["actividad"].lower().startswith(palabra):
            return f["fecha_iso"]
    return None


def elegir_tdr(documentos: list[dict]) -> dict | None:
    """PDF de los términos de referencia, si está enlazado."""
    for d in documentos or []:
        if (urlparse(d["url"]).path.lower().endswith(".pdf") and _RE_TDR.search(d.get("titulo", ""))
                and not _RE_NO_TDR.search(d.get("titulo", ""))):
            return d
    return None


def seleccionar_paginas(paginas: list[str], limite: int = 12000) -> str:
    """Texto de las páginas más relevantes de un PDF, hasta `limite` caracteres."""
    textos = [re.sub(r"\s+", " ", p or "").strip() for p in paginas]
    puntaje = [sum(p.lower().count(k) for k in _PALABRAS_CLAVE) for p in textos]
    orden = [0, 1] + sorted(range(2, len(textos)), key=lambda i: -puntaje[i])
    elegidas, total = [], 0
    for i in orden:
        if i >= len(textos) or not textos[i]:
            continue
        if total + len(textos[i]) > limite and elegidas:
            continue
        elegidas.append(i)
        total += len(textos[i])
    return "\n".join(f"[Página {i + 1}] {textos[i]}" for i in sorted(elegidas))[:limite]