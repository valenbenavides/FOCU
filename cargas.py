"""Alta de integrantes y directores, uno por uno o en lote desde la plantilla."""
import re
import unicodedata
from io import BytesIO

from openpyxl import load_workbook

import db
import seguridad
from schema import AREAS, ETIQUETAS_AREA, FACULTADES

OBLIGATORIAS = ("nombre", "grupo")
SEMILLERO_GENERAL = "General (sin semillero)"
RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _placeholder() -> str:
    """Hash de un código que nadie conoce; el real se crea cuando la persona lo pide."""
    return seguridad.hash_codigo(seguridad.generar_codigo(), sal=seguridad.generar_codigo())


def grupo_existente(con, nombre: str) -> dict | None:
    """Busca un grupo por nombre sin importar mayúsculas, tildes ni espacios."""
    for f in con.execute("SELECT id, nombre FROM grupos").fetchall():
        if mismo_texto(f["nombre"], nombre):
            return dict(f)
    return None


def agregar_estudiante(con, nombre: str, correo: str | None, semillero: str, grupo: str,
                       facultad: str = "", descripcion: str = "", crear_grupo: bool = True) -> dict:
    """Crea un estudiante y su semillero si no existe. No hace commit. ValueError si falta algo o ya existe."""
    nombre, semillero, grupo = (nombre or "").strip(), (semillero or "").strip(), (grupo or "").strip()
    correo = (correo or "").strip()
    facultad, descripcion = (facultad or "").strip(), (descripcion or "").strip()[:2000]
    vacios = [c for c, v in (("nombre", nombre), ("grupo", grupo)) if not v]
    if vacios:
        raise ValueError(f"Falta: {', '.join(vacios)}.")
    if correo and not RE_CORREO.match(correo):
        raise ValueError("El correo no es válido.")

    semillero = semillero or SEMILLERO_GENERAL
    if crear_grupo:
        grupo_id = db.obtener_o_crear_grupo(con, grupo)
    else:
        g = grupo_existente(con, grupo)
        if not g:
            raise ValueError(f"El grupo «{grupo}» no existe. Elígelo de la lista; si falta, créalo primero en la pestaña Grupos.")
        grupo_id, grupo = g["id"], g["nombre"]
    semillero_id = db.obtener_o_crear_semillero(con, semillero, grupo_id)

    if correo and db.usuario_en_grupo_por_correo(con, correo, grupo_id):
        raise ValueError("Ese correo ya está registrado en este grupo.")
    existente = con.execute(
        "SELECT id, correo FROM usuarios WHERE rol = 'estudiante' AND semillero_id = ? AND LOWER(nombre) = LOWER(?)",
        (semillero_id, nombre)).fetchone()
    if existente:
        if correo and not existente["correo"]:
            con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", (correo, existente["id"]))
            return {"id": existente["id"], "nombre": nombre, "correo": correo, "semillero": semillero,
                    "grupo": grupo, "facultad": facultad, "actualizado": True}
        raise ValueError("Ya existe (mismo nombre en el semillero).")

    perfil = {"descripcion_proyecto": descripcion} if descripcion else None
    uid = db.insertar_usuario(con, nombre, correo, "estudiante", semillero_id, _placeholder(),
                              facultad=facultad or None, perfil=perfil)
    return {"id": uid, "nombre": nombre, "correo": correo, "semillero": semillero, "grupo": grupo,
            "facultad": facultad}


def agregar_administrador(con, nombre: str, correo: str) -> dict:
    """Crea una persona de la VRI. No hace commit."""
    nombre, correo = (nombre or "").strip(), (correo or "").strip()
    if not nombre or not correo:
        raise ValueError("Faltan nombre y correo.")
    if not RE_CORREO.match(correo):
        raise ValueError("El correo no es válido.")
    if db.usuario_en_grupo_por_correo(con, correo, None):
        raise ValueError("Ese correo ya es administrador de la VRI.")
    uid = db.insertar_usuario(con, nombre, correo, "admin", None, _placeholder(), consentimiento=1)
    return {"id": uid, "nombre": nombre, "correo": correo}


def _celda(fila, cols: dict, nombre: str) -> str:
    i = cols.get(nombre)
    if i is None or i >= len(fila) or fila[i] is None:
        return ""
    return str(fila[i]).strip()


