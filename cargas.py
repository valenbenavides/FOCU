"""Alta de integrantes: uno por uno o en lote desde un Excel (.xlsx).

Columnas del Excel (primera fila = encabezados): nombre, correo, semillero, grupo.
Obligatorias: nombre y grupo. El correo es opcional: quien no lo tiene queda registrada en su grupo, pero no puede
ingresar hasta que se complete (se puede editar a la persona o volver a subir el Excel con el correo: se
completa a quien ya existe, sin duplicarla). Semillero es opcional: si falta, la persona queda en
"General (sin semillero)" dentro de su grupo). Opcionales: facultad y descripcion, una frase breve del
tema de trabajo que sirve de punto de partida del perfil para el ranking): el correo es lo que la persona escribirá para ingresar (le
llega un código temporal) y el grupo es lo que valida que pertenece a la investigación.
No se entregan códigos aquí: cada persona pide el suyo al ingresar. Los repetidos se
omiten y se reportan."""
import re
from io import BytesIO

from openpyxl import load_workbook

import db
import seguridad

OBLIGATORIAS = ("nombre", "grupo")  # el correo es opcional: sin él la persona queda registrada pero aún no puede ingresar
SEMILLERO_GENERAL = "General (sin semillero)"
RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _placeholder() -> str:
    """Hash de un código que nadie conoce: el real se crea cuando la persona pide su código."""
    return seguridad.hash_codigo(seguridad.generar_codigo(), sal=seguridad.generar_codigo())



def agregar_estudiante(con, nombre: str, correo: str | None, semillero: str, grupo: str,
                       facultad: str = "", descripcion: str = "") -> dict:
    """Crea UN estudiante (y su grupo/semillero si no existen) con un código nuevo.
    No hace commit: lo hace quien la llama. Lanza ValueError si faltan datos o ya existe."""
    nombre, semillero, grupo = (nombre or "").strip(), (semillero or "").strip(), (grupo or "").strip()
    correo = (correo or "").strip()
    facultad, descripcion = (facultad or "").strip(), (descripcion or "").strip()[:2000]
    vacios = [c for c, v in (("nombre", nombre), ("grupo", grupo)) if not v]
    if vacios:
        raise ValueError(f"Falta: {', '.join(vacios)}.")
    if correo and not RE_CORREO.match(correo):
        raise ValueError("El correo no es válido.")

    semillero = semillero or SEMILLERO_GENERAL  # quien no está en un semillero queda en el general del grupo
    grupo_id = db.obtener_o_crear_grupo(con, grupo)
    semillero_id = db.obtener_o_crear_semillero(con, semillero, grupo_id)

    if correo and con.execute("SELECT 1 AS x FROM usuarios WHERE LOWER(correo) = LOWER(?)", (correo,)).fetchone():
        raise ValueError("Ese correo ya está registrado.")
    existente = con.execute(
        "SELECT id, correo FROM usuarios WHERE rol = 'estudiante' AND semillero_id = ? AND LOWER(nombre) = LOWER(?)",
        (semillero_id, nombre)).fetchone()
    if existente:
        if correo and not existente["correo"]:
            # Ya estaba registrada sin correo: se le completa, sin crear otra persona.
            con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", (correo, existente["id"]))
            return {"id": existente["id"], "nombre": nombre, "correo": correo, "semillero": semillero,
                    "grupo": grupo, "facultad": facultad, "actualizado": True}
        raise ValueError("Ya existe (mismo nombre en el semillero).")

    # La descripción breve queda como punto de partida del perfil (descripcion_proyecto): con ella
    # el ranking de convocatorias ya tiene afinidad temática. La persona puede editarla después.
    perfil = {"descripcion_proyecto": descripcion} if descripcion else None
    uid = db.insertar_usuario(con, nombre, correo, "estudiante", semillero_id, _placeholder(),
                              facultad=facultad or None, perfil=perfil)
    return {"id": uid, "nombre": nombre, "correo": correo, "semillero": semillero, "grupo": grupo,
            "facultad": facultad}


def agregar_administrador(con, nombre: str, correo: str) -> dict:
    """Crea una persona del equipo director (VRI). Entra con su correo, igual que los demás.
    No hace commit."""
    nombre, correo = (nombre or "").strip(), (correo or "").strip()
    if not nombre or not correo:
        raise ValueError("Faltan nombre y correo.")
    if not RE_CORREO.match(correo):
        raise ValueError("El correo no es válido.")
    if con.execute("SELECT 1 AS x FROM usuarios WHERE LOWER(correo) = LOWER(?)", (correo,)).fetchone():
        raise ValueError("Ese correo ya está registrado.")
    uid = db.insertar_usuario(con, nombre, correo, "admin", None, _placeholder(), consentimiento=1)
    return {"id": uid, "nombre": nombre, "correo": correo}


def _celda(fila, cols: dict, nombre: str) -> str:
    i = cols.get(nombre)
    if i is None or i >= len(fila) or fila[i] is None:
        return ""
    return str(fila[i]).strip()


def procesar_excel(con, contenido: bytes) -> dict:
    """Devuelve {'creados': [...], 'actualizados': [...], 'omitidos': [{'fila', 'motivo'}]}.
    Lanza ValueError si el archivo no se puede leer o faltan columnas."""
    try:
        hoja = load_workbook(BytesIO(contenido), read_only=True, data_only=True).worksheets[0]
        filas = hoja.iter_rows(values_only=True)
        encabezado = next(filas, None)
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo Excel: {e}")
    if not encabezado:
        raise ValueError("El archivo está vacío.")

    cols = {str(v).strip().lower(): i for i, v in enumerate(encabezado) if v is not None}
    faltan = [c for c in OBLIGATORIAS if c not in cols]
    if faltan:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(faltan)}. "
                         "Usa la plantilla (nombre y grupo; correo, semillero, facultad y descripcion son opcionales).")

    creados, actualizados, omitidos = [], [], []
    for n, fila in enumerate(filas, start=2):
        if all(v is None or str(v).strip() == "" for v in fila):
            continue  # fila vacía
        nombre = _celda(fila, cols, "nombre")
        if nombre.lower().startswith("ejemplo"):
            omitidos.append({"fila": n, "motivo": "Fila de ejemplo."})
            continue
        try:
            r = agregar_estudiante(
                con, nombre, _celda(fila, cols, "correo"),
                _celda(fila, cols, "semillero"), _celda(fila, cols, "grupo"),
                _celda(fila, cols, "facultad"), _celda(fila, cols, "descripcion"))
            (actualizados if r.get("actualizado") else creados).append(r)
        except ValueError as e:
            omitidos.append({"fila": n, "motivo": str(e)})

    con.commit()
    return {"creados": creados, "actualizados": actualizados, "omitidos": omitidos}
