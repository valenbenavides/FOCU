"""Capa de IA basada en Groq (API compatible con OpenAI, con capa gratuita).
- generar_texto(): una respuesta simple (usada para las recomendaciones).
- generar_json(): respuesta en JSON (usada por el recolector de convocatorias).
- ChatAgente: conversación con herramientas (function calling): send_message(texto).text

Si el modelo principal está saturado o sin cuota, reintenta y luego pasa
automáticamente al modelo de respaldo."""
import json
import os
import re
import time

# Principal: más inteligente. Respaldo: más rápido y con mucha más cuota diaria.
MODELO_PRINCIPAL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MODELO_RESPALDO = os.environ.get("GROQ_MODEL_RESPALDO", "llama-3.1-8b-instant")
MODELOS = list(dict.fromkeys([MODELO_PRINCIPAL, MODELO_RESPALDO]))
# Para extraer datos de páginas se usa primero el modelo rápido: gasta mucha menos cuota.
MODELOS_EXTRACCION = list(dict.fromkeys([MODELO_RESPALDO, MODELO_PRINCIPAL]))

_client = None


def obtener_cliente():
    """Un solo cliente compartido, creado la primera vez que se necesita."""
    global _client
    if _client is None:
        from openai import OpenAI  # se importa aquí para que el resto funcione sin la librería
        _client = OpenAI(api_key=os.environ["GROQ_API_KEY"],
                         base_url="https://api.groq.com/openai/v1")
    return _client


def es_error_temporal(e: Exception) -> bool:
    """True si el error es de saturación o límite de uso (vale la pena reintentar)."""
    t = str(e).lower()
    return any(c in t for c in ("503", "429", "unavailable", "rate limit", "rate_limit",
                                "overloaded", "capacity"))


def _llamar(modelos=None, **kwargs):
    """Llama al modelo con 2 intentos por modelo y cambia al siguiente si falla."""
    ultimo_error = None
    for modelo in (modelos or MODELOS):
        for i in range(2):
            try:
                return obtener_cliente().chat.completions.create(model=modelo, **kwargs)
            except Exception as e:
                ultimo_error = e
                if es_error_temporal(e):
                    time.sleep(2 ** i)
                    continue
                break  # error no temporal: prueba el siguiente modelo
    raise ultimo_error


def generar_texto(prompt: str) -> str:
    r = _llamar(messages=[{"role": "user", "content": prompt}], temperature=0.4)
    return (r.choices[0].message.content or "").strip()


def generar_json(prompt: str) -> dict:
    """Pide una respuesta en JSON y la convierte en dict. Lanza ValueError si no es JSON válido."""
    r = _llamar(modelos=MODELOS_EXTRACCION, messages=[{"role": "user", "content": prompt}],
                temperature=0, response_format={"type": "json_object"})
    texto = (r.choices[0].message.content or "").strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.M).strip()
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as e:
        raise ValueError(f"La IA no devolvió un JSON válido: {e}")
    if not isinstance(datos, dict):
        raise ValueError("La IA no devolvió un objeto JSON.")
    return datos


class RespuestaChat:
    def __init__(self, text: str):
        self.text = text


class ChatAgente:
    """Chat con herramientas. `funciones` es {nombre: callable}; `esquemas` es la
    lista de herramientas en formato OpenAI (nombre, descripción y parámetros)."""

    MAX_VUELTAS = 6  # tope de ciclos modelo -> herramienta -> modelo

    def __init__(self, sistema: str, funciones: dict, esquemas: list):
        self.funciones = funciones
        self.esquemas = esquemas
        self.mensajes = [{"role": "system", "content": sistema}]

    def _ejecutar(self, tc) -> dict | list:
        nombre = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            return {"error": "Argumentos inválidos."}
        if nombre not in self.funciones:
            return {"error": f"Herramienta desconocida: {nombre}"}
        try:
            return self.funciones[nombre](**args)
        except Exception as e:
            return {"error": f"Falló la herramienta {nombre}: {e}"}

    def send_message(self, texto: str) -> RespuestaChat:
        self.mensajes.append({"role": "user", "content": texto})
        inicio = len(self.mensajes) - 1
        try:
            for _ in range(self.MAX_VUELTAS):
                r = _llamar(messages=self.mensajes, tools=self.esquemas,
                            tool_choice="auto", temperature=0.3)
                msg = r.choices[0].message
                if not msg.tool_calls:
                    respuesta = (msg.content or "").strip()
                    self.mensajes.append({"role": "assistant", "content": respuesta})
                    return RespuestaChat(respuesta)

                self.mensajes.append({
                    "role": "assistant", "content": msg.content or "",
                    "tool_calls": [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.function.name,
                                                 "arguments": tc.function.arguments or "{}"}}
                                   for tc in msg.tool_calls],
                })
                for tc in msg.tool_calls:
                    self.mensajes.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps(self._ejecutar(tc), ensure_ascii=False, default=str),
                    })
            return RespuestaChat("No logré completar la consulta. ¿Puedes reformular tu pregunta?")
        except Exception:
            # Si algo falla, se descarta este turno para no dejar el historial roto.
            del self.mensajes[inicio:]
            raise
