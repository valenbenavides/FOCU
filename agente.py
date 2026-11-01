"""Agente conversacional (Groq / Llama). Las tres funciones de abajo son las
herramientas: el modelo decide cuándo llamarlas y ChatAgente ejecuta el ciclo
(llamada -> resultado -> respuesta final) automáticamente."""
from matching import cargar_convocatorias, rankear
from schema import CATEGORIAS
from llm import ChatAgente

SISTEMA = f"""Eres FOCU, el asistente de la Universidad del Cauca que ayuda a estudiantes,
docentes, investigadores y emprendedores a encontrar fuentes de financiación.
Responde siempre en español.
- Si no conoces el perfil del usuario, pregúntale de forma breve (tipo de beneficiario, área,
  alcance, monto en COP, fase del proyecto y una descripción breve de la idea o proyecto)
  y guárdalo con guardar_perfil (la descripción va en descripcion_proyecto, con las palabras del usuario).
- Para recomendar, SIEMPRE usa buscar_convocatorias; no inventes convocatorias, montos ni fechas.
- Explica por qué cada opción encaja o no, qué requisitos debe cumplir y su fecha límite.
- La interfaz ya muestra un panel con el ranking completo, así que en tu respuesta de texto
  no repitas cada convocatoria con su porcentaje; concéntrate en explicar el "por qué" de las
  mejores opciones y en preguntar lo que falte del perfil.
- Si una convocatoria aparece como no verificada, avisa que sus datos fueron extraídos
  automáticamente y que conviene confirmarlos en la fuente oficial.
- Si un dato no aparece en los resultados, di que no está disponible y sugiere verificar la fuente.
Categorías válidas: {CATEGORIAS}"""

# Esquemas de las herramientas (formato OpenAI). Los enum evitan valores inválidos.
ESQUEMAS = [
    {"type": "function", "function": {
        "name": "guardar_perfil",
        "description": ("Guarda o actualiza el perfil del usuario. Solo envía los campos que el "
                        "usuario haya mencionado. Monto en pesos colombianos (COP). "
                        "descripcion_proyecto es una descripción breve de la idea del usuario, "
                        "en sus propias palabras."),
        "parameters": {"type": "object", "properties": {
            "tipo_beneficiario": {"type": "string", "enum": CATEGORIAS["tipo_beneficiario"]},
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


def crear_chat(perfil: dict, resultados: list):
    """`perfil` y `resultados` son un dict y una lista compartidos (la sesión del
    estudiante) que las herramientas leen y escriben directamente."""

    def guardar_perfil(tipo_beneficiario: str = "", area_conocimiento: str = "",
                       alcance: str = "", fase_proyecto: str = "",
                       monto_requerido_cop=0, descripcion_proyecto: str = "") -> dict:
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
        try:  # los modelos a veces envían el monto como texto
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
        resultados.extend(ranking)  # el ranking completo queda disponible para la interfaz

        # Al modelo solo se le manda un resumen: así se gastan menos tokens
        # (importante en la capa gratuita) y responde más rápido.
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
