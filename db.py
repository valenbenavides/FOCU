"""Base de datos: SQLite en desarrollo y PostgreSQL (DATABASE_URL) en producción, con las mismas consultas."""
import json
from datetime import date
import os
import sqlite3
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
ES_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
RUTA_DB = Path(os.environ.get("DB_PATH") or Path(__file__).parent / "datos" / "focu.db")

if ES_PG:
    import psycopg2
    import psycopg2.extras
    ErrorIntegridad = (sqlite3.IntegrityError, psycopg2.IntegrityError)
else:
    ErrorIntegridad = (sqlite3.IntegrityError,)


class Conexion:
    """Envoltorio para usar SQLite y PostgreSQL con consultas escritas con `?`."""

    def __init__(self):
        if ES_PG:
            self._con = self._conectar_pg()
        else:
            RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(RUTA_DB, check_same_thread=False)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _conectar_pg():
        return psycopg2.connect(DATABASE_URL, connect_timeout=15, keepalives=1, keepalives_idle=30,
                                keepalives_interval=10, keepalives_count=5,
                                cursor_factory=psycopg2.extras.RealDictCursor)

    def reconectar(self) -> None:
        """Cierra y abre de nuevo la conexión (PostgreSQL)."""
        if ES_PG:
            self.close()
            self._con = self._conectar_pg()

    def asegurar(self) -> None:
        """Reabre la conexión si el servidor la cerró; descarta cualquier transacción abierta."""
        if not ES_PG:
            return
        try:
            self._con.rollback()
            self.execute("SELECT 1").fetchone()
            self._con.rollback()
        except Exception:
            self.reconectar()

    def execute(self, sql: str, params=()):
        if ES_PG:
            cur = self._con.cursor()
            cur.execute(sql.replace("?", "%s"), tuple(params) if params else None)
            return cur
        return self._con.execute(sql, params)

    def insertar(self, sql: str, params=()) -> int:
        """INSERT en una tabla con `id`; devuelve el id nuevo. Lanza ErrorIntegridad si viola una restricción."""
        if not ES_PG:
            return self._con.execute(sql, params).lastrowid
        self.execute("SAVEPOINT sp_insert")
        try:
            nuevo = self.execute(sql + " RETURNING id", params).fetchone()["id"]
        except psycopg2.IntegrityError:
            self.execute("ROLLBACK TO SAVEPOINT sp_insert")
            raise
        self.execute("RELEASE SAVEPOINT sp_insert")
        return nuevo

    def commit(self):
        self._con.commit()

    def rollback(self):
        try:
            self._con.rollback()
        except Exception:
            if not ES_PG:
                raise
            self.reconectar()

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass


def conectar() -> Conexion:
    return Conexion()


