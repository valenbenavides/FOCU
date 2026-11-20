# FOCU: Financiación de Oportunidades y Capacidades Universitarias

Plataforma de la Universidad del Cauca que relaciona grupos de investigación y sus integrantes con las convocatorias de financiación que les aplican, con un porcentaje de compatibilidad. El director del grupo y la VRI revisan y aprueban las postulaciones.

El detalle funcional, la arquitectura y las decisiones de diseño están en el informe del proyecto.

## Tecnología

- **Backend:** Python, FastAPI (`api.py`)
- **Frontend:** HTML, CSS y JavaScript sin librerías (`web/`), servido por el mismo backend
- **Base de datos:** SQLite en desarrollo, PostgreSQL en producción
- **IA:** Groq (asistente) y embeddings (Gemini o local) para el porcentaje de compatibilidad

## Ejecutarlo en Windows

1. Crea y activa el entorno virtual:
   ```
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
2. Instala las dependencias: `pip install -r requirements.txt`
3. Copia `.env.example` como `.env` y rellena los valores (las instrucciones están dentro del archivo).
4. Enciende el servidor: `python -m uvicorn api:app --reload --port 8000`
5. Abre <http://localhost:8000>. Con tu correo en `ADMIN_CORREO` entras como administrador. Sin proveedor de correo, el código de acceso se imprime en la terminal.

Para publicarlo, sigue `docs/GUIA_DESPLIEGUE.md`.

## Pruebas

```
python -m unittest discover -s tests -v
```