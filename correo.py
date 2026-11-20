"""Envío de correos en segundo plano, sin romper la petición si fallan.

Proveedor según el entorno: Brevo (BREVO_API_KEY + MAIL_FROM), SMTP (SMTP_HOST + MAIL_FROM) o,
si no hay ninguno, se imprime en la consola del servidor."""
import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr

import requests


def _enviar_ahora(destino: str, asunto: str, cuerpo: str) -> bool:
    remitente = os.environ.get("MAIL_FROM", "").strip()
    try:
        if os.environ.get("BREVO_API_KEY") and remitente:
            r = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json"},
                json={"sender": {"name": "FOCU - Universidad del Cauca", "email": remitente},
                      "to": [{"email": destino}], "subject": asunto, "textContent": cuerpo},
                timeout=20)
            r.raise_for_status()
            return True
        if os.environ.get("SMTP_HOST") and remitente:
            msg = EmailMessage()
            msg["From"] = formataddr(("FOCU - Universidad del Cauca", remitente))
            msg["To"], msg["Subject"] = destino, asunto
            msg.set_content(cuerpo)
            puerto = int(os.environ.get("SMTP_PORT", "587"))
            if puerto == 465:
                srv = smtplib.SMTP_SSL(os.environ["SMTP_HOST"], puerto, timeout=20)
            else:
                srv = smtplib.SMTP(os.environ["SMTP_HOST"], puerto, timeout=20)
                srv.starttls()
            with srv:
                if os.environ.get("SMTP_USER"):
                    srv.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                srv.send_message(msg)
            return True
    except Exception as e:
        print(f"[correo] No se pudo enviar a {destino}: {e}")
        return False
    print(f"[correo] (modo desarrollo, sin proveedor configurado)\n  Para: {destino}\n"
          f"  Asunto: {asunto}\n  {cuerpo}\n")
    return True


def enviar(destino: str, asunto: str, cuerpo: str) -> None:
    """Envía en segundo plano."""
    if destino:
        threading.Thread(target=_enviar_ahora, args=(destino, asunto, cuerpo), daemon=True).start()


def enviar_a_varios(destinos: list[str], asunto: str, cuerpo: str) -> None:
    for d in dict.fromkeys(destinos):
        enviar(d, asunto, cuerpo)