"""Asistente de la VRI: consulta grupos, personas, postulaciones y convocatorias. Solo lectura."""
from datetime import date

import db
from llm import ChatAgente

SISTEMA = """Eres FOCU, el asistente de la Vicerrectoría de Investigaciones (VRI) de la Universidad del Cauca.
Ayudas al equipo de la VRI a consultar lo que hay en la plataforma: grupos de investigación, personas,
postulaciones y convocatorias. Responde siempre en español.
- Para cualquier dato usa las herramientas; no inventes nombres, cifras, fechas ni estados.
- Si te piden un panorama general ("cómo vamos", "qué hay pendiente"), usa resumen_general.
- Si una búsqueda no encuentra nada, dilo y sugiere otra forma de buscar (otro nombre, otro estado).
- Solo consultas: no puedes aprobar, rechazar, editar ni borrar nada. Si te lo piden, explica en qué
  pestaña de la plataforma lo hace la VRI (Postulaciones, Grupos, Personas, Convocatorias).
- FORMATO (la interfaz es un chat pequeño): responde corto, máximo unas 150 palabras. NO uses tablas,
  encabezados (#), líneas horizontales (---) ni emojis. Usa párrafos breves y, si hace falta, una lista
  con guiones (máximo 8 ítems) y **negrita** solo para nombres o cifras clave.
- Si el usuario saluda o escribe algo breve, responde natural en una o dos frases, sin consultar nada.
- Haz como máximo una pregunta al final, y solo si hace falta."""

FASES = ("en_director", "devuelta", "en_vri", "aprobada", "rechazada")
NOMBRES_FASE = {"en_director": "en revisión del director", "devuelta": "devuelta al integrante",
                "en_vri": "en revisión de la VRI", "aprobada": "aprobada", "rechazada": "rechazada"}

ESQUEMAS = [
    {"type": "function", "function": {
        "name": "resumen_general",
        "description": "Panorama de la plataforma: grupos, personas, solicitudes de registro, postulaciones por "
                       "fase y convocatorias (activas, verificadas, próximas a cerrar).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "buscar_grupos",
        "description": "Busca grupos de investigación por nombre y/o facultad (parte del texto). Devuelve director, "
                       "correo, facultad, integrantes y solicitudes pendientes. Sin filtros devuelve los primeros.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Parte del nombre del grupo."},
            "facultad": {"type": "string", "description": "Parte del nombre de la facultad."},
            "sin_director": {"type": "boolean", "description": "true = solo grupos sin director o sin correo."}}}}},
    {"type": "function", "function": {
        "name": "detalle_grupo",
        "description": "Ficha de un grupo: datos, director/es, integrantes (con cargo y estado) y sus postulaciones.",
        "parameters": {"type": "object", "properties": {
            "nombre": {"type": "string", "description": "Nombre (o parte) del grupo."}}, "required": ["nombre"]}}},
    {"type": "function", "function": {
        "name": "buscar_personas",
        "description": "Busca personas (integrantes y directores) por nombre, correo o grupo.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Parte del nombre, correo o grupo."}}, "required": ["texto"]}}},
    {"type": "function", "function": {
        "name": "listar_postulaciones",
        "description": "Postulaciones con su convocatoria, grupo, quién la inició, fase y avance de requisitos. "
                       "Se puede filtrar por fase y por grupo.",
        "parameters": {"type": "object", "properties": {
            "fase": {"type": "string", "enum": list(FASES),
                     "description": "en_director, devuelta, en_vri (esperan a la VRI), aprobada o rechazada."},
            "grupo": {"type": "string", "description": "Parte del nombre del grupo."}}}}},
    {"type": "function", "function": {
        "name": "buscar_convocatorias",
        "description": "Convocatorias de la plataforma con entidad, fecha límite, montos y si están verificadas "
                       "(visto bueno de la VRI) y activas. Se puede filtrar por texto y por estado.",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Parte del nombre, entidad u objetivo."},
            "estado": {"type": "string", "enum": ["todas", "verificadas", "por_verificar", "vigentes", "cerradas"]}}}}},
    {"type": "function", "function": {
        "name": "detalle_convocatoria",
        "description": "Ficha de una convocatoria: objetivo, fechas, montos, requisitos, documentos y cuántas "
                       "postulaciones tiene.",
        "parameters": {"type": "object", "properties": {
            "nombre": {"type": "string", "description": "Nombre (o parte) de la convocatoria."}}, "required": ["nombre"]}}},
]