def procesar_excel(con, contenido: bytes) -> dict:
    """Carga personas desde la plantilla y devuelve creados, actualizados y omitidos. ValueError si no se puede leer."""
    try:
        hoja = load_workbook(BytesIO(contenido), read_only=True, data_only=True).worksheets[0]
        filas = hoja.iter_rows(values_only=True)
        encabezado = next(filas, None)
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo: {e}")
    if not encabezado:
        raise ValueError("El archivo está vacío.")

    cols = {str(v).strip().lower(): i for i, v in enumerate(encabezado) if v is not None}
    faltan = [c for c in OBLIGATORIAS if c not in cols]
    if faltan:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(faltan)}. "
                         "Usa la plantilla (nombre, correo y grupo son obligatorios; semillero, cargo, facultad y descripcion son opcionales).")

    creados, actualizados, omitidos = [], [], []
    for n, fila in enumerate(filas, start=2):
        if all(v is None or str(v).strip() == "" for v in fila):
            continue
        nombre = _celda(fila, cols, "nombre")
        if nombre.lower().startswith("ejemplo"):
            omitidos.append({"fila": n, "motivo": "Fila de ejemplo."})
            continue
        faltan_fila = [c for c in ("nombre", "correo", "grupo")
                       if c in cols and not _celda(fila, cols, c)]
        if faltan_fila:
            omitidos.append({"fila": n, "motivo": "Falta: " + ", ".join(faltan_fila) + " (son obligatorios)."})
            continue
        cargo = _sin_tildes(limpio(_celda(fila, cols, "cargo")))
        if cargo not in ("", "estudiante", "director", "directora", "director/a"):
            omitidos.append({"fila": n, "motivo": "Cargo no válido: elige Estudiante o Director/a de la lista."})
            continue
        es_director = cargo.startswith("director")
        try:
            if es_director:
                g = grupo_existente(con, _celda(fila, cols, "grupo"))
                if not g:
                    raise ValueError(f"El grupo «{_celda(fila, cols, 'grupo')}» no existe. Elígelo de la lista; "
                                     "si falta, créalo primero en la pestaña Grupos.")
                r = asignar_director(con, g["id"], nombre, _celda(fila, cols, "correo"), adicional=True,
                                     semillero=_celda(fila, cols, "semillero"))
                r["semillero"] = _celda(fila, cols, "semillero") or SEMILLERO_GENERAL
            else:
                r = agregar_estudiante(
                    con, nombre, _celda(fila, cols, "correo"),
                    _celda(fila, cols, "semillero"), _celda(fila, cols, "grupo"),
                    _celda(fila, cols, "facultad"), _celda(fila, cols, "descripcion"), crear_grupo=False)
            r["cargo"] = "Director/a" if es_director else "Estudiante"
            (actualizados if r.get("actualizado") else creados).append(r)
        except ValueError as e:
            omitidos.append({"fila": n, "motivo": str(e)})

    con.commit()
    return {"creados": creados, "actualizados": actualizados, "omitidos": omitidos}


def _grupo_o_error(con, grupo_id: int) -> dict:
    g = db.obtener_grupo(con, grupo_id)
    if not g:
        raise ValueError("Ese grupo no existe.")
    return g


def asignar_director(con, grupo_id: int, nombre: str, correo: str = "", adicional: bool = False,
                     semillero: str = "") -> dict:
    """Deja a una persona como director del grupo; con adicional=True agrega otro sin tocar al anterior. No hace commit."""
    grupo = _grupo_o_error(con, grupo_id)
    nombre, correo = (nombre or "").strip(), (correo or "").strip()
    semillero = (semillero or "").strip()
    if not nombre:
        raise ValueError("Falta el nombre del director.")
    if correo and not RE_CORREO.match(correo):
        raise ValueError("El correo del director no es válido.")
    otro = db.usuario_en_grupo_por_correo(con, correo, grupo_id) if correo else None

    previos = db.directores_de_grupo(con, grupo_id)
    if adicional and previos:
        mismo = [p for p in previos if (correo and (p["correo"] or "").lower() == correo.lower())
                 or (not correo and p["nombre"].lower() == nombre.lower())]
        if mismo:
            con.execute("UPDATE usuarios SET nombre = ?, correo = ? WHERE id = ?",
                        (nombre, correo or mismo[0]["correo"], mismo[0]["id"]))
            return {"id": mismo[0]["id"], "nombre": nombre, "correo": correo, "grupo": grupo["nombre"]}
    elif previos:
        uid = previos[0]["id"]
        if otro and otro["id"] != uid:
            raise ValueError("Ese correo ya pertenece a otra persona.")
        con.execute("UPDATE usuarios SET nombre = ?, correo = ? WHERE id = ?", (nombre, correo or None, uid))
        return {"id": uid, "nombre": nombre, "correo": correo, "grupo": grupo["nombre"]}

    if otro:
        uid = otro["id"]
    else:
        try:
            uid = agregar_estudiante(con, nombre, correo, semillero, grupo["nombre"], grupo.get("facultad") or "",
                                     grupo.get("descripcion") or "")["id"]
        except ValueError:
            fila = con.execute(
                "SELECT u.id FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id "
                "WHERE s.grupo_id = ? AND LOWER(u.nombre) = LOWER(?)", (grupo_id, nombre)).fetchone()
            if not fila:
                raise
            uid = fila["id"]
            if correo:
                con.execute("UPDATE usuarios SET correo = ? WHERE id = ?", (correo, uid))
    con.execute("UPDATE usuarios SET es_director_grupo = 1 WHERE id = ?", (uid,))
    return {"id": uid, "nombre": nombre, "correo": correo, "grupo": grupo["nombre"]}


