"""Agente conversacional para estudiantes: guarda su perfil y recomienda convocatorias."""
from matching import cargar_convocatorias, rankear
from schema import CATEGORIAS, TIPO_FIJO, TIPOS_PERFIL
from llm import ChatAgente

if TIPO_FIJO:
    _QUIEN = "grupos de investigación"
    _PREGUNTAR = "área, alcance, monto en COP, fase del proyecto"
    _NOTA_TIPO = "- Todos los usuarios son grupos de investigación: nunca preguntes el tipo de beneficiario.\n"
else:
    _QUIEN = "integrantes de grupos de investigación (docentes, investigadores y estudiantes)"
    _PREGUNTAR = "tipo de beneficiario, área, alcance, monto en COP, fase del proyecto"
    _NOTA_TIPO = ""

SISTEMA = f"""Eres FOCU, el asistente de la Universidad del Cauca que ayuda a {_QUIEN}
a encontrar fuentes de financiación y oportunidades: proyectos de investigación, becas, movilidad,
posgrados y otros apoyos. No asumas que la persona tiene un proyecto: pregunta primero qué busca
(financiar un proyecto, una beca u otra oportunidad) y adapta las preguntas a eso.
Responde siempre en español.
{_NOTA_TIPO}- Si no conoces el perfil del usuario, pregúntale de forma breve ({_PREGUNTAR}
  y una descripción breve de lo que busca: su idea o proyecto, o la beca u oportunidad que le interesa)
  y guárdalo con guardar_perfil (la descripción va en descripcion_proyecto, con las palabras del usuario).
- Para recomendar, SIEMPRE usa buscar_convocatorias; no inventes convocatorias, montos ni fechas.
- Explica por qué cada opción encaja o no, qué requisitos debe cumplir y su fecha límite.
- FORMATO (la interfaz es un chat pequeño): responde corto, máximo unas 150 palabras. NO uses tablas,
  encabezados (#), líneas horizontales (---) ni emojis. Usa párrafos breves y, si hace falta, una lista
  con guiones (máximo 4 ítems) y **negrita** solo para nombres de convocatorias o cifras clave.
  Esa estructura (conclusión, motivos, sugerencia) SOLO cuando analices convocatorias. Si el usuario
  saluda o escribe algo breve, responde de forma natural en una o dos frases, sin buscar convocatorias
  ni analizar nada hasta que lo pida o tengas lo necesario. Responde a lo que realmente preguntó.
  No ofrezcas un menú de varias preguntas al final; haz como máximo una.
- La interfaz ya muestra un panel con el ranking completo, así que en tu respuesta de texto
  no repitas cada convocatoria con su porcentaje; concéntrate en explicar el "por qué" de las
  mejores opciones y en preguntar lo que falte del perfil.
- Si una convocatoria aparece como no verificada, avisa que sus datos fueron extraídos
  automáticamente y que conviene confirmarlos en la fuente oficial.
- Si un dato no aparece en los resultados, di que no está disponible y sugiere verificar la fuente.
- Al inicio de estas instrucciones, en "DATOS DEL USUARIO", está lo que esta persona ya registró.
  NUNCA le preguntes algo que ya figure ahí: úsalo directamente. Pregunta solo lo que falte
  y, si algún dato cambió o la persona lo corrige, actualízalo con guardar_perfil.
Categorías válidas: {CATEGORIAS}"""

ESQUEMAS = [
    {"type": "function", "function": {
        "name": "guardar_perfil",
        "description": ("Guarda o actualiza el perfil del usuario. Solo envía los campos que el "
                        "usuario haya mencionado. Monto en pesos colombianos (COP). "
                        "descripcion_proyecto es una descripción breve de la idea del usuario, "
                        "en sus propias palabras."),
        "parameters": {"type": "object", "properties": {
            **({} if TIPO_FIJO else {"tipo_beneficiario": {"type": "string", "enum": TIPOS_PERFIL}}),
            "area_conocimiento": {"type": "string", "enum": CATEGORIAS["area_conocimiento"]},
            "alcance": {"type": "string", "enum": CATEGORIAS["alcance"]},
            "fase_proyecto": {"type": "string", "enum": CATEGORIAS["fase_proyecto"]},
            "monto_requerido_cop": {"type": "integer", "description": "Monto en pesos colombianos."},
            "descripcion_proyecto": {"type": "string"},
        }},
    }},
    {"type": "function", "function": {
        "name": "obtener_perfil_usuario",
        "description": "Devuelve el perfil que el usuario ha proporcionado hasta ahora.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "buscar_convocatorias",
        "description": ("Compara el perfil actual con todas las convocatorias y devuelve el ranking: "
                        "elegibles primero, con % de coincidencia, detalle por campo y motivos de "
                        "exclusión."),
        "parameters": {"type": "object", "properties": {}},
    }},
]