def _esquema() -> list[str]:
    pk = "SERIAL PRIMARY KEY" if ES_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts = "TIMESTAMP" if ES_PG else "TEXT"
    return [
        f"""CREATE TABLE IF NOT EXISTS grupos (
            id {pk},
            nombre TEXT NOT NULL UNIQUE,
            descripcion TEXT,
            area TEXT,
            facultad TEXT,
            codigo TEXT,
            categoria TEXT,
            linea TEXT,
            enlace_gruplac TEXT,
            contactos_extra TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS semilleros (
            id {pk},
            nombre TEXT NOT NULL,
            grupo_id INTEGER NOT NULL REFERENCES grupos(id),
            UNIQUE (nombre, grupo_id))""",
        f"""CREATE TABLE IF NOT EXISTS usuarios (
            id {pk},
            nombre TEXT NOT NULL,
            correo TEXT,
            rol TEXT NOT NULL CHECK (rol IN ('estudiante', 'admin')),
            semillero_id INTEGER REFERENCES semilleros(id),
            codigo_hash TEXT NOT NULL UNIQUE,
            facultad TEXT,
            codigo_expira INTEGER,
            codigo_intentos INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            consentimiento INTEGER NOT NULL DEFAULT 0,
            es_director_grupo INTEGER NOT NULL DEFAULT 0,
            perfil_json TEXT NOT NULL DEFAULT '{{}}',
            creado {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS chat_conversaciones (
            id {pk},
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            titulo TEXT NOT NULL,
            creado {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS chat_mensajes (
            id {pk},
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            conversacion_id INTEGER,
            rol TEXT NOT NULL,
            texto TEXT NOT NULL,
            creado {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX IF NOT EXISTS idx_chat_usuario ON chat_mensajes (usuario_id, id)",
        f"""CREATE TABLE IF NOT EXISTS solicitudes_registro (
            id {pk},
            nombre TEXT NOT NULL,
            correo TEXT NOT NULL,
            grupo_id INTEGER NOT NULL REFERENCES grupos(id),
            semillero TEXT,
            estado TEXT NOT NULL DEFAULT 'sin_verificar'
                CHECK (estado IN ('sin_verificar', 'pendiente', 'aprobada', 'rechazada')),
            codigo_hash TEXT,
            codigo_expira INTEGER,
            codigo_intentos INTEGER NOT NULL DEFAULT 0,
            comentario TEXT,
            creada {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resuelta {ts},
            resuelta_por INTEGER)""",
        f"""CREATE TABLE IF NOT EXISTS postulaciones (
            id {pk},
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            convocatoria_id TEXT NOT NULL,
            nota TEXT,
            estado TEXT NOT NULL DEFAULT 'solicitada'
                CHECK (estado IN ('solicitada', 'aprobada', 'rechazada')),
            comentario_vri TEXT,
            etapa_grupo TEXT NOT NULL DEFAULT 'aprobada',
            comentario_director TEXT,
            decidida_director {ts},
            grupo_id INTEGER,
            titulo TEXT,
            creada {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resuelta {ts},
            UNIQUE (usuario_id, convocatoria_id))""",
        f"""CREATE TABLE IF NOT EXISTS postulacion_requisitos (
            id {pk},
            postulacion_id INTEGER NOT NULL REFERENCES postulaciones(id) ON DELETE CASCADE,
            clave TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            observacion TEXT,
            actualizado_por INTEGER,
            actualizado {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (postulacion_id, clave))""",
        f"""CREATE TABLE IF NOT EXISTS convocatorias (
            id TEXT PRIMARY KEY,
            datos TEXT NOT NULL,
            fuente TEXT NOT NULL DEFAULT 'manual',
            verificada INTEGER NOT NULL DEFAULT 0,
            activa INTEGER NOT NULL DEFAULT 1,
            actualizada {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS enlaces_vistos (
            url TEXT PRIMARY KEY,
            resultado TEXT NOT NULL,
            vista {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS ajustes (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL)""",
    ]


def iniciar_db(con: Conexion) -> None:
    """Crea las tablas que falten; si la conexión se cierra a la mitad, reconecta y repite."""
    try:
        _iniciar_db(con)
    except Exception as e:
        if ES_PG and isinstance(e, (psycopg2.InterfaceError, psycopg2.OperationalError)):
            con.reconectar()
            _iniciar_db(con)
        else:
            raise


def _iniciar_db(con: Conexion) -> None:
    for sentencia in _esquema():
        con.execute(sentencia)
    con.commit()
    for tabla, col, tipo in (
            ("usuarios", "facultad", "TEXT"), ("usuarios", "codigo_expira", "INTEGER"),
            ("usuarios", "codigo_intentos", "INTEGER NOT NULL DEFAULT 0"),
            ("usuarios", "es_director_grupo", "INTEGER NOT NULL DEFAULT 0"),
            ("grupos", "descripcion", "TEXT"), ("grupos", "area", "TEXT"),
            ("grupos", "facultad", "TEXT"), ("grupos", "codigo", "TEXT"),
            ("grupos", "categoria", "TEXT"), ("grupos", "linea", "TEXT"),
            ("grupos", "enlace_gruplac", "TEXT"), ("grupos", "contactos_extra", "TEXT"),
            ("postulaciones", "etapa_grupo", "TEXT NOT NULL DEFAULT 'aprobada'"),
            ("postulaciones", "comentario_director", "TEXT"),
            ("postulaciones", "decidida_director", "TIMESTAMP" if ES_PG else "TEXT"),
            ("postulaciones", "grupo_id", "INTEGER"), ("postulaciones", "titulo", "TEXT"),
            ("chat_mensajes", "conversacion_id", "INTEGER")):
        try:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {'IF NOT EXISTS ' if ES_PG else ''}{col} {tipo}")
            con.commit()
        except Exception:
            con.rollback()
    _postulaciones_por_grupo(con)


def _postulaciones_por_grupo(con) -> None:
    """Asigna cada postulación a su grupo y crea el índice de una por grupo y convocatoria."""
    con.execute("""UPDATE postulaciones SET grupo_id =
                   (SELECT s.grupo_id FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id
                    WHERE u.id = postulaciones.usuario_id)
                   WHERE grupo_id IS NULL""")
    con.commit()
    try:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_postulacion_grupo_convocatoria "
                    "ON postulaciones (grupo_id, convocatoria_id) WHERE grupo_id IS NOT NULL")
        con.commit()
    except Exception:
        con.rollback()


_SELECT_USUARIO = """
SELECT u.id, u.nombre, u.correo, u.rol, u.activo, u.consentimiento, u.perfil_json, u.facultad,
       u.es_director_grupo, u.semillero_id, s.nombre AS semillero, g.id AS grupo_id, g.nombre AS grupo
FROM usuarios u
LEFT JOIN semilleros s ON s.id = u.semillero_id
LEFT JOIN grupos g ON g.id = s.grupo_id
"""


def _usuario(fila) -> dict | None:
    if fila is None:
        return None
    u = dict(fila)
    u["perfil"] = json.loads(u.pop("perfil_json") or "{}")
    u["activo"] = bool(u["activo"])
    u["consentimiento"] = bool(u["consentimiento"])
    u["es_director_grupo"] = bool(u["es_director_grupo"])
    return u


def usuario_por_id(con, usuario_id: int) -> dict | None:
    return _usuario(con.execute(_SELECT_USUARIO + " WHERE u.id = ?", (usuario_id,)).fetchone())


def usuario_por_hash(con, codigo_hash: str) -> dict | None:
    return _usuario(con.execute(_SELECT_USUARIO + " WHERE u.codigo_hash = ?", (codigo_hash,)).fetchone())


def usuarios_por_correo(con, correo: str) -> list[dict]:
    """Los perfiles que comparten un correo; la cuenta de la VRI va primero."""
    filas = con.execute(_SELECT_USUARIO + " WHERE LOWER(u.correo) = LOWER(?) "
                        "ORDER BY CASE WHEN u.rol = 'admin' THEN 0 ELSE 1 END, u.id",
                        ((correo or "").strip(),)).fetchall()
    return [_usuario(f) for f in filas]


def puede_ingresar(u: dict) -> bool:
    return bool(u and u["activo"] and (u["rol"] == "admin" or u.get("semillero_id")))


def usuario_por_correo(con, correo: str) -> dict | None:
    """El perfil de ingreso por defecto: el primero que puede ingresar, o el primero que exista."""
    perfiles = usuarios_por_correo(con, correo)
    return next((u for u in perfiles if puede_ingresar(u)), perfiles[0] if perfiles else None)


def usuario_en_grupo_por_correo(con, correo: str, grupo_id: int | None) -> dict | None:
    """La cuenta con ese correo dentro de ese grupo (grupo_id None = cuentas de la VRI)."""
    for u in usuarios_por_correo(con, correo):
        if grupo_id is None and u["rol"] == "admin":
            return u
        if grupo_id is not None and u["rol"] != "admin" and u.get("grupo_id") == grupo_id:
            return u
    return None


def guardar_codigo_temporal(con, usuario_id: int, codigo_hash: str, expira: int) -> None:
    con.execute("UPDATE usuarios SET codigo_hash = ?, codigo_expira = ?, codigo_intentos = 0 WHERE id = ?",
                (codigo_hash, expira, usuario_id))
    con.commit()


def datos_codigo(con, usuario_id: int) -> dict | None:
    f = con.execute("SELECT codigo_hash, codigo_expira, codigo_intentos FROM usuarios WHERE id = ?",
                    (usuario_id,)).fetchone()
    return dict(f) if f else None


def sumar_intento_codigo(con, usuario_id: int) -> None:
    con.execute("UPDATE usuarios SET codigo_intentos = codigo_intentos + 1 WHERE id = ?", (usuario_id,))
    con.commit()


def correos_administradores(con) -> list[str]:
    filas = con.execute("SELECT correo FROM usuarios WHERE rol = 'admin' AND activo = 1 "
                        "AND correo IS NOT NULL AND correo != ''").fetchall()
    return [f["correo"] for f in filas]


def hay_admin(con) -> bool:
    return con.execute("SELECT 1 AS x FROM usuarios WHERE rol = 'admin' LIMIT 1").fetchone() is not None


def obtener_o_crear_grupo(con, nombre: str) -> int:
    fila = con.execute("SELECT id FROM grupos WHERE LOWER(nombre) = LOWER(?)", (nombre,)).fetchone()
    if fila:
        return fila["id"]
    return con.insertar("INSERT INTO grupos (nombre) VALUES (?)", (nombre,))


def obtener_o_crear_semillero(con, nombre: str, grupo_id: int) -> int:
    fila = con.execute("SELECT id FROM semilleros WHERE LOWER(nombre) = LOWER(?) AND grupo_id = ?",
                       (nombre, grupo_id)).fetchone()
    if fila:
        return fila["id"]
    return con.insertar("INSERT INTO semilleros (nombre, grupo_id) VALUES (?, ?)", (nombre, grupo_id))


def insertar_usuario(con, nombre: str, correo: str | None, rol: str, semillero_id: int | None,
                     codigo_hash: str, consentimiento: int = 0, facultad: str | None = None,
                     perfil: dict | None = None) -> int:
    """Lanza ErrorIntegridad si el hash del código ya existe."""
    return con.insertar(
        "INSERT INTO usuarios (nombre, correo, rol, semillero_id, codigo_hash, consentimiento, "
        "facultad, perfil_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (nombre, correo or None, rol, semillero_id, codigo_hash, consentimiento, facultad or None,
         json.dumps(perfil or {}, ensure_ascii=False)))


def guardar_perfil(con, usuario_id: int, perfil: dict) -> None:
    con.execute("UPDATE usuarios SET perfil_json = ? WHERE id = ?",
                (json.dumps(perfil, ensure_ascii=False), usuario_id))
    con.commit()


CHAT_MAX_MENSAJES = 100
CHAT_DIAS = 4


def _ahora_chat(dias: int = 0) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")


def _limite_chat() -> str:
    return _ahora_chat(CHAT_DIAS)


def purgar_chat(con) -> None:
    """Borra las conversaciones vencidas (y los mensajes sueltos de versiones anteriores)."""
    lim = _limite_chat()
    con.execute("DELETE FROM chat_mensajes WHERE conversacion_id IS NULL OR conversacion_id IN "
                "(SELECT id FROM chat_conversaciones WHERE actualizado < ?)", (lim,))
    con.execute("DELETE FROM chat_conversaciones WHERE actualizado < ?", (lim,))
    con.commit()


def crear_conversacion(con, usuario_id: int, titulo: str) -> int:
    titulo = " ".join((titulo or "").split())[:60] or "Conversación"
    ahora = _ahora_chat()
    cur = con.execute("INSERT INTO chat_conversaciones (usuario_id, titulo, creado, actualizado) VALUES (?, ?, ?, ?)",
                      (usuario_id, titulo, ahora, ahora))
    con.commit()
    return cur.lastrowid


def conversacion_es_de(con, usuario_id: int, conversacion_id: int) -> bool:
    return con.execute("SELECT 1 FROM chat_conversaciones WHERE id = ? AND usuario_id = ?",
                       (conversacion_id, usuario_id)).fetchone() is not None


def guardar_mensaje_chat(con, usuario_id: int, rol: str, texto: str, conversacion_id: int) -> None:
    con.execute("INSERT INTO chat_mensajes (usuario_id, conversacion_id, rol, texto) VALUES (?, ?, ?, ?)",
                (usuario_id, conversacion_id, rol, texto))
    con.execute("UPDATE chat_conversaciones SET actualizado = ? WHERE id = ?", (_ahora_chat(), conversacion_id))
    con.execute("DELETE FROM chat_mensajes WHERE conversacion_id = ? AND id NOT IN "
                "(SELECT id FROM chat_mensajes WHERE conversacion_id = ? ORDER BY id DESC LIMIT ?)",
                (conversacion_id, conversacion_id, CHAT_MAX_MENSAJES))
    con.commit()


def listar_conversaciones(con, usuario_id: int, limite: int = 40) -> list[dict]:
    """Historial de la persona: la más reciente primero. Antes de listar borra lo vencido."""
    purgar_chat(con)
    filas = con.execute("SELECT id, titulo, actualizado FROM chat_conversaciones WHERE usuario_id = ? "
                        "ORDER BY actualizado DESC, id DESC LIMIT ?", (usuario_id, limite)).fetchall()
    return [{"id": f["id"], "titulo": f["titulo"], "actualizado": str(f["actualizado"])[:19]} for f in filas]


def mensajes_de_conversacion(con, usuario_id: int, conversacion_id: int, limite: int = 100) -> list[dict]:
    """Los mensajes de una conversación de esta persona, del más antiguo al más reciente."""
    filas = con.execute("SELECT m.rol, m.texto FROM chat_mensajes m JOIN chat_conversaciones c ON c.id = m.conversacion_id "
                        "WHERE c.id = ? AND c.usuario_id = ? AND c.actualizado >= ? ORDER BY m.id DESC LIMIT ?",
                        (conversacion_id, usuario_id, _limite_chat(), limite)).fetchall()
    return [{"rol": f["rol"], "texto": f["texto"]} for f in reversed(filas)]


def borrar_conversacion(con, usuario_id: int, conversacion_id: int) -> None:
    if not conversacion_es_de(con, usuario_id, conversacion_id):
        return
    con.execute("DELETE FROM chat_mensajes WHERE conversacion_id = ?", (conversacion_id,))
    con.execute("DELETE FROM chat_conversaciones WHERE id = ?", (conversacion_id,))
    con.commit()


def borrar_chat(con, usuario_id: int) -> None:
    con.execute("DELETE FROM chat_mensajes WHERE usuario_id = ?", (usuario_id,))
    con.execute("DELETE FROM chat_conversaciones WHERE usuario_id = ?", (usuario_id,))
    con.commit()


def listar_estudiantes(con) -> list[dict]:
    filas = con.execute("""
        SELECT u.id, u.nombre, u.correo, u.facultad, u.activo, u.consentimiento, u.es_director_grupo,
               (u.perfil_json != '{}') AS tiene_perfil,
               s.nombre AS semillero, g.nombre AS grupo,
               (SELECT COUNT(*) FROM postulaciones p WHERE p.usuario_id = u.id) AS postulaciones
        FROM usuarios u
        LEFT JOIN semilleros s ON s.id = u.semillero_id
        LEFT JOIN grupos g ON g.id = s.grupo_id
        WHERE u.rol = 'estudiante'
        ORDER BY g.nombre, s.nombre, u.nombre""").fetchall()
    out = []
    for f in filas:
        d = dict(f)
        for k in ("activo", "consentimiento", "tiene_perfil", "es_director_grupo"):
            d[k] = bool(d[k])
        out.append(d)
    return out


def listar_semilleros(con) -> list[dict]:
    """Grupos con sus semilleros y cuántos estudiantes tiene cada uno (para listas desplegables)."""
    filas = con.execute("""
        SELECT g.id AS grupo_id, g.nombre AS grupo, s.id AS semillero_id, s.nombre AS semillero,
               (SELECT COUNT(*) FROM usuarios u
                WHERE u.semillero_id = s.id AND u.rol = 'estudiante') AS estudiantes
        FROM grupos g LEFT JOIN semilleros s ON s.grupo_id = g.id
        ORDER BY g.nombre, s.nombre""").fetchall()
    grupos: dict[int, dict] = {}
    for f in filas:
        g = grupos.setdefault(f["grupo_id"], {"id": f["grupo_id"], "nombre": f["grupo"], "semilleros": []})
        if f["semillero_id"] is not None:
            g["semilleros"].append({"id": f["semillero_id"], "nombre": f["semillero"],
                                    "estudiantes": f["estudiantes"]})
    return list(grupos.values())


def obtener_grupo(con, grupo_id: int) -> dict | None:
    f = con.execute("SELECT id, nombre, descripcion, area, facultad, codigo, categoria, linea, enlace_gruplac, "
                    "contactos_extra FROM grupos WHERE id = ?",
                    (grupo_id,)).fetchone()
    return dict(f) if f else None


def guardar_grupo(con, nombre: str, descripcion: str = "", area: str = "", facultad: str = "",
                  codigo: str = "", categoria: str = "", linea: str = "", enlace_gruplac: str = "",
                  contactos_extra: str = "") -> tuple[int, bool]:
    """Crea el grupo o completa el existente. Devuelve (id, es_nuevo). No hace commit."""
    nombre = nombre.strip()
    fila = con.execute("SELECT id FROM grupos WHERE LOWER(nombre) = LOWER(?)", (nombre,)).fetchone()
    datos = {"descripcion": (descripcion or "").strip()[:2000], "area": (area or "").strip(),
             "facultad": (facultad or "").strip(), "codigo": (codigo or "").strip(),
             "categoria": (categoria or "").strip()[:20], "linea": (linea or "").strip()[:200],
             "enlace_gruplac": (enlace_gruplac or "").strip()[:500],
             "contactos_extra": (contactos_extra or "").strip()[:500]}
    if fila:
        for campo, valor in datos.items():
            if valor:
                con.execute(f"UPDATE grupos SET {campo} = ? WHERE id = ?", (valor, fila["id"]))
        return fila["id"], False
    nuevo = con.insertar(
        "INSERT INTO grupos (nombre, descripcion, area, facultad, codigo, categoria, linea, enlace_gruplac, "
        "contactos_extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nombre, datos["descripcion"] or None, datos["area"] or None, datos["facultad"] or None,
         datos["codigo"] or None, datos["categoria"] or None, datos["linea"] or None,
         datos["enlace_gruplac"] or None, datos["contactos_extra"] or None))
    return nuevo, True


def eliminar_grupo(con, grupo_id: int) -> str | None:
    """Borra un grupo con su director y solicitudes. Devuelve None o el motivo si no se pudo."""
    g = obtener_grupo(con, grupo_id)
    if not g:
        return "Ese grupo no existe."
    miembros = con.execute("""SELECT COUNT(*) AS n FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id
        WHERE s.grupo_id = ? AND u.es_director_grupo = 0""", (grupo_id,)).fetchone()["n"]
    if miembros:
        return (f"Este grupo tiene {miembros} integrante(s). Muévelos o elimínalos primero "
                "(pestaña Personas) y luego borra el grupo.")
    usos = con.execute("""SELECT COUNT(*) AS n FROM postulaciones p
        WHERE p.grupo_id = ? OR p.usuario_id IN (SELECT u.id FROM usuarios u
            JOIN semilleros s ON s.id = u.semillero_id WHERE s.grupo_id = ?)""",
        (grupo_id, grupo_id)).fetchone()["n"]
    if usos:
        return f"Este grupo tiene {usos} postulación(es) y no se puede eliminar."
    con.execute("""DELETE FROM usuarios WHERE es_director_grupo = 1 AND rol = 'estudiante' AND semillero_id IN
        (SELECT id FROM semilleros WHERE grupo_id = ?)""", (grupo_id,))
    con.execute("DELETE FROM solicitudes_registro WHERE grupo_id = ?", (grupo_id,))
    con.execute("DELETE FROM semilleros WHERE grupo_id = ?", (grupo_id,))
    con.execute("DELETE FROM grupos WHERE id = ?", (grupo_id,))
    con.commit()
    return None


def directores_de_grupo(con, grupo_id: int) -> list[dict]:
    filas = con.execute("""
        SELECT u.id, u.nombre, u.correo, u.activo FROM usuarios u
        JOIN semilleros s ON s.id = u.semillero_id
        WHERE s.grupo_id = ? AND u.es_director_grupo = 1 ORDER BY u.id""", (grupo_id,)).fetchall()
    return [dict(f) for f in filas]


def correos_directores_de_grupo(con, grupo_id: int) -> list[str]:
    return [d["correo"] for d in directores_de_grupo(con, grupo_id) if d["activo"] and d["correo"]]


def listar_grupos(con) -> list[dict]:
    """Grupos con su director, cuántos integrantes tienen y cuántas solicitudes esperan respuesta."""
    filas = con.execute("""
        SELECT g.id, g.nombre, g.descripcion, g.area, g.facultad, g.codigo, g.categoria, g.linea,
          g.enlace_gruplac, g.contactos_extra,
          (SELECT COUNT(*) FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id
             WHERE s.grupo_id = g.id AND u.rol = 'estudiante' AND u.es_director_grupo = 0) AS miembros,
          (SELECT COUNT(*) FROM solicitudes_registro r
             WHERE r.grupo_id = g.id AND r.estado = 'pendiente') AS pendientes
        FROM grupos g ORDER BY g.nombre""").fetchall()
    salida = []
    for f in filas:
        g = dict(f)
        d = directores_de_grupo(con, g["id"])
        g["director"] = d[0]["nombre"] if d else None
        g["director_correo"] = d[0]["correo"] if d else None
        salida.append(g)
    return salida


_SELECT_SOLICITUD = """
SELECT r.id, r.nombre, r.correo, r.grupo_id, g.nombre AS grupo, r.semillero, r.estado, r.comentario,
       r.creada, r.resuelta
FROM solicitudes_registro r JOIN grupos g ON g.id = r.grupo_id"""


def crear_solicitud_sin_verificar(con, nombre: str, correo: str, grupo_id: int, semillero: str,
                                  codigo_hash: str, expira: int) -> int:
    """Guarda la solicitud con el código que se envió al correo. Reemplaza una anterior sin verificar."""
    con.execute("DELETE FROM solicitudes_registro WHERE LOWER(correo) = LOWER(?) AND estado = 'sin_verificar'",
                (correo,))
    nuevo = con.insertar(
        "INSERT INTO solicitudes_registro (nombre, correo, grupo_id, semillero, codigo_hash, codigo_expira) "
        "VALUES (?, ?, ?, ?, ?, ?)", (nombre, correo, grupo_id, semillero or None, codigo_hash, expira))
    con.commit()
    return nuevo


def solicitud_en_estado(con, correo: str, estado: str) -> dict | None:
    f = con.execute(_SELECT_SOLICITUD + " WHERE LOWER(r.correo) = LOWER(?) AND r.estado = ? ORDER BY r.id DESC",
                    (correo, estado)).fetchone()
    return dict(f) if f else None


def datos_codigo_solicitud(con, solicitud_id: int) -> dict | None:
    f = con.execute("SELECT codigo_hash, codigo_expira, codigo_intentos FROM solicitudes_registro WHERE id = ?",
                    (solicitud_id,)).fetchone()
    return dict(f) if f else None


def sumar_intento_solicitud(con, solicitud_id: int) -> None:
    con.execute("UPDATE solicitudes_registro SET codigo_intentos = codigo_intentos + 1 WHERE id = ?",
                (solicitud_id,))
    con.commit()


def marcar_solicitud(con, solicitud_id: int, estado: str, comentario: str | None = None,
                     resuelta_por: int | None = None) -> None:
    resuelta = "CURRENT_TIMESTAMP" if estado in ("aprobada", "rechazada") else "NULL"
    con.execute(f"UPDATE solicitudes_registro SET estado = ?, comentario = ?, resuelta = {resuelta}, "
                "resuelta_por = ?, codigo_hash = NULL WHERE id = ?",
                (estado, comentario, resuelta_por, solicitud_id))


def obtener_solicitud(con, solicitud_id: int) -> dict | None:
    f = con.execute(_SELECT_SOLICITUD + " WHERE r.id = ?", (solicitud_id,)).fetchone()
    return dict(f) if f else None


def listar_solicitudes(con, estado: str | None = "pendiente", grupo_id: int | None = None) -> list[dict]:
    sql, params = _SELECT_SOLICITUD + " WHERE r.estado != 'sin_verificar'", []
    if estado:
        sql += " AND r.estado = ?"
        params.append(estado)
    if grupo_id is not None:
        sql += " AND r.grupo_id = ?"
        params.append(grupo_id)
    sql += " ORDER BY r.creada DESC, r.id DESC"
    out = []
    for f in con.execute(sql, params).fetchall():
        d = dict(f)
        d["sin_director"] = not correos_directores_de_grupo(con, d["grupo_id"])
        out.append(d)
    return out


def listar_miembros_de_grupo(con, grupo_id: int) -> list[dict]:
    filas = con.execute("""
        SELECT u.id, u.nombre, u.correo, u.facultad, u.activo, u.es_director_grupo, s.nombre AS semillero
        FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id
        WHERE s.grupo_id = ? AND u.rol = 'estudiante'
        ORDER BY u.es_director_grupo DESC, u.nombre""", (grupo_id,)).fetchall()
    out = []
    for f in filas:
        d = dict(f)
        d["activo"], d["es_director_grupo"] = bool(d["activo"]), bool(d["es_director_grupo"])
        out.append(d)
    return out


ETAPAS_GRUPO = ("pendiente", "aprobada", "devuelta")


def fase_postulacion(p: dict) -> str:
    """Dónde va la postulación: en_director, devuelta, en_vri, aprobada o rechazada."""
    if p.get("estado") in ("aprobada", "rechazada"):
        return p["estado"]
    etapa = p.get("etapa_grupo") or "aprobada"
    return {"pendiente": "en_director", "devuelta": "devuelta"}.get(etapa, "en_vri")


_SELECT_POSTULACION = """
        SELECT p.id, p.convocatoria_id, p.nota, p.estado, p.comentario_vri, p.creada, p.resuelta,
               p.etapa_grupo, p.comentario_director, p.decidida_director, p.titulo,
               u.id AS usuario_id, u.nombre AS estudiante, u.correo,
               s.nombre AS semillero, g.id AS grupo_id, g.nombre AS grupo
        FROM postulaciones p
        JOIN usuarios u ON u.id = p.usuario_id
        LEFT JOIN semilleros s ON s.id = u.semillero_id
        LEFT JOIN grupos g ON g.id = p.grupo_id
        WHERE 1 = 1"""


def listar_postulaciones(con, usuario_id: int | None = None, estado: str | None = None,
                         grupo_id: int | None = None, etapa_grupo: str | None = None) -> list[dict]:
    sql = _SELECT_POSTULACION
    params: list = []
    if usuario_id is not None:
        sql += " AND p.usuario_id = ?"
        params.append(usuario_id)
    if estado:
        sql += " AND p.estado = ?"
        params.append(estado)
    if grupo_id is not None:
        sql += " AND g.id = ?"
        params.append(grupo_id)
    if etapa_grupo:
        sql += " AND p.etapa_grupo = ?"
        params.append(etapa_grupo)
    sql += " ORDER BY p.creada DESC, p.id DESC"
    filas = [dict(f) for f in con.execute(sql, params).fetchall()]
    for f in filas:
        f["fase"] = fase_postulacion(f)
    return filas


def obtener_requisitos(con, postulacion_id: int) -> dict:
    filas = con.execute("""SELECT r.clave, r.estado, r.observacion, r.actualizado, u.nombre AS actualizado_por
                           FROM postulacion_requisitos r LEFT JOIN usuarios u ON u.id = r.actualizado_por
                           WHERE r.postulacion_id = ?""", (postulacion_id,)).fetchall()
    return {f["clave"]: dict(f) for f in filas}


def guardar_requisito(con, postulacion_id: int, clave: str, estado: str, observacion: str | None,
                      usuario_id: int) -> None:
    hay = con.execute("SELECT 1 AS x FROM postulacion_requisitos WHERE postulacion_id = ? AND clave = ?",
                      (postulacion_id, clave)).fetchone()
    if hay:
        con.execute("UPDATE postulacion_requisitos SET estado = ?, observacion = ?, actualizado_por = ?, "
                    "actualizado = CURRENT_TIMESTAMP WHERE postulacion_id = ? AND clave = ?",
                    (estado, observacion, usuario_id, postulacion_id, clave))
    else:
        con.execute("INSERT INTO postulacion_requisitos (postulacion_id, clave, estado, observacion, actualizado_por) "
                    "VALUES (?, ?, ?, ?, ?)", (postulacion_id, clave, estado, observacion, usuario_id))
    con.commit()


def estados_requisitos(con, ids: list[int]) -> dict[int, dict]:
    """{postulacion_id: {clave: estado}} para varias postulaciones de una vez."""
    if not ids:
        return {}
    marcas = ",".join("?" for _ in ids)
    filas = con.execute(f"SELECT postulacion_id, clave, estado FROM postulacion_requisitos "
                        f"WHERE postulacion_id IN ({marcas})", list(ids)).fetchall()
    salida: dict[int, dict] = {i: {} for i in ids}
    for f in filas:
        salida[f["postulacion_id"]][f["clave"]] = f["estado"]
    return salida


def postulacion_del_grupo(con, grupo_id: int, convocatoria_id: str) -> dict | None:
    """La postulación que el grupo ya tiene para esa convocatoria (si existe)."""
    f = con.execute(_SELECT_POSTULACION + " AND p.grupo_id = ? AND p.convocatoria_id = ?",
                    (grupo_id, str(convocatoria_id))).fetchone()
    if not f:
        return None
    f = dict(f)
    f["fase"] = fase_postulacion(f)
    return f


def eliminar_postulacion(con, postulacion_id: int) -> None:
    """Borra la postulación y su lista de requisitos."""
    con.execute("DELETE FROM postulacion_requisitos WHERE postulacion_id = ?", (postulacion_id,))
    con.execute("DELETE FROM postulaciones WHERE id = ?", (postulacion_id,))
    con.commit()


def obtener_postulacion(con, postulacion_id: int) -> dict | None:
    f = con.execute(_SELECT_POSTULACION + " AND p.id = ?", (postulacion_id,)).fetchone()
    if not f:
        return None
    f = dict(f)
    f["fase"] = fase_postulacion(f)
    return f


def guardar_convocatoria(con, conv: dict, fuente: str = "manual", verificada: bool = True,
                         activa: bool = True, respetar_verificadas: bool = False) -> None:
    """Crea o actualiza una convocatoria normalizada; con respetar_verificadas no pisa las ya verificadas."""
    condicion = " WHERE convocatorias.verificada = 0" if respetar_verificadas else ""
    conv = dict(conv)
    previa = con.execute("SELECT datos FROM convocatorias WHERE id = ?", (str(conv["id"]),)).fetchone()
    if previa:
        antes = json.loads(previa["datos"])
        for k in ("creada", "depuracion_desde"):
            if antes.get(k) and not conv.get(k):
                conv[k] = antes[k]
    conv.setdefault("creada", date.today().isoformat())
    con.execute(
        "INSERT INTO convocatorias (id, datos, fuente, verificada, activa, actualizada) "
        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT (id) DO UPDATE SET datos = excluded.datos, fuente = excluded.fuente, "
        "actualizada = CURRENT_TIMESTAMP" + condicion,
        (str(conv["id"]), json.dumps(conv, ensure_ascii=False), fuente, int(verificada), int(activa)))


def enlace_visto(con, url: str, version: int = 1) -> bool:
    """¿Ya se revisó con esta versión del lector? (si el lector mejora, se revisa otra vez)"""
    f = con.execute("SELECT resultado FROM enlaces_vistos WHERE url = ?", (url,)).fetchone()
    return bool(f) and str(f["resultado"]).endswith(f"|v{version}")


def obtener_ajuste(con, clave: str, defecto=None):
    """Valor guardado (JSON) de un ajuste de la aplicación, o `defecto` si no existe."""
    f = con.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    if not f:
        return defecto
    try:
        return json.loads(f["valor"])
    except ValueError:
        return defecto


def guardar_ajuste(con, clave: str, valor) -> None:
    con.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
                "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
                (clave, json.dumps(valor, ensure_ascii=False)))
    con.commit()


def marcar_enlace(con, url: str, resultado: str, version: int = 1) -> None:
    """Recuerda que el recolector ya revisó este enlace (para no gastar IA otra vez)."""
    resultado = f"{resultado}|v{version}"
    con.execute("INSERT INTO enlaces_vistos (url, resultado) VALUES (?, ?) "
                "ON CONFLICT (url) DO UPDATE SET resultado = excluded.resultado, vista = CURRENT_TIMESTAMP",
                (url, resultado))


def enlace_eliminado(con, url: str) -> bool:
    """¿La VRI eliminó la convocatoria de este enlace? Así el recolector no la vuelve a traer."""
    f = con.execute("SELECT resultado FROM enlaces_vistos WHERE url = ?", (url,)).fetchone()
    return bool(f) and str(f["resultado"]).startswith("eliminada")


def eliminar_convocatoria(con, cid: str) -> str | None:
    """Borra una convocatoria. Devuelve None o el motivo si no se pudo (tiene postulaciones)."""
    conv = obtener_convocatoria(con, cid)
    if not conv:
        return "Esa convocatoria no existe."
    usos = con.execute("SELECT COUNT(*) AS n FROM postulaciones WHERE convocatoria_id = ?", (str(cid),)).fetchone()["n"]
    if usos:
        return (f"Esta convocatoria ya tiene {usos} postulación(es) y no se puede eliminar. "
                "Puedes ocultarla para que nadie más la vea.")
    con.execute("DELETE FROM convocatorias WHERE id = ?", (str(cid),))
    if conv.get("url"):
        marcar_enlace(con, conv["url"], "eliminada", 1_000_000)
    con.commit()
    return None


DIAS_VENCIDA = 7
DIAS_SIN_REVISAR = 60
DIAS_DEPURACION = 14


def depurar_sin_revisar(con, hoy=None) -> dict:
    """Mueve a «por depurar» las convocatorias sin fecha ni visto bueno y borra las que lo llevan mucho tiempo."""
    from datetime import date, timedelta
    hoy = hoy or date.today()
    movidas = borradas = 0
    for f in con.execute("SELECT id, datos, actualizada FROM convocatorias WHERE verificada = 0").fetchall():
        d = json.loads(f["datos"])
        if str(d.get("fecha_limite") or "").strip():
            continue
        try:
            if d.get("depuracion_desde"):
                if (hoy - date.fromisoformat(d["depuracion_desde"])).days > DIAS_DEPURACION:
                    usos = con.execute("SELECT COUNT(*) AS n FROM postulaciones WHERE convocatoria_id = ?",
                                       (str(f["id"]),)).fetchone()["n"]
                    if not usos:
                        con.execute("DELETE FROM convocatorias WHERE id = ?", (str(f["id"]),))
                        borradas += 1
                continue
            creada = date.fromisoformat(str(d.get("creada") or f["actualizada"])[:10])
        except ValueError:
            continue
        if (hoy - creada).days >= DIAS_SIN_REVISAR:
            d["depuracion_desde"] = hoy.isoformat()
            con.execute("UPDATE convocatorias SET datos = ? WHERE id = ?",
                        (json.dumps(d, ensure_ascii=False), str(f["id"])))
            movidas += 1
    if movidas or borradas:
        con.commit()
    return {"movidas": movidas, "borradas": borradas}


def recuperar_convocatoria(con, cid: str) -> bool:
    """Saca una convocatoria de «por depurar»: vuelve al catálogo y el plazo de revisión empieza de nuevo."""
    f = con.execute("SELECT datos FROM convocatorias WHERE id = ?", (str(cid),)).fetchone()
    if not f:
        return False
    d = json.loads(f["datos"])
    d.pop("depuracion_desde", None)
    d["creada"] = date.today().isoformat()
    con.execute("UPDATE convocatorias SET datos = ? WHERE id = ?", (json.dumps(d, ensure_ascii=False), str(cid)))
    con.commit()
    return True


def mantenimiento_convocatorias(con) -> dict:
    """Limpieza automática del catálogo: vencidas de más de una semana y sin revisar de más de dos meses."""
    r = depurar_sin_revisar(con)
    r["vencidas_borradas"] = purgar_vencidas(con)
    return r


def purgar_vencidas(con, dias: int = DIAS_VENCIDA, hoy=None) -> int:
    """Borra las convocatorias vencidas hace más de `dias` días y sin postulaciones. Devuelve cuántas."""
    from datetime import date, timedelta
    limite = ((hoy or date.today()) - timedelta(days=dias)).isoformat()
    n = 0
    for c in listar_convocatorias(con, solo_activas=False):
        cierre = str(c.get("fecha_limite") or "")[:10]
        if not cierre or cierre >= limite:
            continue
        usos = con.execute("SELECT COUNT(*) AS n FROM postulaciones WHERE convocatoria_id = ?",
                           (str(c["id"]),)).fetchone()["n"]
        if usos:
            continue
        con.execute("DELETE FROM convocatorias WHERE id = ?", (str(c["id"]),))
        n += 1
    if n:
        con.commit()
    return n


def _conv(fila) -> dict:
    c = json.loads(fila["datos"])
    c["id"] = str(fila["id"])
    c.update({"fuente": fila["fuente"], "verificada": bool(fila["verificada"]),
              "activa": bool(fila["activa"]), "actualizada": fila["actualizada"]})
    return c


def listar_convocatorias(con, solo_activas: bool = True) -> list[dict]:
    sql = "SELECT id, datos, fuente, verificada, activa, actualizada FROM convocatorias"
    if solo_activas:
        sql += " WHERE activa = 1"
    sql += " ORDER BY id"
    return [_conv(f) for f in con.execute(sql).fetchall()]


def obtener_convocatoria(con, cid: str) -> dict | None:
    f = con.execute("SELECT id, datos, fuente, verificada, activa, actualizada "
                    "FROM convocatorias WHERE id = ?", (str(cid),)).fetchone()
    return _conv(f) if f else None


def cambiar_estado_convocatoria(con, cid: str, activa: bool | None = None,
                                verificada: bool | None = None) -> bool:
    cambios, params = [], []
    if activa is not None:
        cambios.append("activa = ?")
        params.append(int(activa))
    if verificada is not None:
        cambios.append("verificada = ?")
        params.append(int(verificada))
    if not cambios:
        return False
    params.append(str(cid))
    cur = con.execute(f"UPDATE convocatorias SET {', '.join(cambios)} WHERE id = ?", params)
    con.commit()
    return cur.rowcount > 0


def poner_fecha_convocatoria(con, cid: str, fecha: str | None, manual: bool = True) -> bool:
    """Escribe o borra (con None) la fecha de cierre; la manual no la pisa una relectura."""
    f = con.execute("SELECT datos FROM convocatorias WHERE id = ?", (str(cid),)).fetchone()
    if not f:
        return False
    datos = json.loads(f["datos"])
    if fecha:
        datos["fecha_limite"] = fecha
        if manual:
            datos["fecha_manual"] = True
    else:
        datos.pop("fecha_limite", None)
        datos.pop("fecha_manual", None)
    con.execute("UPDATE convocatorias SET datos = ?, actualizada = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(datos, ensure_ascii=False), str(cid)))
    con.commit()
    return True


def contar_convocatorias(con) -> int:
    return con.execute("SELECT COUNT(*) AS n FROM convocatorias").fetchone()["n"]