def aprobar_solicitud(con, solicitud: dict) -> dict:
    """Convierte una solicitud de registro en una persona del grupo. No hace commit."""
    grupo = _grupo_o_error(con, solicitud["grupo_id"])
    if db.usuario_en_grupo_por_correo(con, solicitud["correo"], solicitud["grupo_id"]):
        return {"ya_existia": True, "nombre": solicitud["nombre"], "correo": solicitud["correo"]}
    r = agregar_estudiante(con, solicitud["nombre"], solicitud["correo"], solicitud.get("semillero") or "",
                           grupo["nombre"], grupo.get("facultad") or "", grupo.get("descripcion") or "")
    r["ya_existia"] = False
    return r


def _area_valida(texto: str) -> str:
    t = (texto or "").strip().lower()
    if t in AREAS:
        return t
    for clave, etiqueta in ETIQUETAS_AREA.items():
        if t == etiqueta.lower():
            return clave
    return ""


def limpio(texto) -> str:
    """Quita espacios y saltos de línea sobrantes."""
    return " ".join(str(texto or "").split())


def _sin_tildes(texto) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def mismo_texto(a, b) -> bool:
    return _sin_tildes(limpio(a)) == _sin_tildes(limpio(b))


def plantilla_con_grupos(con, ruta_base) -> bytes:
    """Plantilla con listas desplegables de grupos y facultades en columnas ocultas de la misma hoja."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = load_workbook(ruta_base)
    ws = wb.worksheets[0]
    nombres = [f["nombre"] for f in con.execute("SELECT nombre FROM grupos ORDER BY nombre").fetchall()]
    encab = [str(c.value).strip().lower() if c.value else "" for c in ws[1]]
    siguiente = max(len(encab), ws.max_column) + 3

    def lista_oculta(titulo: str, valores: list[str], columna_destino: str, offset: int):
        """Escribe los valores en una columna oculta y la usa como lista de la columna destino."""
        if not valores or columna_destino not in encab:
            return
        col_lista = siguiente + offset
        letra_lista = get_column_letter(col_lista)
        ws.cell(row=1, column=col_lista, value=titulo)
        for i, v in enumerate(valores, start=2):
            ws.cell(row=i, column=col_lista, value=v)
        ws.column_dimensions[letra_lista].hidden = True
        letra = get_column_letter(encab.index(columna_destino) + 1)
        dv = DataValidation(type="list", formula1=f"${letra_lista}$2:${letra_lista}${len(valores) + 1}",
                            allow_blank=True, showErrorMessage=True, errorStyle="stop",
                            errorTitle="Valor no válido", error="Elige una opción de la lista desplegable.")
        ws.add_data_validation(dv)
        dv.add(f"{letra}2:{letra}2000")

    lista_oculta("lista de grupos (no editar)", nombres, "grupo", 0)
    lista_oculta("lista de facultades (no editar)", FACULTADES, "facultad", 1)
    if "Instrucciones" in wb.sheetnames:
        from datetime import datetime
        ins = wb["Instrucciones"]
        ins.cell(row=ins.max_row + 2, column=1,
                 value=f"Plantilla generada el {datetime.now():%d/%m/%Y a las %H:%M} con {len(nombres)} grupos.")
    b = BytesIO()
    wb.save(b)
    return b.getvalue()