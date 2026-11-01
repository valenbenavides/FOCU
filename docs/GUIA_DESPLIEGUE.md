# Cómo publicar FOCU con un enlace que cualquiera pueda abrir

La idea: el programa deja de correr en tu computador y pasa a correr en un servicio de internet. Así, aunque apagues tu equipo, la página sigue funcionando.

Usamos dos servicios gratuitos:

| Servicio | Para qué | Por qué |
|---|---|---|
| **Neon** (neon.com) | Guardar los datos (PostgreSQL) | Los servidores gratuitos borran sus archivos al reiniciar; si la base estuviera en el servidor, perderías los estudiantes. |
| **Render** (render.com) | Ejecutar la página y el servidor | Publica directo desde GitHub y entrega una dirección pública con HTTPS. |

Las pantallas de estos servicios cambian con frecuencia: si algún botón se llama distinto, busca el equivalente.

## Paso 1. Crear la base de datos en Neon

1. Crea una cuenta en neon.com e inicia un proyecto nuevo (por ejemplo, `focu`).
2. En el panel del proyecto busca **Connection string** (cadena de conexión) y cópiala. Se ve así:
   `postgresql://usuario:clave@ep-algo.region.aws.neon.tech/neondb?sslmode=require`
3. Guárdala: es tu `DATABASE_URL`. Es secreta, como una contraseña.

No tienes que crear tablas: el programa las crea solo al arrancar.

## Paso 2. Subir el proyecto a GitHub

1. Crea una cuenta en github.com y un repositorio nuevo, **privado**, llamado `focu`.
2. En VS Code, abre una terminal en la carpeta del proyecto y escribe:
   ```
   git init
   git add .
   git commit -m "FOCU"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/focu.git
   git push -u origin main
   ```
3. Comprueba en GitHub que **no aparezca** un archivo `.env`. El `.gitignore` ya lo excluye, pero verifícalo: ahí están tus claves.

## Paso 3. Publicar en Render

1. Crea una cuenta en render.com y conéctala con tu GitHub.
2. Pulsa **New** → **Blueprint**, elige el repositorio `focu` y Render leerá el archivo `render.yaml`.
   Si prefieres hacerlo a mano: **New** → **Web Service**, elige el repositorio y rellena:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
3. En **Environment** (variables de entorno) agrega:

   | Variable | Valor |
   |---|---|
   | `SECRET_KEY` | Una clave larga al azar (Render puede generarla). **Después de crear estudiantes no la cambies.** |
   | `GROQ_API_KEY` | Tu clave de console.groq.com |
   | `DATABASE_URL` | La cadena de conexión de Neon (Paso 1) |
   | `ADMIN_CORREO` | El correo del primer director (VRI). Con él entras y te llega el código |
   | `BREVO_API_KEY` | Clave de la API de Brevo (brevo.com, gratis hasta 300 correos/día) para enviar los códigos y avisos |
   | `MAIL_FROM` | El remitente verificado en Brevo (ej. focu@tudominio.co) |

   > Render gratis bloquea los puertos SMTP, por eso se usa la API web de Brevo. Si la universidad te da un servidor SMTP, puedes usar `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` y `SMTP_PASS` en su lugar (revisa que el plan de Render no lo bloquee).
   | `EMBEDDINGS_PROVIDER` | `auto` (o `none` si no tienes clave de Gemini) |
   | `GEMINI_API_KEY` | Opcional: mejora el porcentaje de afinidad |
   | `PYTHON_VERSION` | `3.12.8` |

4. Pulsa **Deploy**. Tarda unos minutos. Cuando termine verás la dirección, parecida a `https://focu-xxxx.onrender.com`.

## Paso 4. Probar

1. Abre la dirección. La primera vez puede tardar hasta un minuto.
2. Escribe tu `ADMIN_CORREO` y pide el código: llega a tu correo. Si aún no configuraste el envío de correos, el código aparece en la pestaña **Logs** de Render.
3. Recorre el flujo completo:
   - Estudiantes → agrega a una persona con su correo.
   - Cierra sesión y entra con ese correo (te llega un código); acepta el aviso, llena el perfil y revisa las convocatorias.
   - Pulsa **Quiero postularme**; vuelve a entrar como administrador y aprueba la solicitud.
   - Convocatorias → **Actualizar desde las fuentes**, revisa el informe y pulsa **Dar visto bueno** en las que sirvan (los estudiantes solo ven las aprobadas).
   - Como estudiante, pide una postulación; como director, apruébala en Postulaciones.

## Lo que debes saber del plan gratuito

- **El servidor se duerme** tras unos 15 minutos sin visitas y tarda cerca de un minuto en despertar. Antes de una presentación, abre la página 5 minutos antes.
- **Tiene poca memoria (512 MB).** Por eso el proyecto calcula los embeddings con la API de Gemini (o solo con coincidencia de palabras) y no con un modelo local.
- **La recolección programada** (`SCRAPER_CADA_HORAS`) no corre mientras el servidor duerme. Usa el botón del panel.
- **Neon gratuito** también apaga el cómputo cuando no se usa; despierta solo con la primera consulta.
- Para un uso real, un plan pago de Render mantiene el servidor siempre encendido (algunas comparativas lo ubican cerca de 7 dólares al mes; confírmalo en render.com/pricing).

## Si prefieres otro servicio

El `Dockerfile` sirve para cualquier plataforma que acepte contenedores (Railway, Fly.io, Google Cloud Run, un servidor de la universidad…). Hugging Face Spaces ya no es gratuito para este tipo de aplicación. Recuerda que siempre hace falta una base PostgreSQL externa.

## Antes de usarlo con estudiantes reales

- La universidad debe **revisar y aprobar** `datos/aviso_datos.txt` (borrador).
- Decidan quién es dueño de las cuentas de Render, Neon y Groq: que no dependan de una cuenta personal.
- Usa como `ADMIN_CORREO` un correo institucional de la VRI al que solo ellos accedan.
- Programa copias de seguridad de la base (Neon ofrece restauración; revisa los límites de su plan gratuito).
- Calcula los costos mensuales (servidor, base, IA) para la propuesta.
