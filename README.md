# FOCU: Financiación de Oportunidades y Capacidades Universitarias

Plataforma de la Universidad del Cauca para que semilleros y grupos de investigación encuentren las convocatorias de financiación que realmente les aplican, con un **porcentaje de compatibilidad**, y para que la **VRI** revise y apruebe las solicitudes de postulación.

## Qué hace

**Estudiante (con su correo):**
1. Escribe su correo (debe estar registrado en un grupo de investigación), recibe un código temporal de 6 dígitos y acepta el aviso de datos personales.
2. Llena su perfil (tipo de beneficiario, área, fase, alcance, monto y descripción), o lo va contando al asistente de IA.
3. Ve el ranking de convocatorias con su porcentaje, los requisitos y por qué sacó ese porcentaje.
4. Pulsa **¿Qué me falta?** para ver las diferencias con la convocatoria y una recomendación de la IA.
5. Pulsa **Quiero postularme** y la solicitud llega a la VRI. Después ve la respuesta en *Mis postulaciones*.

**Administrador (VRI):**
- **Postulaciones:** ve las solicitudes de los estudiantes y las aprueba o rechaza con un comentario.
- **Estudiantes:** agrega a una persona a mano (nombre y grupo; el correo, que puede faltar, es lo que le permite ingresar), la edita, la cambia de semillero o desactiva su acceso.
- **Cargar Excel:** sube la plantilla con grupos, semilleros y estudiantes; el sistema registra a cada integrante. Nadie recibe contraseñas: cada uno pide su código temporal por correo al ingresar.
- **Convocatorias:** actualiza el catálogo desde las fuentes oficiales con un botón, agrega convocatorias a mano, da el **visto bueno** (los estudiantes solo ven las convocatorias con visto bueno) y oculta las que no sirven.
- **Postulaciones:** ve quién se postula, a qué convocatoria, y da el visto bueno o la rechaza; el estudiante recibe un correo con la decisión. Cada solicitud nueva avisa por correo a los directores.
- **Directores:** agrega a otras personas de la VRI.

## Cómo está hecho

| Parte | Archivos | Con qué |
|---|---|---|
| **Página (frontend)** | `web/` | HTML, CSS y JavaScript sin librerías, con los colores y la letra de la Universidad del Cauca |
| **Servidor y API (backend)** | `api.py` | Python con FastAPI |
| **Base de datos** | `db.py` | SQLite para desarrollar y PostgreSQL (Neon) para publicar |
| **Acceso por código y roles** | `seguridad.py` | Códigos temporales por correo (HMAC, 15 min, un solo uso) y tokens JWT |
| **Carga desde Excel** | `cargas.py`, `plantilla_semilleros.xlsx` | openpyxl |
| **Porcentaje de compatibilidad** | `matching.py` | Reglas exactas + embeddings + coincidencia de palabras |
| **Asistente de IA** | `agente.py`, `llm.py` | Llama a través de Groq, con herramientas |
| **Recolección de convocatorias** | `scrapers/` | requests, BeautifulSoup y la IA para leer cada página |

El servidor entrega la página en `/` y la API en `/api`, así que todo vive en **un solo servicio y una sola dirección**.

```
focu/
├── api.py                 servidor: rutas de la API y entrega de la página
├── db.py                  base de datos (SQLite o PostgreSQL)
├── seguridad.py           códigos temporales, tokens, límite de intentos
├── acceso.py              emitir y verificar el código enviado por correo
├── correo.py              envío de correos (Brevo, SMTP o consola)
├── cargas.py              alta de estudiantes (uno a uno o por Excel)
├── convocatorias.py       formato único de convocatoria
├── matching.py            porcentaje de compatibilidad y brechas
├── agente.py, llm.py      asistente de IA
├── schema.py              opciones válidas (áreas, fases, tipos...)
├── scrapers/              recolector de convocatorias y lista de fuentes (fuentes.json)
├── web/                   página (index.html, styles.css, app.js)
├── datos/                 convocatorias de ejemplo y aviso de datos personales
├── tests/                 pruebas automáticas
├── docs/                  cronograma, guía de despliegue y contrato de la API
├── Dockerfile, render.yaml, requirements.txt, .env.example
└── plantilla_semilleros.xlsx
```

## Ejecutarlo en tu computador (Windows)

1. Abre la carpeta del proyecto en VS Code y una terminal (PowerShell).
2. Crea y activa el entorno virtual:
   ```
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
   Si PowerShell dice que los scripts están deshabilitados, ejecuta una vez:
   `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
3. Instala lo necesario: `pip install -r requirements.txt`
4. Copia `.env.example` como `.env` y rellena al menos `SECRET_KEY` y `GROQ_API_KEY` (las instrucciones están dentro del archivo).
5. Enciende el servidor: `python -m uvicorn api:app --reload --port 8000`
6. Abre <http://localhost:8000>. Pon tu correo en `ADMIN_CORREO` del `.env`: con él entras como director. Sin proveedor de correo configurado, el código de acceso se imprime en la terminal.

Para una primera prueba con tus propios correos y los datos reales, sigue `docs/PRUEBA_LOCAL.md`.

Con esto la base es SQLite (`datos/focu.db`). Para que otras personas lo vean desde internet, sigue `docs/GUIA_DESPLIEGUE.md`.

Tus propias convocatorias: si tienes un `datos/convocatorias.json`, el sistema lo usa para cargar el catálogo la primera vez; si no, carga `datos/convocatorias_ejemplo.json`. Después se administran desde el panel.

## Pruebas

```
python -m unittest discover -s tests -v
```

## Qué está probado y qué no

**Probado con pruebas automáticas (29):** base de datos, códigos y tokens, límite de intentos, alta de estudiantes (individual y por Excel, con repetidos y archivos dañados), administradores, códigos nuevos, listados, formato de convocatorias, verificación sin que el recolector pise lo revisado, ranking con porcentaje, postulaciones, ingreso por correo (código vencido, de un solo uso, límite de intentos), y la lógica del recolector con páginas simuladas. La sintaxis del JavaScript también se revisó.

**Escrito pero NO ejecutado todavía (probar antes de presentar):**
- El servidor FastAPI de punta a punta y la página en un navegador.
- La conexión a PostgreSQL (Neon).
- La lectura de las páginas reales de convocatorias: los selectores y la IA deben ajustarse al ver los resultados.
- El chat y las recomendaciones con Groq, y los embeddings con Gemini.

## Límites y pendientes conocidos

- **Fuente de convocatorias:** solo Minciencias (https://minciencias.gov.co/convocatorias/todas), por decisión del equipo. Las recolectadas quedan **sin visto bueno** y los estudiantes no las ven hasta que la VRI las revise.
- **Detección de brechas:** se dejó fuera por ahora (la función existe en `matching.py`, pero no está en la página).
- **Postulación por estudiante:** hoy cada solicitud es de una persona. Si la postulación real la hace el semillero o el grupo, hay que cambiar el diseño.
- **Aviso de datos personales:** `datos/aviso_datos.txt` es un **borrador** que debe revisar la universidad.
- **Asistente:** las conversaciones viven en la memoria del servidor; si se reinicia, el chat empieza de cero (el perfil y las postulaciones se conservan).
- **Servicios gratuitos:** el servidor se duerme tras unos minutos sin visitas y la primera carga puede tardar cerca de un minuto. Para un uso real conviene un plan pago.
- **Logo:** copia tu imagen en `web/assets/logo_unicauca.png` (si no está, la página funciona sin él).
- **Costos de operación** (base de datos, servidor, IA) deben quedar incluidos en la propuesta a la universidad.