def _coincide(texto: str, *campos) -> bool:
    t = (texto or "").strip().lower()
    return not t or any(t in str(c or "").lower() for c in campos)


def _vigente(c: dict, hoy: str) -> bool:
    f = c.get("fecha_limite")
    return not f or str(f) >= hoy


def _cargo(p: dict) -> str:
    return "Director/a" if p.get("es_director_grupo") else "Integrante"


def crear_chat_vri(ctx: dict) -> ChatAgente:
    """Crea el asistente; ctx["con"] es la conexión de la petición en curso."""

    def con():
        return ctx["con"]

    def resumen_general() -> dict:
        c = con()
        hoy = date.today().isoformat()
        estudiantes = db.listar_estudiantes(c)
        grupos = db.listar_grupos(c)
        posts = db.listar_postulaciones(c)
        convs = db.listar_convocatorias(c, solo_activas=False)
        por_fase = {NOMBRES_FASE[f]: sum(1 for p in posts if p["fase"] == f) for f in FASES}
        proximas = sorted((x for x in convs if x["activa"] and x["verificada"] and x.get("fecha_limite")
                           and str(x["fecha_limite"]) >= hoy), key=lambda x: str(x["fecha_limite"]))[:5]
        return {
            "grupos": {"total": len(grupos), "sin_director_o_sin_correo": sum(1 for g in grupos if not g.get("director_correo")),
                       "con_solicitudes_pendientes": sum(1 for g in grupos if g.get("pendientes"))},
            "personas": {"total": len(estudiantes), "directores": sum(1 for p in estudiantes if p["es_director_grupo"]),
                         "desactivadas": sum(1 for p in estudiantes if not p["activo"])},
            "solicitudes_de_registro_pendientes": len(db.listar_solicitudes(c, "pendiente")),
            "postulaciones": {"total": len(posts), "por_fase": por_fase},
            "convocatorias": {"total": len(convs), "activas": sum(1 for x in convs if x["activa"]),
                              "verificadas": sum(1 for x in convs if x["verificada"]),
                              "por_verificar": sum(1 for x in convs if x["activa"] and not x["verificada"])},
            "proximas_a_cerrar": [{"nombre": x["nombre"], "fecha_limite": x["fecha_limite"]} for x in proximas],
        }

    def buscar_grupos(texto: str = "", facultad: str = "", sin_director: bool = False) -> dict:
        grupos = [g for g in db.listar_grupos(con())
                  if _coincide(texto, g["nombre"], g.get("codigo")) and _coincide(facultad, g.get("facultad"))
                  and (not sin_director or not g.get("director_correo"))]
        return {"total": len(grupos), "grupos": [
            {"nombre": g["nombre"], "facultad": g.get("facultad"), "director": g.get("director"),
             "correo_director": g.get("director_correo"), "integrantes": g["miembros"],
             "solicitudes_pendientes": g["pendientes"]} for g in grupos[:10]]}

    def _grupo(nombre: str):
        enc = [g for g in db.listar_grupos(con()) if _coincide(nombre, g["nombre"])]
        exacto = [g for g in enc if g["nombre"].lower() == (nombre or "").strip().lower()]
        return (exacto or enc)

    def detalle_grupo(nombre: str) -> dict:
        enc = _grupo(nombre)
        if not enc:
            return {"error": "No encontré un grupo con ese nombre."}
        if len(enc) > 1:
            return {"varios": [g["nombre"] for g in enc[:10]], "nota": "Hay varios: pide al usuario que precise."}
        g = enc[0]
        gente = [p for p in db.listar_estudiantes(con()) if p.get("grupo") == g["nombre"]]
        posts = db.listar_postulaciones(con(), grupo_id=g["id"])
        return {"nombre": g["nombre"], "facultad": g.get("facultad"), "descripcion": (g.get("descripcion") or "")[:300],
                "director": g.get("director"), "correo_director": g.get("director_correo"),
                "integrantes": [{"nombre": p["nombre"], "cargo": _cargo(p), "facultad": p.get("facultad"),
                                 "semillero": p.get("semillero"), "activo": p["activo"]} for p in gente[:25]],
                "postulaciones": [{"convocatoria": p["convocatoria_id"], "titulo": p.get("titulo"),
                                   "fase": NOMBRES_FASE[p["fase"]], "iniciada_por": p["estudiante"]} for p in posts[:15]]}

    def buscar_personas(texto: str) -> dict:
        gente = [p for p in db.listar_estudiantes(con()) if _coincide(texto, p["nombre"], p.get("correo"), p.get("grupo"))]
        return {"total": len(gente), "personas": [
            {"nombre": p["nombre"], "correo": p.get("correo"), "cargo": _cargo(p), "grupo": p.get("grupo"),
             "facultad": p.get("facultad"), "activo": p["activo"], "postulaciones": p["postulaciones"]}
            for p in gente[:10]]}

    def _nombres_convocatorias() -> dict:
        return {c["id"]: c["nombre"] for c in db.listar_convocatorias(con(), solo_activas=False)}

    def listar_postulaciones(fase: str = "", grupo: str = "") -> dict:
        nombres = _nombres_convocatorias()
        posts = [p for p in db.listar_postulaciones(con())
                 if (not fase or p["fase"] == fase) and _coincide(grupo, p.get("grupo"))]
        estados = db.estados_requisitos(con(), [p["id"] for p in posts[:15]])
        import requisitos
        convs = {c["id"]: c for c in db.listar_convocatorias(con(), solo_activas=False)}
        salida = []
        for p in posts[:15]:
            r = requisitos.resumen(estados.get(p["id"], {}), requisitos.lista_para(convs.get(p["convocatoria_id"])))
            salida.append({"convocatoria": nombres.get(p["convocatoria_id"], p["convocatoria_id"]), "grupo": p.get("grupo"),
                           "iniciada_por": p["estudiante"], "fase": NOMBRES_FASE[p["fase"]],
                           "requisitos_listos": f"{r['cumplidos']} de {r['total']}", "solicitada": str(p["creada"])[:10]})
        return {"total": len(posts), "mostrando": len(salida), "postulaciones": salida}

    def buscar_convocatorias(texto: str = "", estado: str = "todas") -> dict:
        hoy = date.today().isoformat()
        convs = [c for c in db.listar_convocatorias(con(), solo_activas=False)
                 if _coincide(texto, c.get("nombre"), c.get("entidad"), c.get("objetivo"))]
        if estado == "verificadas":
            convs = [c for c in convs if c["verificada"]]
        elif estado == "por_verificar":
            convs = [c for c in convs if c["activa"] and not c["verificada"]]
        elif estado == "vigentes":
            convs = [c for c in convs if c["activa"] and _vigente(c, hoy)]
        elif estado == "cerradas":
            convs = [c for c in convs if not _vigente(c, hoy)]
        return {"total": len(convs), "convocatorias": [
            {"nombre": c["nombre"], "entidad": c.get("entidad"), "fecha_limite": c.get("fecha_limite") or "sin fecha",
             "monto_max": c.get("monto_max"), "verificada": c["verificada"], "activa": c["activa"]} for c in convs[:10]]}

    def detalle_convocatoria(nombre: str) -> dict:
        convs = [c for c in db.listar_convocatorias(con(), solo_activas=False) if _coincide(nombre, c.get("nombre"))]
        exacto = [c for c in convs if c["nombre"].lower() == (nombre or "").strip().lower()]
        convs = exacto or convs
        if not convs:
            return {"error": "No encontré una convocatoria con ese nombre."}
        if len(convs) > 1:
            return {"varias": [c["nombre"] for c in convs[:10]], "nota": "Hay varias: pide al usuario que precise."}
        c = convs[0]
        n_post = sum(1 for p in db.listar_postulaciones(con()) if p["convocatoria_id"] == c["id"])
        return {"nombre": c["nombre"], "entidad": c.get("entidad"), "objetivo": (c.get("objetivo") or "")[:400],
                "fecha_limite": c.get("fecha_limite") or "sin fecha", "monto_min": c.get("monto_min"),
                "monto_max": c.get("monto_max"), "verificada": c["verificada"], "activa": c["activa"],
                "requisitos": (c.get("requisitos_lista") or [])[:12] or (c.get("requisitos") or "")[:400],
                "documentos": len(c.get("documentos") or []), "postulaciones": n_post,
                "fuente": c.get("fuente")}

    funciones = {"resumen_general": resumen_general, "buscar_grupos": buscar_grupos, "detalle_grupo": detalle_grupo,
                 "buscar_personas": buscar_personas, "listar_postulaciones": listar_postulaciones,
                 "buscar_convocatorias": buscar_convocatorias, "detalle_convocatoria": detalle_convocatoria}
    return ChatAgente(SISTEMA, funciones, ESQUEMAS)