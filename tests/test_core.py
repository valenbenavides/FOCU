"""Pruebas automáticas (no necesitan internet ni claves de IA).

Ejecutar desde la carpeta del proyecto:
    python -m unittest discover -s tests -v
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp()
os.environ["SECRET_KEY"] = "clave-de-prueba-" + "x" * 32
os.environ["DB_PATH"] = str(Path(TMP) / "prueba.db")
os.environ["EMBEDDINGS_PROVIDER"] = "none"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, str(RAIZ))

from openpyxl import Workbook  # noqa: E402

import acceso  # noqa: E402
import cargas  # noqa: E402
import convocatorias  # noqa: E402
import db  # noqa: E402
import matching  # noqa: E402
import seguridad  # noqa: E402
from scrapers import recolector  # noqa: E402


def excel(filas, encabezado=("nombre", "correo", "semillero", "grupo")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(encabezado)
    for f in filas:
        ws.append(f)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class BaseConDB(unittest.TestCase):
    def setUp(self):
        ruta = Path(os.environ["DB_PATH"])
        if ruta.exists():
            ruta.unlink()
        self.con = db.conectar()
        db.iniciar_db(self.con)

    def tearDown(self):
        self.con.close()


class TestSeguridad(unittest.TestCase):
    def test_codigo_temporal_formato_y_sal(self):
        c = seguridad.generar_codigo_temporal()
        self.assertRegex(c, r"^\d{6}$")
        self.assertEqual(seguridad.hash_codigo(c, "A@x.co"), seguridad.hash_codigo(c, " a@X.co "))
        self.assertNotEqual(seguridad.hash_codigo(c, "a@x.co"), seguridad.hash_codigo(c, "b@x.co"))

    def test_token(self):
        t = seguridad.crear_token(7)
        self.assertEqual(seguridad.leer_token(t), 7)
        self.assertIsNone(seguridad.leer_token(t + "x"))
        self.assertIsNone(seguridad.leer_token("basura"))

    def test_bloqueo_de_intentos(self):
        for _ in range(seguridad.MAX_FALLOS):
            seguridad.registrar_fallo("9.9.9.9")
        self.assertTrue(seguridad.login_bloqueado("9.9.9.9"))
        self.assertFalse(seguridad.login_bloqueado("8.8.8.8"))

    def test_limite_de_solicitudes(self):
        self.assertFalse(seguridad.limite_excedido("k", 2, 60))
        self.assertFalse(seguridad.limite_excedido("k", 2, 60))
        self.assertTrue(seguridad.limite_excedido("k", 2, 60))


class TestAcceso(BaseConDB):
    def setUp(self):
        super().setUp()
        cargas.agregar_estudiante(self.con, "Ana Pérez", "Ana@Unicauca.edu.co", "Solar", "Energía")
        cargas.agregar_administrador(self.con, "Dir VRI", "vri@unicauca.edu.co")
        self.con.commit()

    def test_flujo_correcto_y_un_solo_uso(self):
        u, codigo = acceso.emitir_codigo(self.con, " ana@unicauca.edu.co ")
        self.assertEqual(u["rol"], "estudiante")
        self.assertEqual(db.datos_codigo(self.con, u["id"])["codigo_hash"],
                         seguridad.hash_codigo(codigo, "ana@unicauca.edu.co"))
        logueado, _ = acceso.verificar_codigo(self.con, "ANA@unicauca.edu.co", codigo)
        self.assertEqual(logueado["id"], u["id"])
        otra, _ = acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", codigo)
        self.assertIsNone(otra)  # ya se usó

    def test_correo_no_registrado_o_inactivo(self):
        self.assertIsNone(acceso.emitir_codigo(self.con, "intruso@x.co"))
        u = db.usuario_por_correo(self.con, "ana@unicauca.edu.co")
        self.con.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (u["id"],))
        self.con.commit()
        self.assertIsNone(acceso.emitir_codigo(self.con, "ana@unicauca.edu.co"))

    def test_estudiante_sin_grupo_no_entra(self):
        self.con.execute("INSERT INTO usuarios (nombre, correo, rol, codigo_hash) VALUES ('Sin grupo','sg@x.co','estudiante','h')")
        self.con.commit()
        self.assertIsNone(acceso.emitir_codigo(self.con, "sg@x.co"))

    def test_codigo_vencido(self):
        u, codigo = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        self.con.execute("UPDATE usuarios SET codigo_expira = ? WHERE id = ?", (int(time.time()) - 1, u["id"]))
        self.con.commit()
        self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", codigo)[0])

    def test_intentos_limitados(self):
        u, codigo = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        for _ in range(seguridad.MAX_INTENTOS_CODIGO):
            self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", "000000" if codigo != "000000" else "111111")[0])
        # ya agotó los intentos: ni siquiera el código correcto sirve
        self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", codigo)[0])

    def test_nuevo_codigo_reemplaza_al_anterior(self):
        _, c1 = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        _, c2 = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        if c1 != c2:
            self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", c1)[0])
        self.assertIsNotNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", c2)[0])

    def test_director_entra_igual(self):
        u, codigo = acceso.emitir_codigo(self.con, "vri@unicauca.edu.co")
        self.assertEqual(acceso.verificar_codigo(self.con, "vri@unicauca.edu.co", codigo)[0]["rol"], "admin")
        self.assertEqual(db.correos_administradores(self.con), ["vri@unicauca.edu.co"])


class TestEstudiantes(BaseConDB):
    def test_alta_individual(self):
        a = cargas.agregar_estudiante(self.con, "  Ana Pérez ", "ana@unicauca.edu.co", "Semillero Solar", "Grupo Energía")
        self.con.commit()
        u = db.usuario_por_correo(self.con, "ANA@unicauca.edu.co")
        self.assertEqual((u["nombre"], u["semillero"], u["grupo"]), ("Ana Pérez", "Semillero Solar", "Grupo Energía"))
        self.assertEqual(u["rol"], "estudiante")
        self.assertNotIn("codigo", a)

    def test_duplicados_y_faltantes(self):
        cargas.agregar_estudiante(self.con, "Ana Pérez", "ana@x.co", "Semillero Solar", "Grupo Energía")
        casos = [
            (("ana pérez", "otro@x.co", "SEMILLERO SOLAR", "grupo energía"), "Ya existe"),
            (("Otra", "ANA@x.co", "Semillero Agro", "Grupo Agro"), "correo ya está registrado"),
            (("", "a@x.co", "S", "G"), "Falta: nombre"),
            (("X", "", "S", ""), "Falta: grupo"),
            (("X", "no-es-correo", "S", "G"), "correo no es válido"),
        ]
        for args, esperado in casos:
            with self.assertRaises(ValueError) as cm:
                cargas.agregar_estudiante(self.con, *args)
            self.assertIn(esperado, str(cm.exception))

    def test_excel(self):
        r = cargas.procesar_excel(self.con, excel([
            ["EJEMPLO - borrar", "e@x.co", "S0", "G0"],
            ["Ana Pérez", "ana@x.co", "Semillero Solar", "Grupo Energía"],
            ["Luis Gómez", "luis@x.co", "Semillero Solar", "Grupo Energía"],
            ["Ana Pérez", "ana@x.co", "Semillero Solar", "Grupo Energía"],
            ["", "z@x.co", "Semillero Agro", "Grupo Agro"],
            ["Sin Correo", None, "Semillero Agro", "Grupo Agro"],
            ["Sin Semillero", "ss@x.co", None, "Grupo Agro"],
            [None, None, None, None],
        ]))
        self.assertEqual([c["nombre"] for c in r["creados"]], ["Ana Pérez", "Luis Gómez", "Sin Correo", "Sin Semillero"])
        self.assertEqual(db.usuario_por_correo(self.con, "ss@x.co")["semillero"], cargas.SEMILLERO_GENERAL)
        self.assertEqual(len(r["omitidos"]), 3)
        r2 = cargas.procesar_excel(self.con, excel([["Ana Pérez", "ana@x.co", "Semillero Solar", "Grupo Energía"]]))
        self.assertEqual(r2["creados"], [])

    def test_sin_correo_y_completarlo_despues(self):
        r = cargas.procesar_excel(self.con, excel([["Ana", None, "S", "G"], ["Luis", "", "S", "G"]]))
        self.assertEqual(len(r["creados"]), 2)
        fila = self.con.execute("SELECT correo FROM usuarios WHERE nombre = 'Ana'").fetchone()
        self.assertIsNone(fila["correo"])
        # Sin correo no se puede ingresar: ni siquiera se emite un código.
        self.assertIsNone(acceso.emitir_codigo(self.con, ""))
        # Volver a subir el mismo Excel con el correo lo completa, sin duplicar.
        r2 = cargas.procesar_excel(self.con, excel([["Ana", "ana@x.co", "S", "G"], ["Luis", None, "S", "G"]]))
        self.assertEqual(r2["creados"], [])
        self.assertEqual([c["nombre"] for c in r2["actualizados"]], ["Ana"])
        self.assertEqual(len(r2["omitidos"]), 1)  # Luis sigue sin correo: ya existía
        self.assertEqual(db.usuario_por_correo(self.con, "ana@x.co")["nombre"], "Ana")
        self.assertEqual(self.con.execute("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'estudiante'").fetchone()["n"], 2)
        # Un correo ya usado por otra persona no se puede asignar.
        r3 = cargas.procesar_excel(self.con, excel([["Luis", "ana@x.co", "S", "G"]]))
        self.assertEqual(r3["actualizados"], [])
        self.assertIn("ya está registrado", r3["omitidos"][0]["motivo"])

    def test_facultad_y_descripcion_opcionales(self):
        r = cargas.procesar_excel(self.con, excel(
            [["Ana", "ana@x.co", "S", "G", "FACNED", "Efecto magnetocalórico en manganitas"],
             ["Luis", "luis@x.co", "S", "G", None, None]],
            encabezado=("nombre", "correo", "semillero", "grupo", "facultad", "descripcion")))
        self.assertEqual(len(r["creados"]), 2)
        ana = db.usuario_por_correo(self.con, "ana@x.co")
        self.assertEqual(ana["facultad"], "FACNED")
        self.assertEqual(ana["perfil"], {"descripcion_proyecto": "Efecto magnetocalórico en manganitas"})
        luis = db.usuario_por_correo(self.con, "luis@x.co")
        self.assertIsNone(luis["facultad"])
        self.assertEqual(luis["perfil"], {})

    def test_excel_invalido(self):
        with self.assertRaises(ValueError):
            cargas.procesar_excel(self.con, excel([["a"]], encabezado=("nombre",)))
        with self.assertRaises(ValueError):
            cargas.procesar_excel(self.con, excel([["a", "b", "c"]], encabezado=("nombre", "semillero", "facultad")))
        with self.assertRaises(ValueError):
            cargas.procesar_excel(self.con, b"no soy un excel")

    def test_plantilla_oficial(self):
        r = cargas.procesar_excel(self.con, (RAIZ / "plantilla_semilleros.xlsx").read_bytes())
        self.assertEqual(r["creados"], [])
        self.assertEqual(r["omitidos"][0]["motivo"], "Fila de ejemplo.")

    def test_administrador(self):
        adm = cargas.agregar_administrador(self.con, "Persona VRI", "vri@x.co")
        self.con.commit()
        self.assertEqual(db.usuario_por_correo(self.con, "vri@x.co")["rol"], "admin")
        self.assertTrue(db.hay_admin(self.con))
        with self.assertRaises(ValueError):
            cargas.agregar_administrador(self.con, "Otra", "VRI@x.co")

    def test_listados(self):
        cargas.agregar_estudiante(self.con, "Ana", "a@x.co", "Solar", "Energía")
        cargas.agregar_estudiante(self.con, "Luis", "l@x.co", "Solar", "Energía")
        cargas.agregar_estudiante(self.con, "Rosa", "r@x.co", "Café", "Agro")
        self.con.commit()
        grupos = {g["nombre"]: g for g in db.listar_semilleros(self.con)}
        self.assertEqual(grupos["Energía"]["semilleros"][0]["estudiantes"], 2)
        self.assertEqual(len(db.listar_estudiantes(self.con)), 3)


class TestConvocatorias(BaseConDB):
    def test_normalizacion(self):
        c = convocatorias.normalizar_convocatoria({
            "nombre": "  Prueba ", "fecha_limite": "2027-03-15T00:00:00", "monto_min": "5.000.000",
            "monto_max": 1000000, "areas": ["agro", "inventada"], "tipos_beneficiario": "docente"})
        self.assertEqual(c["nombre"], "Prueba")
        self.assertEqual(c["fecha_limite"], "2027-03-15")
        self.assertEqual((c["monto_min"], c["monto_max"]), (1000000, 5000000))   # se corrige el orden
        self.assertEqual(c["areas"], ["agro"])
        self.assertEqual(c["tipos_beneficiario"], ["docente"])
        self.assertIn("idea", c["fases"])             # sin dato: acepta todas
        self.assertTrue(c["id"].startswith("m-"))
        with self.assertRaises(ValueError):
            convocatorias.normalizar_convocatoria({"nombre": "  "})

    def test_guardar_ocultar_y_respetar_verificadas(self):
        c = convocatorias.normalizar_convocatoria({"id": "web-1", "nombre": "Original"})
        db.guardar_convocatoria(self.con, c, fuente="minciencias", verificada=False)
        self.con.commit()
        # el scraper puede actualizar una convocatoria sin verificar
        db.guardar_convocatoria(self.con, {**c, "nombre": "Actualizada"}, fuente="minciencias",
                                verificada=False, respetar_verificadas=True)
        self.assertEqual(db.obtener_convocatoria(self.con, "web-1")["nombre"], "Actualizada")
        # pero no pisa una que una persona ya verificó
        db.cambiar_estado_convocatoria(self.con, "web-1", verificada=True)
        db.guardar_convocatoria(self.con, {**c, "nombre": "Pisada"}, fuente="minciencias",
                                verificada=False, respetar_verificadas=True)
        conv = db.obtener_convocatoria(self.con, "web-1")
        self.assertEqual((conv["nombre"], conv["verificada"]), ("Actualizada", True))
        # ocultar la saca del listado normal
        db.cambiar_estado_convocatoria(self.con, "web-1", activa=False)
        self.assertEqual(db.listar_convocatorias(self.con, solo_activas=True), [])
        self.assertEqual(len(db.listar_convocatorias(self.con, solo_activas=False)), 1)

    def _sembrar_ejemplos(self):
        for raw in json.loads((RAIZ / "datos" / "convocatorias_ejemplo.json").read_text(encoding="utf-8")):
            db.guardar_convocatoria(self.con, convocatorias.normalizar_convocatoria(raw))
        self.con.commit()

    def test_ranking_y_brechas(self):
        self._sembrar_ejemplos()
        convs = db.listar_convocatorias(self.con)
        self.assertEqual(len(convs), 5)
        perfil = {"tipo_beneficiario": "grupo_semillero", "area_conocimiento": "ingenieria_tecnologia",
                  "alcance": "regional", "fase_proyecto": "prototipo", "monto_requerido_cop": 100000000,
                  "descripcion_proyecto": "prototipo de energía solar para comunidades rurales"}
        ranking = matching.rankear(perfil, convs)
        self.assertEqual(len(ranking), 5)
        # los elegibles van primero y ordenados por porcentaje
        elegibles = [r for r in ranking if r["elegible"]]
        self.assertEqual(ranking[: len(elegibles)], elegibles)
        self.assertEqual([r["porcentaje"] for r in elegibles], sorted([r["porcentaje"] for r in elegibles], reverse=True))
        por_id = {r["id"]: r for r in ranking}
        self.assertFalse(por_id["ej-5"]["elegible"])      # no va dirigida a semilleros
        self.assertTrue(por_id["ej-2"]["elegible"])
        self.assertIn("detalle", por_id["ej-2"])
        # brechas: el monto pedido supera el máximo de ej-1 (15 millones) y la fase/alcance no coinciden en ej-4
        b1 = matching.brechas(perfil, next(c for c in convs if c["id"] == "ej-1"))
        self.assertTrue(any("monto máximo" in x for x in b1))
        b5 = matching.brechas(perfil, next(c for c in convs if c["id"] == "ej-5"))
        self.assertTrue(any("tipo de beneficiario" in x for x in b5))

    def test_ranking_sin_descripcion(self):
        self._sembrar_ejemplos()
        r = matching.rankear({"tipo_beneficiario": "emprendedor"}, db.listar_convocatorias(self.con))
        self.assertTrue(all("afinidad_tematica" not in x["detalle"] for x in r))

    def test_afinidad_lexica(self):
        self.assertGreater(matching.afinidad_lexica("energía solar rural", "proyectos de energía solar"), 0.5)
        self.assertEqual(matching.afinidad_lexica("", "algo"), 0.0)


class TestPostulaciones(BaseConDB):
    def test_flujo(self):
        a = cargas.agregar_estudiante(self.con, "Ana", "ana@x.co", "Solar", "Energía")
        self.con.commit()
        pid = self.con.insertar("INSERT INTO postulaciones (usuario_id, convocatoria_id, nota) VALUES (?, ?, ?)",
                                (a["id"], "ej-1", "quiero"))
        self.con.commit()
        with self.assertRaises(db.ErrorIntegridad):
            self.con.insertar("INSERT INTO postulaciones (usuario_id, convocatoria_id) VALUES (?, ?)", (a["id"], "ej-1"))
        self.con.execute("UPDATE postulaciones SET estado = ?, comentario_vri = ?, resuelta = CURRENT_TIMESTAMP WHERE id = ?",
                         ("aprobada", "ok", pid))
        self.con.commit()
        self.assertEqual(db.listar_postulaciones(self.con, estado="solicitada"), [])
        p = db.listar_postulaciones(self.con, usuario_id=a["id"])[0]
        self.assertEqual((p["estudiante"], p["semillero"], p["grupo"], p["estado"]), ("Ana", "Solar", "Energía", "aprobada"))


class FalsaRespuesta:
    def __init__(self, texto, url="https://sitio.test/x", tipo="text/html"):
        self.text, self.content, self.url = texto, texto.encode(), url
        self.headers = {"content-type": tipo}


class TestRecolector(BaseConDB):
    LISTADO = """<html><body>
      <a href="/convocatorias/abierta-1">Convocatoria abierta uno</a>
      <a href="/convocatorias/abierta-2">Convocatoria dos</a>
      <a href="https://otro-sitio.test/convocatoria-externa">Externa</a>
      <a href="/noticias/">Noticias</a>
      <a href="javascript:void(0)">convocatoria falsa</a>
    </body></html>"""
    DETALLE = "<html><head><script>var x=1;</script></head><body><nav>menú</nav><p>" + ("Texto de la convocatoria. " * 20) + "</p></body></html>"

    def _fuente(self, **kw):
        return {"id": "prueba", "nombre": "Fuente de prueba", "entidad": "Entidad X",
                "url_listado": "https://sitio.test/convocatorias", "patron_enlaces": "/convocatorias/", **kw}

    def _descargar(self, url):
        return FalsaRespuesta(self.LISTADO if url.endswith("/convocatorias") else self.DETALLE, url=url)

    def test_extraer_enlaces(self):
        enlaces = recolector.extraer_enlaces(self.LISTADO, "https://sitio.test/convocatorias", self._fuente())
        self.assertEqual([e[0] for e in enlaces], ["https://sitio.test/convocatorias/abierta-1",
                                                    "https://sitio.test/convocatorias/abierta-2"])

    def test_texto_de_respuesta(self):
        texto = recolector.texto_de_respuesta(FalsaRespuesta(self.DETALLE))
        self.assertIn("Texto de la convocatoria", texto)
        self.assertNotIn("var x", texto)
        self.assertNotIn("menú", texto)

    def test_recolectar_guarda_y_descarta(self):
        def extraer(texto, url):
            if url.endswith("abierta-1"):
                return {"es_convocatoria": True, "nombre": "Convocatoria uno", "objetivo": "Objetivo",
                        "fecha_limite": "2027-05-01", "areas": ["agro"], "monto_max": 5000000}
            return {"es_convocatoria": False}
        inf = recolector.recolectar(self.con, [self._fuente()], descargar_fn=self._descargar,
                                    extraer_fn=extraer, pausa=0, revisar_robots=False)
        res = inf["fuentes"][0]
        self.assertEqual((res["enlaces"], res["guardadas"], res["descartadas"]), (2, 1, 1))
        guardadas = db.listar_convocatorias(self.con)
        self.assertEqual(len(guardadas), 1)
        c = guardadas[0]
        self.assertTrue(c["id"].startswith("web-"))
        self.assertFalse(c["verificada"])
        self.assertEqual((c["entidad"], c["fuente"], c["areas"]), ("Entidad X", "prueba", ["agro"]))
        self.assertEqual(c["url"], "https://sitio.test/convocatorias/abierta-1")

        # una persona la verifica: una nueva recolección ya no la pisa
        db.cambiar_estado_convocatoria(self.con, c["id"], verificada=True)
        recolector.recolectar(self.con, [self._fuente()], descargar_fn=self._descargar,
                              extraer_fn=lambda t, u: {"es_convocatoria": True, "nombre": "Cambiado"},
                              pausa=0, revisar_robots=False)
        self.assertEqual(db.obtener_convocatoria(self.con, c["id"])["nombre"], "Convocatoria uno")

    def test_fuente_sin_direccion_o_con_error(self):
        inf = recolector.recolectar(self.con, [self._fuente(url_listado="", activa=False)],
                                    descargar_fn=self._descargar, extraer_fn=lambda t, u: {}, pausa=0,
                                    revisar_robots=False)
        self.assertIn("sin dirección", inf["fuentes"][0]["errores"][0])

        def falla(url):
            raise RuntimeError("sin internet")
        inf = recolector.recolectar(self.con, [self._fuente()], descargar_fn=falla,
                                    extraer_fn=lambda t, u: {}, pausa=0, revisar_robots=False)
        self.assertIn("sin internet", inf["fuentes"][0]["errores"][0])
        self.assertEqual(inf["total_guardadas"], 0)

    def test_la_ia_falla_en_una_pagina_no_detiene_las_demas(self):
        def extraer(texto, url):
            if url.endswith("abierta-1"):
                raise ValueError("JSON inválido")
            return {"es_convocatoria": True, "nombre": "Convocatoria dos"}
        inf = recolector.recolectar(self.con, [self._fuente()], descargar_fn=self._descargar,
                                    extraer_fn=extraer, pausa=0, revisar_robots=False)
        res = inf["fuentes"][0]
        self.assertEqual(res["guardadas"], 1)
        self.assertEqual(len(res["errores"]), 1)


if __name__ == "__main__":
    unittest.main()
