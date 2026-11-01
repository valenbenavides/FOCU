# Primera prueba en tu computador (Windows)

Objetivo: comprobar de punta a punta que se puede ingresar con correo y código, y que el director da el visto bueno.
Usa **tus propios correos**; no hace falta tener los de nadie más.

## 1. Preparar

1. Descomprime el proyecto, abre la carpeta en VS Code y una terminal (PowerShell).
2. Pasos 2 a 4 de "Ejecutarlo en tu computador" del README (entorno virtual, `pip install -r requirements.txt`, copiar `.env.example` como `.env`).
3. En `.env` pon como mínimo:
   - `SECRET_KEY` (el comando para generarla está dentro del archivo)
   - `ADMIN_CORREO=` tu correo principal (con él entras como **director**)
   - `GROQ_API_KEY=` si quieres probar también el chat
4. Copia al lado del proyecto el Excel `focu_integrantes_para_cargar.xlsx`.
5. Carga los datos reales y reparte tus correos entre personas del Excel:
   ```
   python preparar_prueba.py focu_integrantes_para_cargar.xlsx tucorreo2@gmail.com tucorreo3@gmail.com
   ```
   Cada correo queda como una persona distinta, de grupos distintos (te dice cuál). Con ellos pruebas como **estudiante**.
   Si quieres empezar de cero, borra `datos/focu.db` y repite el paso.
6. Enciende el servidor: `python -m uvicorn api:app --reload --port 8000` y abre <http://localhost:8000>.

## 2. El código de acceso

- **Sin configurar correo:** el código aparece en la terminal donde corre el servidor (sirve para probar la lógica).
- **Con Gmail, para probar el envío real:** en `.env` pon
  `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=tucorreo@gmail.com`, `SMTP_PASS=` una *contraseña de aplicación*
  de Google (Cuenta de Google > Seguridad > Verificación en dos pasos > Contraseñas de aplicaciones) y `MAIL_FROM=tucorreo@gmail.com`.
  Reinicia el servidor.

## 3. Qué comprobar

| # | Prueba | Debe pasar |
|---|--------|-----------|
| 1 | Entrar con un correo que **no** está en la base | Sale el mismo mensaje neutro; no llega código |
| 2 | Entrar con tu `ADMIN_CORREO` | Llega el código; entras al panel de director |
| 3 | Pedir un código y escribirlo mal 5 veces | Se bloquea ese código |
| 4 | Entrar como estudiante (uno de tus correos) | Ves tus convocatorias y el porcentaje de afinidad |
| 5 | Como estudiante, ver la lista de convocatorias | Solo aparecen las que tienen visto bueno |
| 6 | Como director, dar visto bueno a una convocatoria | Ahora la ve el estudiante |
| 7 | Como estudiante, postularte | Llega correo al director; queda "pendiente" |
| 8 | Como director, aprobar o rechazar | Llega correo al estudiante con la decisión |
| 9 | Director: subir el Excel de nuevo | Dice cuántos se omitieron; nadie se duplica |
| 10 | Director: pestaña de integrantes | Se ven 690 personas; las que no tienen correo, sin poder ingresar |

Anota lo que falle (pantalla y mensaje de la terminal) y me lo pasas: con eso lo arreglo.
