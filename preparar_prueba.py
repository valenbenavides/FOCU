"""Prepara una base de PRUEBA en tu computador con los datos reales del Excel.

Uso:
    python preparar_prueba.py focu_integrantes_para_cargar.xlsx tu_correo@gmail.com otro@gmail.com

- Carga el Excel completo (las personas sin correo quedan registradas pero no pueden ingresar).
- A cada correo que escribas le asigna una persona distinta del Excel (de grupos distintos) para que
  puedas ingresar como estudiante y ver su perfil, el ranking y las postulaciones.
- Para ingresar como DIRECTOR (VRI) pon tu correo en ADMIN_CORREO dentro de .env; se crea al arrancar.
Solo toca la base local (datos/focu.db si DATABASE_URL está vacío). No la uses sobre la base publicada.
"""
import sys

from dotenv import load_dotenv

load_dotenv()

import cargas  # noqa: E402
import db  # noqa: E402


def main(ruta: str, correos: list[str]) -> None:
    con = db.conectar()
    db.iniciar_db(con)
    r = cargas.procesar_excel(con, open(ruta, "rb").read())
    print(f"Excel cargado: {len(r['creados'])} personas nuevas, {len(r['actualizados'])} con correo completado, "
          f"{len(r['omitidos'])} omitidas.")
    grupos = con.execute(
        "SELECT g.id AS gid, g.nombre AS grupo FROM grupos g ORDER BY g.id").fetchall()
    usados = set()
    for i, mail in enumerate(correos):
        mail = mail.strip()
        if not cargas.RE_CORREO.match(mail):
            print(f"  ! '{mail}' no parece un correo; lo salto.")
            continue
        if con.execute("SELECT 1 AS x FROM usuarios WHERE LOWER(correo) = LOWER(?)", (mail,)).fetchone():
            print(f"  = {mail} ya está registrado.")
            continue
        for k in range(len(grupos)):
            g = grupos[(i + k) % len(grupos)]
            fila = con.execute(
                "SELECT u.id, u.nombre FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id "
                "WHERE s.grupo_id = ? AND u.correo IS NULL AND u.rol = 'estudiante' ORDER BY u.id LIMIT 1",
                (g["gid"],)).fetchone()
            if fila and fila["id"] not in usados:
                con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", (mail, fila["id"]))
                usados.add(fila["id"])
                print(f"  + {mail} entra como {fila['nombre']} (grupo: {g['grupo']})")
                break
    con.commit()
    print("Listo. Enciende el servidor con:  python -m uvicorn api:app --reload --port 8000")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
