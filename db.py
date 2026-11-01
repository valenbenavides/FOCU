"""Base de datos. Funciona con dos motores sin cambiar el resto del código:

- SQLite (un archivo, datos/focu.db): para desarrollar en tu computador.
- PostgreSQL (Neon, Supabase, el servidor de la universidad...): para publicar en
  internet. Se activa poniendo DATABASE_URL en el .env o en las variables del servidor.

IMPORTANTE para el despliegue: en los servicios gratuitos el disco se borra al
reiniciar, así que allí SIEMPRE hay que usar PostgreSQL (DATABASE_URL).

Tablas: grupos -> semilleros -> usuarios (estudiantes y administradores VRI),
postulaciones (intención de un estudiante de aplicar a una convocatoria, que la VRI
aprueba o rechaza) y convocatorias (cargadas a mano o recolectadas por los scrapers)."""
import json
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
    """Envoltorio mínimo para usar SQLite y PostgreSQL con las mismas consultas
    (escritas con `?` como marcador; aquí se traducen para PostgreSQL)."""

    def __init__(self):
        if ES_PG:
            self._con = psycopg2.connect(DATABASE_URL, connect_timeout=15,
                                         cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: FastAPI atiende cada petición en un hilo distinto.
            self._con = sqlite3.connect(RUTA_DB, check_same_thread=False)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=()):
        if ES_PG:
            cur = self._con.cursor()
            cur.execute(sql.replace("?", "%s"), tuple(params) if params else None)
            return cur
        return self._con.execute(sql, params)

    def insertar(self, sql: str, params=()) -> int:
        """INSERT en una tabla con columna `id`. Devuelve el id nuevo.
        Lanza ErrorIntegridad si viola una restricción (p. ej. un valor repetido)."""
        if not ES_PG:
            return self._con.execute(sql, params).lastrowid
        # En PostgreSQL un error aborta toda la transacción; el SAVEPOINT permite
        # deshacer solo este INSERT y seguir con el resto (p. ej. en cargas masivas).
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
        self._con.rollback()

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
            nombre TEXT NOT NULL UNIQUE)""",
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
            perfil_json TEXT NOT NULL DEFAULT '{{}}',
            creado {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS postulaciones (
            id {pk},
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            convocatoria_id TEXT NOT NULL,
            nota TEXT,
            estado TEXT NOT NULL DEFAULT 'solicitada'
                CHECK (estado IN ('solicitada', 'aprobada', 'rechazada')),
            comentario_vri TEXT,
            creada {ts} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resuelta {ts},
            UNIQUE (usuario_id, convocatoria_id))""",
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
    ]


def iniciar_db(con: Conexion) -> None:
    for sentencia in _esquema():
        con.execute(sentencia)
    con.commit()
    # Bases creadas con una versión anterior: agrega las columnas del código temporal.
    for col, tipo in (("facultad", "TEXT"), ("codigo_expira", "INTEGER"), ("codigo_intentos", "INTEGER NOT NULL DEFAULT 0")):
        try:
            con.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {tipo}")
            con.commit()
        except Exception:
            con.rollback()  # ya existe


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
_SELECT_USUARIO = """
SELECT u.id, u.nombre, u.correo, u.rol, u.activo, u.consentimiento, u.perfil_json, u.facultad,
       u.semillero_id, s.nombre AS semillero, g.nombre AS grupo
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
    return u


def usuario_por_id(con, usuario_id: int) -> dict | None:
    return _usuario(con.execute(_SELECT_USUARIO + " WHERE u.id = ?", (usuario_id,)).fetchone())


def usuario_por_hash(con, codigo_hash: str) -> dict | None:
    return _usuario(con.execute(_SELECT_USUARIO + " WHERE u.codigo_hash = ?", (codigo_hash,)).fetchone())


def usuario_por_correo(con, correo: str) -> dict | None:
    return _usuario(con.execute(_SELECT_USUARIO + " WHERE LOWER(u.correo) = LOWER(?)",
                                ((correo or "").strip(),)).fetchone())


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


def listar_estudiantes(con) -> list[dict]:
    filas = con.execute("""
        SELECT u.id, u.nombre, u.correo, u.facultad, u.activo, u.consentimiento,
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
        for k in ("activo", "consentimiento", "tiene_perfil"):
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


# ---------------------------------------------------------------------------
# Postulaciones
# ---------------------------------------------------------------------------
def listar_postulaciones(con, usuario_id: int | None = None, estado: str | None = None) -> list[dict]:
    sql = """
        SELECT p.id, p.convocatoria_id, p.nota, p.estado, p.comentario_vri, p.creada, p.resuelta,
               u.id AS usuario_id, u.nombre AS estudiante, u.correo,
               s.nombre AS semillero, g.nombre AS grupo
        FROM postulaciones p
        JOIN usuarios u ON u.id = p.usuario_id
        LEFT JOIN semilleros s ON s.id = u.semillero_id
        LEFT JOIN grupos g ON g.id = s.grupo_id
        WHERE 1 = 1"""
    params: list = []
    if usuario_id is not None:
        sql += " AND p.usuario_id = ?"
        params.append(usuario_id)
    if estado:
        sql += " AND p.estado = ?"
        params.append(estado)
    sql += " ORDER BY p.creada DESC, p.id DESC"
    return [dict(f) for f in con.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# Convocatorias
# ---------------------------------------------------------------------------
def guardar_convocatoria(con, conv: dict, fuente: str = "manual", verificada: bool = True,
                         activa: bool = True, respetar_verificadas: bool = False) -> None:
    """Crea o actualiza una convocatoria (ya normalizada, con su `id`).
    respetar_verificadas=True: si una persona ya la verificó, no se sobrescribe
    (lo usan los scrapers para no pisar el trabajo humano)."""
    condicion = " WHERE convocatorias.verificada = 0" if respetar_verificadas else ""
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


def marcar_enlace(con, url: str, resultado: str, version: int = 1) -> None:
    """Recuerda que el recolector ya revisó este enlace (para no gastar IA otra vez)."""
    resultado = f"{resultado}|v{version}"
    con.execute("INSERT INTO enlaces_vistos (url, resultado) VALUES (?, ?) "
                "ON CONFLICT (url) DO UPDATE SET resultado = excluded.resultado, vista = CURRENT_TIMESTAMP",
                (url, resultado))


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


def contar_convocatorias(con) -> int:
    return con.execute("SELECT COUNT(*) AS n FROM convocatorias").fetchone()["n"]