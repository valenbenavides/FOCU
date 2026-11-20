"""Envía un correo de prueba con la configuración del .env: python probar_correo.py destino@correo.com"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import correo

if len(sys.argv) < 2 or "@" not in sys.argv[1]:
    sys.exit("Uso: python probar_correo.py destino@correo.com")
destino = sys.argv[1].strip()

if os.environ.get("BREVO_API_KEY") and os.environ.get("MAIL_FROM"):
    proveedor = "Brevo (API web)"
elif os.environ.get("SMTP_HOST") and os.environ.get("MAIL_FROM"):
    proveedor = f"SMTP ({os.environ['SMTP_HOST']}:{os.environ.get('SMTP_PORT', '587')})"
else:
    proveedor = None
    print("No hay proveedor de correo configurado en .env.")
    print("Falta: BREVO_API_KEY + MAIL_FROM, o bien SMTP_HOST + SMTP_PORT + SMTP_USER + SMTP_PASS + MAIL_FROM.")
    print("Sin eso, FOCU solo imprime los correos en la terminal (modo desarrollo).\n")

if proveedor:
    print(f"Proveedor: {proveedor}\nRemitente: {os.environ['MAIL_FROM']}\nDestino:   {destino}\nEnviando…")
ok = correo._enviar_ahora(destino, "Prueba de correo de FOCU",
                          "Hola,\n\nSi lees esto, FOCU ya puede enviar los códigos de ingreso por correo.\n\n"
                          "Universidad del Cauca · FOCU")
if proveedor and ok:
    print("\nListo: el servidor aceptó el correo. Revisa la bandeja de entrada y también el correo no deseado.")
elif proveedor:
    print("\nNo se pudo enviar. El motivo salió arriba, en la línea que empieza con [correo].")
    print("Causas comunes: clave mal copiada, puerto bloqueado por el hosting o remitente sin verificar en Brevo.")