def sistema_con_datos(nombre: str, grupo: str, facultad: str, perfil: dict) -> str:
    """Instrucciones del agente precedidas de lo que ya se sabe de la persona."""
    lineas = [f"- Nombre: {nombre}"]
    if grupo:
        lineas.append(f"- Grupo de investigación: {grupo}")
    if facultad:
        lineas.append(f"- Facultad: {facultad}")
    nombres = {"tipo_beneficiario": "Tipo de beneficiario", "area_conocimiento": "Área de conocimiento",
               "alcance": "Alcance", "fase_proyecto": "Fase del proyecto",
               "monto_requerido_cop": "Monto requerido (COP)", "descripcion_proyecto": "Descripción del proyecto"}
    faltan = []
    for campo, etiqueta in nombres.items():
        if campo == "tipo_beneficiario" and TIPO_FIJO:
            continue
        valor = perfil.get(campo)
        if valor:
            lineas.append(f"- {etiqueta}: {valor}")
        else:
            faltan.append(etiqueta)
    falta = ("Lo que aún falta preguntar: " + ", ".join(faltan) + "." if faltan
             else "El perfil está completo: no preguntes datos, ve directo a buscar_convocatorias o a responder.")
    return "DATOS DEL USUARIO (ya registrados):\n" + "\n".join(lineas) + "\n" + falta + "\n\n" + SISTEMA


def crear_chat(perfil: dict, resultados: list):
    """Crea el agente; perfil y resultados son compartidos con la sesión del estudiante."""

    def guardar_perfil(tipo_beneficiario: str = "", area_conocimiento: str = "",
                       alcance: str = "", fase_proyecto: str = "",
                       monto_requerido_cop=0, descripcion_proyecto: str = "") -> dict:
        if TIPO_FIJO:
            tipo_beneficiario = TIPO_FIJO
        nuevos = {"tipo_beneficiario": tipo_beneficiario, "area_conocimiento": area_conocimiento,
                  "alcance": alcance, "fase_proyecto": fase_proyecto}
        errores = []
        for campo, valor in nuevos.items():
            if not valor:
                continue
            if valor not in CATEGORIAS[campo]:
                errores.append(f"{campo}='{valor}' no es válido. Opciones: {CATEGORIAS[campo]}")
            else:
                perfil[campo] = valor
        try:
            monto = int(float(monto_requerido_cop or 0))
        except (TypeError, ValueError):
            monto = 0
            errores.append("monto_requerido_cop debe ser un número en COP.")
        if monto:
            perfil["monto_requerido_cop"] = monto
        if descripcion_proyecto:
            perfil["descripcion_proyecto"] = descripcion_proyecto
        return {"perfil_actual": dict(perfil), "errores": errores}

    def obtener_perfil_usuario() -> dict:
        return dict(perfil)

    def buscar_convocatorias() -> dict:
        if not perfil:
            return {"error": "Aún no hay perfil. Pregunta al usuario y usa guardar_perfil."}
        ranking = rankear(perfil, cargar_convocatorias())
        resultados.clear()
        resultados.extend(ranking)
        elegibles = [r for r in ranking if r["elegible"]][:6]
        return {
            "mejores_opciones": [{
                "nombre": r["nombre"], "entidad": r["entidad"],
                "fecha_limite": r["fecha_limite"], "porcentaje": r["porcentaje"],
                "datos_verificados": r.get("verificada", True),
                "detalle": r["detalle"], "requisitos": (r["requisitos"] or "")[:300],
            } for r in elegibles],
            "no_elegibles": [{"nombre": r["nombre"], "motivos": r["motivos_exclusion"]}
                             for r in ranking if not r["elegible"]],
        }

    funciones = {"guardar_perfil": guardar_perfil,
                 "obtener_perfil_usuario": obtener_perfil_usuario,
                 "buscar_convocatorias": buscar_convocatorias}
    return ChatAgente(SISTEMA, funciones, ESQUEMAS)