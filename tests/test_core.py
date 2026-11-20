"""Pruebas automáticas, sin internet ni claves de IA: python -m unittest discover -s tests -q"""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from unittest import mock
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
        self.assertIsNone(otra)

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
        _, codigo = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        for _ in range(seguridad.MAX_INTENTOS_CODIGO):
            self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", "000000" if codigo != "000000" else "111111")[0])
        self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", codigo)[0])

    def test_nuevo_codigo_reemplaza_al_anterior(self):
        _, c1 = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        _, c2 = acceso.emitir_codigo(self.con, "ana@unicauca.edu.co")
        if c1 != c2:
            self.assertIsNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", c1)[0])
        self.assertIsNotNone(acceso.verificar_codigo(self.con, "ana@unicauca.edu.co", c2)[0])

    def test_director_entra_igual(self):
        _, codigo = acceso.emitir_codigo(self.con, "vri@unicauca.edu.co")
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
            (("Otra", "ANA@x.co", "Semillero Solar", "Grupo Energía"), "correo ya está registrado en este grupo"),
            (("", "a@x.co", "S", "G"), "Falta: nombre"),
            (("X", "", "S", ""), "Falta: grupo"),
            (("X", "no-es-correo", "S", "G"), "correo no es válido"),
        ]
        for args, esperado in casos:
            with self.assertRaises(ValueError) as cm:
                cargas.agregar_estudiante(self.con, *args)
            self.assertIn(esperado, str(cm.exception))

    def _crear_grupos(self, *nombres):
        for n in nombres:
            db.guardar_grupo(self.con, n)
        self.con.commit()

    def test_excel_no_crea_grupos_por_error_de_escritura(self):
        self._crear_grupos("Grupo Energía")
        r = cargas.procesar_excel(self.con, excel([
            ["Ana", "ana@x.co", "", "grupo energia"],
            ["Luis", "luis@x.co", "", "Grupo Energia Solar"],
        ]))
        self.assertEqual([c["nombre"] for c in r["creados"]], ["Ana"])
        self.assertEqual(r["creados"][0]["grupo"], "Grupo Energía")
        self.assertIn("no existe", r["omitidos"][0]["motivo"])
        self.assertEqual(len(db.listar_grupos(self.con)), 1)

    def test_excel_con_cargo_director(self):
        self._crear_grupos("Grupo A")
        r = cargas.procesar_excel(self.con, excel([
            ["Dra. Ana", "ana@x.co", "", "Grupo A", "Director/a"],
            ["Luis", "luis@x.co", "Solar", "Grupo A", "Estudiante"],
            ["Pedro", "pedro@x.co", "", "Grupo A", ""],
            ["Rosa", "rosa@x.co", "", "Grupo A", "Decano"],
        ], encabezado=("nombre", "correo", "semillero", "grupo", "cargo")))
        self.assertEqual([(c["nombre"], c["cargo"]) for c in r["creados"]],
                         [("Dra. Ana", "Director/a"), ("Luis", "Estudiante"), ("Pedro", "Estudiante")])
        self.assertIn("Cargo no válido", r["omitidos"][0]["motivo"])
        self.assertEqual(db.usuario_por_correo(self.con, "ana@x.co")["es_director_grupo"], 1)
        self.assertEqual(db.usuario_por_correo(self.con, "luis@x.co")["es_director_grupo"], 0)

    def test_plantilla_con_lista_de_grupos(self):
        import openpyxl
        self._crear_grupos("Grupo A", "Grupo B")
        datos = cargas.plantilla_con_grupos(self.con, RAIZ / "plantilla_personas.xlsx")
        wb = openpyxl.load_workbook(io.BytesIO(datos))
        ws = wb["Estudiantes"]
        formulas = {str(v.sqref): v.formula1 for v in ws.data_validations.dataValidation}
        ref = formulas["D2:D2000"]
        letra = ref.split(":")[0].lstrip("$").rstrip("0123456789$")
        self.assertTrue(ws.column_dimensions[letra].hidden)
        self.assertEqual([ws[f"{letra}{i}"].value for i in (2, 3)], ["Grupo A", "Grupo B"])
        self.assertIn("Director/a", formulas["G2:G2000"])
        ref_f = formulas["E2:E2000"]
        letra_f = ref_f.split(":")[0].lstrip("$").rstrip("0123456789$")
        self.assertTrue(ws.column_dimensions[letra_f].hidden)
        self.assertEqual(ws[f"{letra_f}2"].value, "Facultad de Artes")

    def test_excel(self):
        self._crear_grupos("Grupo Energía", "Grupo Agro")
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
        self.assertEqual([c["nombre"] for c in r["creados"]], ["Ana Pérez", "Luis Gómez", "Sin Semillero"])
        self.assertIsNotNone(db.usuario_por_correo(self.con, "ss@x.co"))
        self.assertEqual(len(r["omitidos"]), 4)
        self.assertIn("Falta: correo", [o["motivo"] for o in r["omitidos"] if o["fila"] == 7][0])
        r2 = cargas.procesar_excel(self.con, excel([["Ana Pérez", "ana@x.co", "Semillero Solar", "Grupo Energía"]]))
        self.assertEqual(r2["creados"], [])

    def test_sin_correo_y_completarlo_despues(self):
        cargas.agregar_estudiante(self.con, "Ana", None, "S", "G")
        cargas.agregar_estudiante(self.con, "Luis", "", "S", "G")
        fila = self.con.execute("SELECT correo FROM usuarios WHERE nombre = 'Ana'").fetchone()
        self.assertIsNone(fila["correo"])
        self.assertIsNone(acceso.emitir_codigo(self.con, ""))
        r2 = cargas.procesar_excel(self.con, excel([["Ana", "ana@x.co", "S", "G"], ["Luis", None, "S", "G"]]))
        self.assertEqual(r2["creados"], [])
        self.assertEqual([c["nombre"] for c in r2["actualizados"]], ["Ana"])
        self.assertEqual(len(r2["omitidos"]), 1)
        self.assertEqual(db.usuario_por_correo(self.con, "ana@x.co")["nombre"], "Ana")
        self.assertEqual(self.con.execute("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'estudiante'").fetchone()["n"], 2)
        r3 = cargas.procesar_excel(self.con, excel([["Luis", "ana@x.co", "S", "G"]]))
        self.assertEqual(r3["actualizados"], [])
        self.assertIn("ya está registrado", r3["omitidos"][0]["motivo"])

    def test_facultad_y_descripcion_opcionales(self):
        self._crear_grupos("G")
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
        r = cargas.procesar_excel(self.con, (RAIZ / "plantilla_personas.xlsx").read_bytes())
        self.assertEqual(r["creados"], [])
        self.assertEqual(r["omitidos"], [])

    def test_administrador(self):
        cargas.agregar_administrador(self.con, "Persona VRI", "vri@x.co")
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


class TestRegistroEnGrupos(BaseConDB):
    """Grupos, directores y solicitudes de ingreso."""

    def _grupo(self, nombre="Grupo Uno", director="Dra. Ana Líder", correo="ana@unicauca.edu.co"):
        gid, nuevo = db.guardar_grupo(self.con, nombre, "Materiales cerámicos y sensores.", "ingenieria_tecnologia",
                                      "FACNED", "COL0001")
        self.assertTrue(nuevo)
        if director:
            cargas.asignar_director(self.con, gid, director, correo)
        self.con.commit()
        return gid

    def test_director_con_semillero_opcional(self):
        gid = self._grupo(director=None)
        cargas.asignar_director(self.con, gid, "Dir Uno", "uno@x.co", adicional=True)
        cargas.asignar_director(self.con, gid, "Dir Dos", "dos@x.co", adicional=True, semillero="Semillero Solar")
        self.con.commit()
        filas = {r["nombre"]: r["semillero"] for r in db.listar_estudiantes(self.con)}
        self.assertEqual(filas["Dir Dos"], "Semillero Solar")
        self.assertIn("sin semillero", filas["Dir Uno"].lower())

    def test_grupo_y_director_unico(self):
        gid = self._grupo()
        g = db.listar_grupos(self.con)[0]
        self.assertEqual((g["nombre"], g["director"], g["director_correo"], g["miembros"]),
                         ("Grupo Uno", "Dra. Ana Líder", "ana@unicauca.edu.co", 0))
        cargas.asignar_director(self.con, gid, "Ana Líder Pérez", "ana.perez@unicauca.edu.co")
        self.con.commit()
        self.assertEqual(len(db.directores_de_grupo(self.con, gid)), 1)
        self.assertEqual(db.listar_grupos(self.con)[0]["director_correo"], "ana.perez@unicauca.edu.co")
        u, codigo = acceso.emitir_codigo(self.con, "ana.perez@unicauca.edu.co")
        self.assertTrue(u["es_director_grupo"])
        self.assertEqual(u["grupo_id"], gid)

    def test_director_sin_correo_y_grupo_repetido(self):
        gid = self._grupo(director="Prof. Sin Correo", correo="")
        self.assertEqual(db.correos_directores_de_grupo(self.con, gid), [])
        cargas.asignar_director(self.con, gid, "Prof. Sin Correo", "sin@unicauca.edu.co")
        self.con.commit()
        self.assertEqual(db.correos_directores_de_grupo(self.con, gid), ["sin@unicauca.edu.co"])
        gid2, nuevo = db.guardar_grupo(self.con, "grupo uno", "otra descripción")
        self.assertEqual((gid2, nuevo), (gid, False))
        self.assertEqual(db.obtener_grupo(self.con, gid)["descripcion"], "otra descripción")

    def test_registro_verificacion_aprobacion_y_ingreso(self):
        gid = self._grupo()
        codigo = acceso.emitir_codigo_registro(self.con, "Luis Nuevo", "Luis@Correo.co", gid, "Semillero Sensores")
        self.assertIsNone(acceso.verificar_codigo_registro(self.con, "luis@correo.co", "000000"
                                                           if codigo != "000000" else "111111"))
        sol = acceso.verificar_codigo_registro(self.con, "luis@correo.co", codigo)
        self.assertEqual(sol["estado"], "pendiente")
        self.assertIsNone(acceso.verificar_codigo_registro(self.con, "luis@correo.co", codigo))
        self.assertIsNone(acceso.emitir_codigo(self.con, "luis@correo.co"))
        pendientes = db.listar_solicitudes(self.con, grupo_id=gid)
        self.assertEqual([p["nombre"] for p in pendientes], ["Luis Nuevo"])
        self.assertFalse(pendientes[0]["sin_director"])
        r = cargas.aprobar_solicitud(self.con, db.obtener_solicitud(self.con, sol["id"]))
        db.marcar_solicitud(self.con, sol["id"], "aprobada", None, 1)
        self.con.commit()
        self.assertFalse(r["ya_existia"])
        u, codigo2 = acceso.emitir_codigo(self.con, "luis@correo.co")
        self.assertEqual((u["grupo"], u["semillero"], u["es_director_grupo"]), ("Grupo Uno", "Semillero Sensores", False))
        self.assertIn("cerámicos", u["perfil"]["descripcion_proyecto"])
        self.assertEqual(db.listar_solicitudes(self.con, grupo_id=gid), [])

    def test_codigo_de_registro_vencido_o_con_intentos_agotados(self):
        gid = self._grupo()
        codigo = acceso.emitir_codigo_registro(self.con, "Eva", "eva@x.co", gid, "")
        for _ in range(seguridad.MAX_INTENTOS_CODIGO):
            acceso.verificar_codigo_registro(self.con, "eva@x.co", "999999" if codigo != "999999" else "888888")
        self.assertIsNone(acceso.verificar_codigo_registro(self.con, "eva@x.co", codigo))
        codigo = acceso.emitir_codigo_registro(self.con, "Eva", "eva@x.co", gid, "")
        self.con.execute("UPDATE solicitudes_registro SET codigo_expira = ?", (int(time.time()) - 5,))
        self.assertIsNone(acceso.verificar_codigo_registro(self.con, "eva@x.co", codigo))

    def test_grupo_sin_director_lo_atiende_la_vri(self):
        gid = self._grupo(director="")
        cargas.agregar_administrador(self.con, "VRI Uno", "vri@unicauca.edu.co")
        self.con.commit()
        codigo = acceso.emitir_codigo_registro(self.con, "Ana", "ana2@x.co", gid, "")
        acceso.verificar_codigo_registro(self.con, "ana2@x.co", codigo)
        self.assertTrue(db.listar_solicitudes(self.con)[0]["sin_director"])
        self.assertEqual(db.correos_directores_de_grupo(self.con, gid), [])
        self.assertEqual(db.correos_administradores(self.con), ["vri@unicauca.edu.co"])

    def test_aprobar_si_el_correo_ya_estaba_registrado(self):
        gid = self._grupo()
        cargas.agregar_estudiante(self.con, "Pedro", "pedro@x.co", "", "Grupo Uno")
        self.con.commit()
        codigo = acceso.emitir_codigo_registro(self.con, "Pedro", "pedro@x.co", gid, "")
        sol = acceso.verificar_codigo_registro(self.con, "pedro@x.co", codigo)
        self.assertTrue(cargas.aprobar_solicitud(self.con, sol)["ya_existia"])

    def test_director_agrega_a_mano_y_lista_su_grupo(self):
        gid = self._grupo()
        cargas.agregar_estudiante(self.con, "Mario", "mario@x.co", "", "Grupo Uno")
        cargas.agregar_estudiante(self.con, "Otra", "otra@x.co", "", "Otro Grupo")
        self.con.commit()
        miembros = db.listar_miembros_de_grupo(self.con, gid)
        self.assertEqual([m["nombre"] for m in miembros], ["Dra. Ana Líder", "Mario"])
        self.assertTrue(miembros[0]["es_director_grupo"])
        self.assertEqual(db.listar_grupos(self.con)[1]["miembros"] if len(db.listar_grupos(self.con)) > 1 else 0, 1)

    def test_el_integrante_aprobado_hereda_la_facultad_del_grupo(self):
        gid, _ = db.guardar_grupo(self.con, "Grupo Fac", "Sensores.", "ingenieria_tecnologia",
                                  "Facultad de Ingeniería Civil")
        cargas.asignar_director(self.con, gid, "Dir Uno", "dir@unicauca.edu.co")
        self.con.commit()
        codigo = acceso.emitir_codigo_registro(self.con, "Nuevo", "nuevo@x.co", gid, "")
        sol = acceso.verificar_codigo_registro(self.con, "nuevo@x.co", codigo)
        cargas.aprobar_solicitud(self.con, sol)
        self.con.commit()
        u = db.usuario_por_correo(self.con, "nuevo@x.co")
        self.assertEqual((u["facultad"], u["grupo"]), ("Facultad de Ingeniería Civil", "Grupo Fac"))
        self.assertTrue(cargas.mismo_texto("Facultad de Artes \n", "facultad de artes"))
        self.assertFalse(cargas.mismo_texto("Facultad de Artes", "Facultad de Derecho"))

    def test_una_persona_puede_tener_perfiles_en_varios_grupos_y_en_la_vri(self):
        g1 = self._grupo("Grupo A", "Dir A", "a@unicauca.edu.co")
        g2, _ = db.guardar_grupo(self.con, "Grupo B")
        cargas.asignar_director(self.con, g2, "Dir A", "a@unicauca.edu.co")
        cargas.agregar_administrador(self.con, "Vri", "vri@unicauca.edu.co")
        cargas.asignar_director(self.con, g1, "Vri", "vri@unicauca.edu.co", adicional=True)
        perfiles = db.usuarios_por_correo(self.con, "vri@unicauca.edu.co")
        self.assertEqual([p["rol"] for p in perfiles], ["admin", "estudiante"])
        self.assertEqual(db.usuario_por_correo(self.con, "vri@unicauca.edu.co")["rol"], "admin")
        self.assertEqual(len(db.usuarios_por_correo(self.con, "a@unicauca.edu.co")), 2)
        with self.assertRaises(ValueError):
            cargas.agregar_estudiante(self.con, "Otra", "a@unicauca.edu.co", "General", "Grupo A")
        with self.assertRaises(ValueError):
            cargas.agregar_administrador(self.con, "Vri2", "vri@unicauca.edu.co")

    def test_ingreso_con_correo_de_varios_perfiles(self):
        g1 = self._grupo("Grupo A", "Dir A", "a@unicauca.edu.co")
        cargas.agregar_administrador(self.con, "Vri", "vri@unicauca.edu.co")
        cargas.asignar_director(self.con, g1, "Vri", "vri@unicauca.edu.co", adicional=True)
        self.assertEqual(acceso.estado_de_correo(self.con, "vri@unicauca.edu.co"), "ok")
        u, codigo = acceso.emitir_codigo(self.con, "vri@unicauca.edu.co")
        self.assertEqual(u["rol"], "admin")
        entrada, _ = acceso.verificar_codigo(self.con, "vri@unicauca.edu.co", codigo)
        self.assertEqual(entrada["id"], u["id"])

    def test_el_asistente_de_la_vri_consulta_la_base(self):
        import agente_vri
        self._grupo("Grupo Solar", "Dir Solar", "solar@unicauca.edu.co")
        self._grupo("Grupo Sin Correo", "Dir X", "")
        ctx = {"con": self.con}
        f = agente_vri.crear_chat_vri(ctx).funciones
        r = f["resumen_general"]()
        self.assertEqual(r["grupos"]["total"], 2)
        self.assertEqual(r["grupos"]["sin_director_o_sin_correo"], 1)
        self.assertEqual(f["buscar_grupos"](texto="solar")["total"], 1)
        self.assertEqual(f["buscar_grupos"](sin_director=True)["grupos"][0]["nombre"], "Grupo Sin Correo")
        self.assertEqual(f["detalle_grupo"]("Grupo Solar")["director"], "Dir Solar")
        self.assertIn("error", f["detalle_grupo"]("no existe"))
        self.assertEqual(f["buscar_personas"]("solar@")["total"], 1)
        self.assertEqual(f["listar_postulaciones"]()["total"], 0)
        self.assertEqual(f["buscar_convocatorias"](estado="verificadas")["total"], 0)
        self.assertIn("error", f["detalle_convocatoria"]("inexistente"))

    def test_eliminar_una_postulacion_borra_tambien_su_lista(self):
        g = self._grupo("Grupo Cancela", "Dir C", "c@unicauca.edu.co")
        uid = db.directores_de_grupo(self.con, g)[0]["id"]
        pid = self.con.insertar("INSERT INTO postulaciones (usuario_id, grupo_id, convocatoria_id) VALUES (?, ?, ?)", (uid, g, "x1"))
        db.guardar_requisito(self.con, pid, "aval_departamento", "si", None, uid)
        self.assertIsNotNone(db.obtener_postulacion(self.con, pid))
        db.eliminar_postulacion(self.con, pid)
        self.assertIsNone(db.obtener_postulacion(self.con, pid))
        self.assertEqual(db.obtener_requisitos(self.con, pid), {})

    def test_estado_del_correo_al_ingresar(self):
        gid = self._grupo()
        est = acceso.estado_de_correo
        self.assertEqual(est(self.con, "ana@unicauca.edu.co"), "ok")
        self.assertEqual(est(self.con, " ANA@unicauca.edu.co "), "ok")
        self.assertEqual(est(self.con, "nadie@x.co"), "no_registrado")
        codigo = acceso.emitir_codigo_registro(self.con, "Luis", "luis@x.co", gid, "")
        self.assertEqual(est(self.con, "luis@x.co"), "no_registrado")
        sol = acceso.verificar_codigo_registro(self.con, "luis@x.co", codigo)
        self.assertEqual(est(self.con, "luis@x.co"), "pendiente")
        db.marcar_solicitud(self.con, sol["id"], "rechazada", None, 1)
        self.con.commit()
        self.assertEqual(est(self.con, "luis@x.co"), "rechazada")
        cargas.agregar_estudiante(self.con, "Pedro", "pedro@x.co", "", "Grupo Uno")
        self.con.execute("UPDATE usuarios SET activo = 0 WHERE LOWER(correo) = 'pedro@x.co'")
        self.con.commit()
        self.assertEqual(est(self.con, "pedro@x.co"), "desactivado")
        cargas.agregar_administrador(self.con, "Vri", "vri@unicauca.edu.co")
        self.con.commit()
        self.assertEqual(est(self.con, "vri@unicauca.edu.co"), "ok")
        self.con.execute("INSERT INTO usuarios (nombre, correo, rol, activo, codigo_hash) "
                         "VALUES ('Suelto', 'suelto@x.co', 'estudiante', 1, 'x')")
        self.con.commit()
        self.assertEqual(est(self.con, "suelto@x.co"), "sin_grupo")


class TestConvocatorias(BaseConDB):
    def test_normalizacion(self):
        c = convocatorias.normalizar_convocatoria({
            "nombre": "  Prueba ", "fecha_limite": "2027-03-15T00:00:00", "monto_min": "5.000.000",
            "monto_max": 1000000, "areas": ["agro", "inventada"], "tipos_beneficiario": "docente"})
        self.assertEqual(c["nombre"], "Prueba")
        self.assertEqual(c["fecha_limite"], "2027-03-15")
        self.assertEqual((c["monto_min"], c["monto_max"]), (1000000, 5000000))
        self.assertEqual(c["areas"], ["agro"])
        self.assertEqual(c["tipos_beneficiario"], ["docente"])
        self.assertIn("idea", c["fases"])
        self.assertTrue(c["id"].startswith("m-"))
        with self.assertRaises(ValueError):
            convocatorias.normalizar_convocatoria({"nombre": "  "})

    def test_guardar_ocultar_y_respetar_verificadas(self):
        c = convocatorias.normalizar_convocatoria({"id": "web-1", "nombre": "Original"})
        db.guardar_convocatoria(self.con, c, fuente="minciencias", verificada=False)
        self.con.commit()
        db.guardar_convocatoria(self.con, {**c, "nombre": "Actualizada"}, fuente="minciencias",
                                verificada=False, respetar_verificadas=True)
        self.assertEqual(db.obtener_convocatoria(self.con, "web-1")["nombre"], "Actualizada")
        db.cambiar_estado_convocatoria(self.con, "web-1", verificada=True)
        db.guardar_convocatoria(self.con, {**c, "nombre": "Pisada"}, fuente="minciencias",
                                verificada=False, respetar_verificadas=True)
        conv = db.obtener_convocatoria(self.con, "web-1")
        self.assertEqual((conv["nombre"], conv["verificada"]), ("Actualizada", True))
        db.cambiar_estado_convocatoria(self.con, "web-1", activa=False)
        self.assertEqual(db.listar_convocatorias(self.con, solo_activas=True), [])
        self.assertEqual(len(db.listar_convocatorias(self.con, solo_activas=False)), 1)

    def _sembrar_ejemplos(self):
        for raw in json.loads((RAIZ / "tests" / "convocatorias_prueba.json").read_text(encoding="utf-8")):
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
        elegibles = [r for r in ranking if r["elegible"]]
        self.assertEqual(ranking[: len(elegibles)], elegibles)
        self.assertEqual([r["porcentaje"] for r in elegibles], sorted([r["porcentaje"] for r in elegibles], reverse=True))
        por_id = {r["id"]: r for r in ranking}
        self.assertFalse(por_id["ej-5"]["elegible"])
        self.assertTrue(por_id["ej-2"]["elegible"])
        self.assertIn("detalle", por_id["ej-2"])
        b1 = matching.brechas(perfil, next(c for c in convs if c["id"] == "ej-1"))
        self.assertTrue(any("monto máximo" in x for x in b1))
        b5 = matching.brechas(perfil, next(c for c in convs if c["id"] == "ej-5"))
        self.assertTrue(any("tipo de beneficiario" in x for x in b5))

    def test_ranking_sin_descripcion(self):
        self._sembrar_ejemplos()
        r = matching.rankear({"tipo_beneficiario": "emprendedor"}, db.listar_convocatorias(self.con))
        self.assertTrue(all("afinidad_tematica" not in x["detalle"] for x in r))

    def test_tipo_fijo_opcional(self):
        import schema
        from schema import con_tipo_fijo
        with mock.patch.object(schema, "TIPO_FIJO", "grupo_investigacion"):
            return self._tipo_fijo(con_tipo_fijo)

    def _tipo_fijo(self, con_tipo_fijo):
        self.assertEqual(con_tipo_fijo({"alcance": "nacional"}),
                         {"alcance": "nacional", "tipo_beneficiario": "grupo_investigacion"})
        self.assertEqual(con_tipo_fijo({"tipo_beneficiario": "emprendedor"})["tipo_beneficiario"], "grupo_investigacion")
        conv = {"id": "x1", "nombre": "Solo emprendedores", "entidad": "E", "tipos_beneficiario": ["emprendedor"],
                "areas": ["multidisciplinar"], "alcances": ["nacional"], "fases": ["idea"], "fecha_limite": None,
                "requisitos": "", "beneficios": "", "url": "", "objetivo": ""}
        sin_tipo = matching.rankear({"alcance": "nacional"}, [conv])[0]
        con_tipo = matching.rankear(con_tipo_fijo({"alcance": "nacional"}), [conv])[0]
        self.assertTrue(sin_tipo["elegible"])
        self.assertFalse(con_tipo["elegible"])

    def test_el_agente_recibe_los_datos_ya_registrados(self):
        import agente
        t = agente.sistema_con_datos("Ana", "GICO", "Ingeniería", {"area_conocimiento": "salud", "monto_requerido_cop": 5000000})
        self.assertIn("Grupo de investigación: GICO", t)
        self.assertIn("Área de conocimiento: salud", t)
        self.assertIn("aún falta preguntar", t)
        self.assertNotIn("falta preguntar: Área", t)
        completo = {"tipo_beneficiario": "docente", "area_conocimiento": "salud", "alcance": "nacional",
                    "fase_proyecto": "idea", "monto_requerido_cop": 1, "descripcion_proyecto": "x"}
        self.assertIn("perfil está completo", agente.sistema_con_datos("Ana", "", "", completo))

    def test_el_historial_guarda_varias_conversaciones(self):
        uid = self.con.execute("INSERT INTO usuarios (nombre, rol, codigo_hash) VALUES ('Ana','estudiante','x')").lastrowid
        self.con.commit()
        c1 = db.crear_conversacion(self.con, uid, "Busco una beca")
        c2 = db.crear_conversacion(self.con, uid, "Mi proyecto de robótica")
        db.guardar_mensaje_chat(self.con, uid, "usuario", "hola", c1)
        db.guardar_mensaje_chat(self.con, uid, "asistente", "¡Hola!", c1)
        db.guardar_mensaje_chat(self.con, uid, "usuario", "otra", c2)
        self.assertEqual(db.mensajes_de_conversacion(self.con, uid, c1),
                         [{"rol": "usuario", "texto": "hola"}, {"rol": "asistente", "texto": "¡Hola!"}])
        self.assertEqual([c["id"] for c in db.listar_conversaciones(self.con, uid)], [c2, c1])
        db.borrar_conversacion(self.con, uid, c1)
        self.assertEqual([c["id"] for c in db.listar_conversaciones(self.con, uid)], [c2])
        self.assertEqual(db.mensajes_de_conversacion(self.con, uid, c1), [])
        db.borrar_chat(self.con, uid)
        self.assertEqual(db.listar_conversaciones(self.con, uid), [])

    def test_otra_persona_no_ve_mi_conversacion(self):
        a = self.con.execute("INSERT INTO usuarios (nombre, rol, codigo_hash) VALUES ('Ana','estudiante','x')").lastrowid
        b = self.con.execute("INSERT INTO usuarios (nombre, rol, codigo_hash) VALUES ('Beto','estudiante','y')").lastrowid
        self.con.commit()
        c = db.crear_conversacion(self.con, a, "Privada")
        db.guardar_mensaje_chat(self.con, a, "usuario", "secreto", c)
        self.assertEqual(db.mensajes_de_conversacion(self.con, b, c), [])
        db.borrar_conversacion(self.con, b, c)
        self.assertEqual(len(db.mensajes_de_conversacion(self.con, a, c)), 1)

    def test_el_historial_se_borra_a_los_4_dias_y_tiene_tope(self):
        uid = self.con.execute("INSERT INTO usuarios (nombre, rol, codigo_hash) VALUES ('Beto','estudiante','x')").lastrowid
        self.con.commit()
        self.assertEqual(db.CHAT_DIAS, 4)
        vieja = db.crear_conversacion(self.con, uid, "Vieja")
        db.guardar_mensaje_chat(self.con, uid, "usuario", "viejo", vieja)
        self.con.execute("UPDATE chat_conversaciones SET actualizado = '2020-01-01 00:00:00' WHERE id = ?", (vieja,))
        self.con.commit()
        nueva = db.crear_conversacion(self.con, uid, "Nueva")
        self.assertEqual([c["id"] for c in db.listar_conversaciones(self.con, uid)], [nueva])
        n = self.con.execute("SELECT COUNT(*) AS n FROM chat_mensajes WHERE texto = 'viejo'").fetchone()["n"]
        self.assertEqual(n, 0)
        with mock.patch.object(db, "CHAT_MAX_MENSAJES", 5):
            for i in range(8):
                db.guardar_mensaje_chat(self.con, uid, "usuario", f"m{i}", nueva)
        self.assertEqual([m["texto"] for m in db.mensajes_de_conversacion(self.con, uid, nueva)],
                         ["m3", "m4", "m5", "m6", "m7"])

    def test_la_lista_de_requisitos_se_adapta_a_la_convocatoria(self):
        import requisitos
        interna = {"nombre": "Convocatoria de proyectos de investigación 2026", "entidad": "Vicerrectoría de Investigaciones, Universidad del Cauca"}
        self.assertIs(requisitos.lista_para(interna), requisitos.REQUISITOS)
        beca = {"nombre": "Becas doctorado China", "entidad": "ICETEX",
                "requisitos_lista": ["Ser colombiano", "Tener título profesional", "Ser colombiano", "Carta de motivación"]}
        items = requisitos.lista_para(beca)
        self.assertEqual([i["titulo"] for i in items], ["Ser colombiano", "Tener título profesional", "Carta de motivación"])
        self.assertEqual(requisitos.avales_faltantes({}, items), [])
        r = requisitos.resumen({items[0]["clave"]: "si"}, items)
        self.assertEqual((r["cumplidos"], r["total"], r["departamento"]), (1, 3, None))
        requisitos.validar_cambio({}, items[0]["clave"], "si", items)
        with self.assertRaises(ValueError):
            requisitos.validar_cambio({}, "aval_departamento", "si", items)
        self.assertEqual(len(requisitos.lista_para({"nombre": "X", "entidad": "Y"})), len(requisitos.BASICOS))
        otra = requisitos.lista_para({"nombre": "X", "entidad": "Y", "requisitos": "Ser mayor de edad. Tener pasaporte vigente."})
        self.assertEqual(len(otra), 2)

    def test_la_postulacion_pasa_primero_por_el_director(self):
        a = cargas.agregar_estudiante(self.con, "Ana", "ana@x.co", "General", "Grupo Uno")
        b = cargas.agregar_estudiante(self.con, "Beto", "beto@x.co", "General", "Grupo Dos")
        self.con.commit()
        ids = {}
        for nombre, usuario, etapa in (("a", a["id"], "pendiente"), ("b", b["id"], "aprobada")):
            gid = self.con.execute("SELECT s.grupo_id AS g FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id "
                                   "WHERE u.id = ?", (usuario,)).fetchone()["g"]
            ids[nombre] = self.con.insertar(
                "INSERT INTO postulaciones (usuario_id, grupo_id, convocatoria_id, etapa_grupo) VALUES (?, ?, ?, ?)",
                (usuario, gid, "c1", etapa))
        self.con.commit()
        pa = db.obtener_postulacion(self.con, ids["a"])
        self.assertEqual(pa["fase"], "en_director")
        self.assertEqual(db.obtener_postulacion(self.con, ids["b"])["fase"], "en_vri")
        solo_grupo = db.listar_postulaciones(self.con, grupo_id=pa["grupo_id"], etapa_grupo="pendiente")
        self.assertEqual([f["id"] for f in solo_grupo], [ids["a"]])
        vri = db.listar_postulaciones(self.con, etapa_grupo="aprobada")
        self.assertEqual([f["id"] for f in vri], [ids["b"]])
        self.con.execute("UPDATE postulaciones SET etapa_grupo = 'devuelta' WHERE id = ?", (ids["a"],))
        self.con.commit()
        self.assertEqual(db.obtener_postulacion(self.con, ids["a"])["fase"], "devuelta")
        self.assertEqual(db.fase_postulacion({"estado": "aprobada", "etapa_grupo": "aprobada"}), "aprobada")
        self.assertEqual(db.fase_postulacion({"estado": "rechazada", "etapa_grupo": "pendiente"}), "rechazada")
        self.assertEqual(db.fase_postulacion({"estado": "solicitada"}), "en_vri")

    def test_la_postulacion_es_del_grupo_y_se_migra(self):
        a = cargas.agregar_estudiante(self.con, "Ana", "a1@x.co", "General", "Grupo Cinco")
        b = cargas.agregar_estudiante(self.con, "Beto", "b1@x.co", "General", "Grupo Cinco")
        self.con.commit()
        gid = self.con.execute("SELECT s.grupo_id AS g FROM usuarios u JOIN semilleros s ON s.id = u.semillero_id "
                               "WHERE u.id = ?", (a["id"],)).fetchone()["g"]
        pid = self.con.insertar("INSERT INTO postulaciones (usuario_id, convocatoria_id) VALUES (?, ?)", (a["id"], "cX"))
        self.con.commit()
        db.iniciar_db(self.con)
        self.assertEqual(db.obtener_postulacion(self.con, pid)["grupo_id"], gid)
        previa = db.postulacion_del_grupo(self.con, gid, "cX")
        self.assertEqual(previa["estudiante"], "Ana")
        self.assertEqual([f["id"] for f in db.listar_postulaciones(self.con, grupo_id=gid)], [pid])
        self.assertIsNone(db.postulacion_del_grupo(self.con, gid, "otra"))
        with self.assertRaises(db.ErrorIntegridad):
            self.con.insertar("INSERT INTO postulaciones (usuario_id, grupo_id, convocatoria_id) VALUES (?, ?, ?)",
                              (b["id"], gid, "cX"))

    def test_lista_de_requisitos_y_avales(self):
        import requisitos
        with self.assertRaises(ValueError):
            requisitos.validar_cambio({}, "aval_facultad", "si")
        requisitos.validar_cambio({"aval_departamento": "si"}, "aval_facultad", "si")
        with self.assertRaises(ValueError):
            requisitos.validar_cambio({"aval_departamento": "si", "aval_facultad": "si"}, "aval_departamento", "pendiente")
        with self.assertRaises(ValueError):
            requisitos.validar_cambio({}, "aval_departamento", "no_aplica")
        requisitos.validar_cambio({}, "carta_entidades", "no_aplica")
        with self.assertRaises(ValueError):
            requisitos.validar_cambio({}, "inventado", "si")
        self.assertEqual(len(requisitos.avales_faltantes({})), 2)
        self.assertEqual(requisitos.avales_faltantes({"aval_departamento": "si", "aval_facultad": "si"}), [])
        r = requisitos.resumen({"aval_departamento": "si", "ficha_resumen": "si", "carta_entidades": "si"})
        self.assertEqual((r["departamento"], r["facultad"], r["cumplidos"]), ("si", "pendiente", 2))

    def test_requisitos_se_guardan_por_postulacion(self):
        a = cargas.agregar_estudiante(self.con, "Ana", "ana2@x.co", "General", "Grupo Tres")
        self.con.commit()
        pid = self.con.insertar("INSERT INTO postulaciones (usuario_id, convocatoria_id) VALUES (?, ?)", (a["id"], "c9"))
        self.con.commit()
        db.guardar_requisito(self.con, pid, "aval_departamento", "si", "firmado", a["id"])
        db.guardar_requisito(self.con, pid, "aval_departamento", "no", None, a["id"])
        db.guardar_requisito(self.con, pid, "cronograma", "si", None, a["id"])
        g = db.obtener_requisitos(self.con, pid)
        self.assertEqual(g["aval_departamento"]["estado"], "no")
        self.assertEqual(g["aval_departamento"]["actualizado_por"], "Ana")
        self.assertEqual(db.estados_requisitos(self.con, [pid])[pid], {"aval_departamento": "no", "cronograma": "si"})
        self.assertEqual(db.estados_requisitos(self.con, []), {})

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
        db.iniciar_db(self.con)
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

        db.cambiar_estado_convocatoria(self.con, c["id"], verificada=True)
        recolector.recolectar(self.con, [self._fuente()], descargar_fn=self._descargar,
                              extraer_fn=lambda t, u: {"es_convocatoria": True, "nombre": "Cambiado"},
                              pausa=0, revisar_robots=False)
        self.assertEqual(db.obtener_convocatoria(self.con, c["id"])["nombre"], "Convocatoria uno")

    def test_urls_directas_sin_listado_y_se_releen(self):
        pedidas = []
        def descargar(url):
            pedidas.append(url)
            return FalsaRespuesta(self.DETALLE, url=url)
        fuente = {"id": "erasmus", "nombre": "Erasmus+", "entidad": "Comisión Europea",
                  "urls_directas": ["https://europa.test/cbhe", "https://europa.test/jean-monnet", "no-es-url"]}
        def extraer(texto, url):
            cierre = "2099-02-10" if url.endswith("cbhe") else (date.today() - timedelta(days=3)).isoformat()
            return {"es_convocatoria": True, "nombre": "Acción " + url[-4:], "objetivo": "Objetivo",
                    "fecha_limite": cierre}
        resultados = []
        for _ in range(2):
            inf = recolector.recolectar(self.con, [fuente], descargar_fn=descargar, extraer_fn=extraer,
                                        pausa=0, revisar_robots=False)
            resultados.append(inf["fuentes"][0])
        res = resultados[0]
        self.assertEqual((res["enlaces"], res["guardadas"], res["vencidas"], res["omitidas"]), (2, 1, 1, 0))
        res2 = resultados[1]
        self.assertEqual((res2["enlaces"], res2["guardadas"], res2["vencidas"], res2["omitidas"]), (2, 0, 0, 2))
        self.assertEqual(pedidas.count("https://europa.test/cbhe"), 2)
        self.assertNotIn("no-es-url", pedidas)
        self.assertEqual(len(db.listar_convocatorias(self.con)), 2)

    def test_agregar_por_url_guarda_sin_verificar_y_rechaza_lo_que_no_sirve(self):
        pagina = "<html><body><p>" + ("Descripción de la convocatoria. " * 20) + "</p></body></html>"
        descargar = lambda u: FalsaRespuesta(pagina, url=u)
        abierta = lambda t, u: {"es_convocatoria": True, "nombre": "Beca X", "objetivo": "Objetivo", "fecha_limite": "2099-01-01"}
        conv = recolector.agregar_por_url(self.con, "https://www.sitio.test/beca-x", descargar_fn=descargar,
                                          extraer_fn=abierta, revisar_robots=False)
        guardada = db.obtener_convocatoria(self.con, conv["id"])
        self.assertFalse(guardada["verificada"])
        self.assertEqual((guardada["fuente"], guardada["entidad"]), ("enlace", "sitio.test"))
        no_conv = lambda t, u: {"es_convocatoria": False}
        for extraer, url in ((no_conv, "https://sitio.test/otra"), (abierta, "sin-https")):
            with self.assertRaises(ValueError):
                recolector.agregar_por_url(self.con, url, descargar_fn=descargar, extraer_fn=extraer, revisar_robots=False)
        self.assertEqual(len(db.listar_convocatorias(self.con)), 1)
        muy_vieja = lambda t, u: {"es_convocatoria": True, "nombre": "Antigua", "objetivo": "x", "fecha_limite": "2020-01-01"}
        with self.assertRaises(ValueError):
            recolector.agregar_por_url(self.con, "https://sitio.test/antigua", descargar_fn=descargar,
                                       extraer_fn=muy_vieja, revisar_robots=False)
        reciente = (date.today() - timedelta(days=3)).isoformat()
        vencida = lambda t, u: {"es_convocatoria": True, "nombre": "Vieja", "objetivo": "x", "fecha_limite": reciente}
        v = recolector.agregar_por_url(self.con, "https://sitio.test/vieja", descargar_fn=descargar,
                                       extraer_fn=vencida, revisar_robots=False)
        self.assertFalse(db.obtener_convocatoria(self.con, v["id"])["verificada"])
        self.assertEqual(len(db.listar_convocatorias(self.con)), 2)

    def test_plazo_de_cada_programa_en_una_pagina_con_listado(self):
        texto = ("Buscador de becas\nResearch Grants in Germany\nVer lista\nPlazo de solicitud:\n03.09.2026\n"
                 "Selection decision in February 2027.\n11.03.2027\nSelection decision in June 2027.\n"
                 "Doctoral Programmes in Germany\nVer lista\nPlazo de solicitud:\n03.09.2026\n"
                 "Funding decision will be taken in February 2027.\nBi-nationally Supervised Doctoral Degrees\n")
        hoy = date(2026, 11, 19)
        self.assertEqual(recolector.plazo_de_programa(texto, "Research Grants in Germany", hoy), "2027-03-11")
        self.assertEqual(recolector.plazo_de_programa(texto, "Doctoral Programmes in Germany", hoy), "2026-09-03")
        self.assertIsNone(recolector.plazo_de_programa(texto, "Programa que no está", hoy))
        self.assertEqual(recolector.plazo_de_programa(texto, "Doctoral Programme in Germany – DAAD Scholarship", hoy), "2026-09-03")
        self.assertEqual(recolector.plazo_de_programa(texto, "Research Grants in Germany (DAAD)", hoy), "2027-03-11")
        self.assertEqual(recolector.fecha_en_texto("Plazo 03.09.2026"), date(2026, 9, 3))

    def test_completar_fechas_rellena_las_que_quedaron_sin_fecha_sin_usar_ia(self):
        pagina = ("<html><body><p>" + ("Descripción. " * 30) + "</p><h3>Doctoral Programmes in Germany</h3>"
                  "<p>Plazo de solicitud:</p><p>03.09.2031</p></body></html>")
        descargar = lambda u: FalsaRespuesta(pagina, url=u)
        sin_fecha = lambda t, u: {"es_convocatoria": True, "nombre": "Doctoral Programme in Germany – DAAD Scholarship",
                                  "objetivo": "x"}
        c = recolector.agregar_por_url(self.con, "https://daad.test/x", descargar_fn=descargar,
                                       extraer_fn=sin_fecha, revisar_robots=False)
        db.poner_fecha_convocatoria(self.con, c["id"], None)
        self.assertIsNone(db.obtener_convocatoria(self.con, c["id"]).get("fecha_limite"))
        self.assertEqual(recolector.completar_fechas(self.con, descargar, pausa=0), 1)
        g = db.obtener_convocatoria(self.con, c["id"])
        self.assertEqual(g["fecha_limite"], "2031-09-03")
        self.assertFalse(g.get("fecha_manual"))
        self.assertEqual(recolector.completar_fechas(self.con, descargar, pausa=0), 0)

    def test_purgar_vencidas_borra_las_de_mas_de_una_semana_y_respeta_las_que_tienen_postulaciones(self):
        hoy = date.today()
        def conv(i, dias):
            return {"id": f"c{i}", "nombre": f"C{i}", "fecha_limite": (hoy - timedelta(days=dias)).isoformat()}
        for i, dias in ((1, 3), (2, 8), (3, 30), (4, -10)):
            db.guardar_convocatoria(self.con, conv(i, dias), fuente="manual", verificada=False)
        db.guardar_convocatoria(self.con, {"id": "c5", "nombre": "Sin fecha"}, fuente="manual", verificada=False)
        self.con.commit()
        self.assertEqual(db.purgar_vencidas(self.con), 2)
        ids = sorted(c["id"] for c in db.listar_convocatorias(self.con, solo_activas=False))
        self.assertEqual(ids, ["c1", "c4", "c5"])

    def test_completar_fechas_usa_el_listado_cuando_la_pagina_de_la_beca_no_trae_el_plazo(self):
        listado = ("<html><body><h2>Research Grants in Germany</h2><p>Plazo de solicitud:</p><p>03.09.2026</p>"
                   "<p>11.03.2031</p><h2>Bi-nationally Supervised Doctoral Degrees / Cotutelle</h2>"
                   "<p>Plazo de solicitud:</p><p>11.03.2031</p></body></html>")
        detalle_pagina = "<html><body><h1>Pagina</h1><p>" + ("Texto sin plazo. " * 30) + "</p></body></html>"
        descargar = lambda u: FalsaRespuesta(listado if "buscador" in u and "detail" not in u else detalle_pagina, url=u)
        sin_fecha = lambda t, u: {"es_convocatoria": True, "nombre": "Research Grants in Germany (DAAD)", "objetivo": "x"}
        c = recolector.agregar_por_url(self.con, "https://daad.test/detalle", descargar_fn=descargar,
                                       extraer_fn=sin_fecha, revisar_robots=False)
        db.guardar_convocatoria(self.con, {**db.obtener_convocatoria(self.con, c["id"]), "fecha_limite": None},
                                fuente="daad", verificada=False)
        db.poner_fecha_convocatoria(self.con, c["id"], None)
        with mock.patch.object(recolector, "cargar_fuentes", return_value=[
                {"id": "daad", "url_listado": "https://daad.test/buscador"}]):
            self.assertEqual(recolector.completar_fechas(self.con, descargar, pausa=0), 1)
        self.assertEqual(db.obtener_convocatoria(self.con, c["id"])["fecha_limite"], "2031-03-11")

    def test_depurar_sin_revisar_solo_las_que_no_tienen_fecha(self):
        hoy = date(2027, 3, 1)
        def guardar(i, **extra):
            db.guardar_convocatoria(self.con, {"id": f"d{i}", "nombre": f"D{i}", **extra}, fuente="manual", verificada=False)
        def poner_creada(cid, dias):
            f = self.con.execute("SELECT datos FROM convocatorias WHERE id = ?", (cid,)).fetchone()
            d = json.loads(f["datos"]); d["creada"] = (hoy - timedelta(days=dias)).isoformat()
            self.con.execute("UPDATE convocatorias SET datos = ? WHERE id = ?", (json.dumps(d), cid))
        guardar(1)
        guardar(2)
        guardar(3, fecha_limite="2027-12-31")
        db.guardar_convocatoria(self.con, {"id": "d4", "nombre": "D4"}, fuente="manual", verificada=True)
        poner_creada("d1", 70); poner_creada("d2", 10); poner_creada("d3", 70); poner_creada("d4", 70)
        self.con.commit()
        self.assertEqual(db.depurar_sin_revisar(self.con, hoy), {"movidas": 1, "borradas": 0})
        self.assertTrue(db.obtener_convocatoria(self.con, "d1").get("depuracion_desde"))
        for otra in ("d2", "d3", "d4"):
            self.assertFalse(db.obtener_convocatoria(self.con, otra).get("depuracion_desde"))
        db.guardar_convocatoria(self.con, {"id": "d1", "nombre": "D1 releída"}, fuente="manual", verificada=False)
        self.assertTrue(db.obtener_convocatoria(self.con, "d1").get("depuracion_desde"))
        self.assertEqual(db.depurar_sin_revisar(self.con, hoy + timedelta(days=14))["borradas"], 0)
        self.assertEqual(db.depurar_sin_revisar(self.con, hoy + timedelta(days=15))["borradas"], 1)
        self.assertIsNone(db.obtener_convocatoria(self.con, "d1"))
        guardar(5); poner_creada("d5", 70); self.con.commit()
        db.depurar_sin_revisar(self.con, hoy)
        self.assertTrue(db.recuperar_convocatoria(self.con, "d5"))
        self.assertFalse(db.obtener_convocatoria(self.con, "d5").get("depuracion_desde"))
        self.assertFalse(db.recuperar_convocatoria(self.con, "no-existe"))

    def test_paginas_fijas_sin_cambios_no_gastan_ia_otra_vez(self):
        pagina = "<html><body><h1>Programa X</h1><p>" + ("Descripción del programa. " * 20) + "Cierre: 01/12/2099</p></body></html>"
        descargar = lambda u: FalsaRespuesta(pagina, url=u)
        llamadas = []
        def extraer(t, u):
            llamadas.append(u)
            return {"es_convocatoria": True, "nombre": "Programa X", "objetivo": "x"}
        fuente = {"id": "fija", "nombre": "Fija", "entidad": "E", "urls_directas": ["https://sitio.test/x"],
                  "leer_tdr": False, "activa": True}
        r1 = recolector.recolectar(self.con, [fuente], descargar_fn=descargar, extraer_fn=extraer,
                                   extraer_tdr_fn=None, revisar_robots=False, pausa=0)
        r2 = recolector.recolectar(self.con, [fuente], descargar_fn=descargar, extraer_fn=extraer,
                                   extraer_tdr_fn=None, revisar_robots=False, pausa=0)
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(r1["fuentes"][0]["guardadas"], 1)
        self.assertEqual((r2["fuentes"][0]["guardadas"], r2["fuentes"][0]["omitidas"]), (0, 1))
        pagina = pagina.replace("2099", "2098")
        recolector.recolectar(self.con, [fuente], descargar_fn=descargar, extraer_fn=extraer,
                              extraer_tdr_fn=None, revisar_robots=False, pausa=0)
        self.assertEqual(len(llamadas), 2)

    def test_cerrada_tambien_en_ingles(self):
        for texto in ("Applications for 2027-2028 Chevening Scholarships are now closed.",
                      "Applications are closed", "Inscripciones cerradas", "Convocatoria cerrada"):
            self.assertTrue(recolector._RE_CERRADA.search(texto), texto)
        self.assertFalse(recolector._RE_CERRADA.search("Applications are open until 6 October 2026"))

    def test_fecha_manual_se_guarda_y_la_relectura_no_la_pisa(self):
        pagina = "<html><body><p>" + ("Descripción de la convocatoria. " * 20) + "</p></body></html>"
        descargar = lambda u: FalsaRespuesta(pagina, url=u)
        sin_fecha = lambda t, u: {"es_convocatoria": True, "nombre": "Sin fecha", "objetivo": "x"}
        c = recolector.agregar_por_url(self.con, "https://sitio.test/sf", descargar_fn=descargar,
                                       extraer_fn=sin_fecha, revisar_robots=False)
        self.assertIsNone(db.obtener_convocatoria(self.con, c["id"]).get("fecha_limite"))
        self.assertTrue(db.poner_fecha_convocatoria(self.con, c["id"], "2031-05-01"))
        recolector.agregar_por_url(self.con, "https://sitio.test/sf", descargar_fn=descargar,
                                   extraer_fn=sin_fecha, revisar_robots=False)
        g = db.obtener_convocatoria(self.con, c["id"])
        self.assertEqual(g["fecha_limite"], "2031-05-01")
        self.assertFalse(db.poner_fecha_convocatoria(self.con, "no-existe", "2031-05-01"))

    def test_eliminar_convocatoria_no_vuelve_y_protege_las_que_tienen_postulaciones(self):
        from matching import esta_vigente
        self.assertTrue(esta_vigente({"fecha_limite": None}))
        self.assertTrue(esta_vigente({"fecha_limite": "2099-01-01"}))
        self.assertFalse(esta_vigente({"fecha_limite": "2020-01-01"}))
        url = "https://europa.test/cbhe"
        conv = convocatorias.normalizar_convocatoria({"id": "web-x1", "nombre": "Acción", "objetivo": "o", "url": url})
        db.guardar_convocatoria(self.con, conv, fuente="erasmus", verificada=False)
        self.assertIsNone(db.eliminar_convocatoria(self.con, "web-x1"))
        self.assertIsNone(db.obtener_convocatoria(self.con, "web-x1"))
        self.assertTrue(db.enlace_eliminado(self.con, url))
        self.assertIsNotNone(db.eliminar_convocatoria(self.con, "web-x1"))
        pagina = "<html><body><p>" + ("Descripción. " * 30) + "</p></body></html>"
        extraer = lambda t, u: {"es_convocatoria": True, "nombre": "Acción", "objetivo": "o", "fecha_limite": "2099-01-01"}
        inf = recolector.recolectar(self.con, [{"id": "erasmus", "entidad": "UE", "urls_directas": [url]}],
                                    descargar_fn=lambda u: FalsaRespuesta(pagina, url=u), extraer_fn=extraer,
                                    pausa=0, revisar_robots=False)
        self.assertEqual((inf["fuentes"][0]["guardadas"], inf["fuentes"][0]["omitidas"]), (0, 1))
        self.assertEqual(len(db.listar_convocatorias(self.con, solo_activas=False)), 0)

    def test_el_cierre_se_toma_de_los_terminos_de_referencia_si_la_pagina_no_lo_dice(self):
        cronograma = ("Apertura de la Convocatoria 29 de mayo de 2026\nCierre de la Convocatoria\n21 de agosto de 2026\n"
                      "Verificación de requisitos 24 al 26 de agosto de 2026")
        self.assertEqual(recolector.datos_clave(cronograma).get("fecha_limite"), "2026-08-21")
        datos = {"nombre": "Editorial"}
        recolector._mezclar_tdr(datos, {"fecha_limite": "2026-08-21"}, {"url": "https://x.test/tdr.pdf"})
        self.assertEqual(datos["fecha_limite"], "2026-08-21")
        con_fecha = {"nombre": "X", "fecha_limite": "2026-12-11"}
        recolector._mezclar_tdr(con_fecha, {"fecha_limite": "2026-08-21"}, {"url": "https://x.test/tdr.pdf"})
        self.assertEqual(con_fecha["fecha_limite"], "2026-12-11")
        malo = {"nombre": "Y"}
        recolector._mezclar_tdr(malo, {"fecha_limite": "pronto"}, {"url": "https://x.test/tdr.pdf"})
        self.assertNotIn("fecha_limite", malo)

    def test_pagina_que_dice_no_hay_convocatorias_abiertas(self):
        pagina = "<html><body><p>" + ("Descripción del programa. " * 20) + \
                 "</p><p>No hay convocatorias abiertas en este momento</p></body></html>"
        fuente = {"id": "erasmus", "entidad": "UE", "urls_directas": ["https://europa.test/jm"]}
        extraer = lambda t, u: {"es_convocatoria": True, "nombre": "Jean Monnet", "objetivo": "x"}
        c = {"id": "web-" + hashlib.sha1(b"https://europa.test/jm").hexdigest()[:10],
             "nombre": "Jean Monnet", "objetivo": "x", "entidad": "UE"}
        db.guardar_convocatoria(self.con, convocatorias.normalizar_convocatoria(c), fuente="erasmus", verificada=False)
        self.assertEqual(len(db.listar_convocatorias(self.con)), 1)
        inf = recolector.recolectar(self.con, [fuente], descargar_fn=lambda u: FalsaRespuesta(pagina, url=u),
                                    extraer_fn=extraer, pausa=0, revisar_robots=False)
        self.assertEqual((inf["fuentes"][0]["descartadas"], inf["fuentes"][0]["guardadas"]), (1, 0))
        self.assertEqual(len(db.listar_convocatorias(self.con)), 0)

    TABLA = """<html><body><nav><ul><li><a href="/node/1">Menú: inicio</a></li></ul></nav>
      <table>
        <tr><td>55</td><td><a href="/node/9581">Macroproyectos</a></td><td>Viernes, Octubre 23, 2026</td></tr>
        <tr><td>12</td><td><a href="/node/7000">Antigua</a></td><td>Lunes, Marzo 3, 2024</td></tr>
        <tr><td>54</td><td><a href="/node/9582">Vencida</a></td><td>Viernes, Octubre 23, 2026</td></tr>
      </table></body></html>"""

    PAGINA_DETALLADA = """<html><body><nav><a href="/menu.pdf">Menú</a></nav>
      <h1>Fortalecimiento de capacidades de CTeI en Córdoba</h1><p>Convocatoria en Evaluación - Cerrada</p>
      <p>""" + ("Dirigida a alianzas con grupos de investigación. " * 8) + """</p>
      <table>
        <tr><td>Apertura</td><td>viernes 25 septiembre 2026</td></tr>
        <tr><td>Cierre</td><td>viernes 23 octubre 2026 04:00 pm</td></tr>
        <tr><td>Publicación de resultados definitivos</td><td>miércoles 09 diciembre 2026</td></tr>
        <tr><td>Contacto</td><td>formulario web</td></tr>
      </table>
      <ul><li><a href="/upload/tdr_convocatoria_53.pdf">TdR Convocatoria 53 del SGR</a></li>
          <li><a href="/upload/anexo_3_presupuesto.xlsx">Anexo 3. Presupuesto</a></li>
          <li><a href="/upload/tdr_convocatoria_53.pdf">TdR repetido</a></li>
          <li><a href="/otra-pagina">No es documento</a></li></ul>
      <footer><a href="/pie.pdf">Pie</a></footer></body></html>"""

    def test_detalle_cronograma_documentos_y_estado(self):
        from scrapers import detalle
        base = "https://sitio.test/node/53"
        docs = detalle.extraer_documentos(self.PAGINA_DETALLADA, base)
        self.assertEqual([d["url"] for d in docs],
                         ["https://sitio.test/upload/tdr_convocatoria_53.pdf", "https://sitio.test/upload/anexo_3_presupuesto.xlsx"])
        self.assertEqual(docs[0]["titulo"], "TdR Convocatoria 53 del SGR")
        cron = detalle.extraer_cronograma(self.PAGINA_DETALLADA, recolector.fecha_en_texto)
        self.assertEqual([(f["actividad"], f["fecha_iso"]) for f in cron],
                         [("Apertura", "2026-09-25"), ("Cierre", "2026-10-23"),
                          ("Publicación de resultados definitivos", "2026-12-09")])
        self.assertEqual(detalle.fecha_de(cron, "cierre"), "2026-10-23")
        self.assertIn("Cerrada", detalle.estado_de_pagina("Convocatoria en Evaluación - Cerrada"))

    def test_la_convocatoria_guarda_el_detalle(self):
        def extraer(texto, url):
            return {"es_convocatoria": True, "nombre": "Convocatoria 53", "objetivo": "Objetivo",
                    "requisitos_lista": ["Alianza con al menos un grupo de investigación", "Un proyecto por entidad"],
                    "dirigida_a": "Alianzas de CTeI", "ejes_tematicos": ["Salud", "Agro"]}
        conv = recolector.procesar_enlace("https://sitio.test/node/53", self._fuente(),
                                          lambda u: FalsaRespuesta(self.PAGINA_DETALLADA, url=u), extraer)
        self.assertEqual(conv["fecha_apertura"], "2026-09-25")
        self.assertEqual(conv["fecha_limite"], "2026-10-23")
        self.assertEqual(len(conv["cronograma"]), 3)
        self.assertEqual(len(conv["documentos"]), 2)
        self.assertEqual(conv["requisitos_lista"][1], "Un proyecto por entidad")
        self.assertEqual(conv["ejes_tematicos"], ["Salud", "Agro"])
        r = matching.rankear({"alcance": "nacional"}, [conv])[0]
        self.assertEqual((len(r["cronograma"]), len(r["documentos"]), r["dirigida_a"]), (3, 2, "Alianzas de CTeI"))

    def test_se_leen_los_terminos_de_referencia(self):
        from reportlab.pdfgen import canvas
        from scrapers import detalle
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        for i, t in enumerate(["Portada " * 60, "Indice " * 60, "REQUISITOS: podrán participar alianzas. Requisito de contrapartida. " * 6, "Anexos " * 60]):
            c.drawString(40, 800, t[:90]); c.drawString(40, 780, t[90:180]); c.showPage()
        c.save()
        pdf = buf.getvalue()

        docs = [{"titulo": "Anexo 1. Carta", "url": "https://s.test/a1.pdf"},
                {"titulo": "RESPUESTA A OBSERVACIONES TERMINOS DE REFERENCIA", "url": "https://s.test/resp.pdf"},
                {"titulo": "TdR Convocatoria 53 del SGR", "url": "https://s.test/tdr.pdf"}]
        self.assertEqual(detalle.elegir_tdr(docs)["url"], "https://s.test/tdr.pdf")
        self.assertIsNone(detalle.elegir_tdr([{"titulo": "TdR", "url": "https://s.test/tdr.docx"}]))

        sel = detalle.seleccionar_paginas(["a " * 50, "b " * 50, "nada", "requisitos y requisitos", "x"], limite=300)
        self.assertIn("[Página 4]", sel)

        class Resp:
            def __init__(self, url):
                self.url, self.headers = url, {"content-type": "application/pdf"}
                self.content, self.text = pdf, ""
        abierta = self.PAGINA_DETALLADA.replace("octubre 2026", "octubre 2099")
        def descargar(url):
            return FalsaRespuesta(abierta, url=url) if "node" in url else Resp(url)
        vistos = {}
        def extraer_pagina(texto, url):
            return {"es_convocatoria": True, "nombre": "Conv 53", "requisitos_lista": ["de la página"], "fecha_limite": "2099-01-01"}
        def extraer_tdr(texto, url):
            vistos["texto"] = texto
            return {"requisitos_lista": ["Alianza con un grupo", "Contrapartida del 10%"], "duracion_meses": 24,
                    "criterios_evaluacion": ["Calidad técnica (30 puntos)"], "monto_max": 900000000}
        conv = recolector.procesar_enlace("https://s.test/node/53", self._fuente(), descargar, extraer_pagina, extraer_tdr)
        self.assertTrue(conv["leido_tdr"])
        self.assertEqual(conv["requisitos_lista"], ["Alianza con un grupo", "Contrapartida del 10%"])
        self.assertEqual((conv["duracion_meses"], conv["monto_max"]), (24, 900000000))
        self.assertIn("podrán participar", vistos["texto"])
        sin = recolector.procesar_enlace("https://s.test/node/53", self._fuente(), descargar, extraer_pagina)
        self.assertFalse(sin["leido_tdr"])
        pasada = self.PAGINA_DETALLADA
        vencida = recolector.procesar_enlace("https://s.test/node/53", self._fuente(),
                                             lambda u: FalsaRespuesta(pasada, url=u) if "node" in u else Resp(u),
                                             extraer_pagina, extraer_tdr)
        self.assertFalse(vencida["leido_tdr"])
        roto = recolector.procesar_enlace("https://s.test/node/53", self._fuente(),
                                          lambda u: FalsaRespuesta(abierta, url=u), extraer_pagina, extraer_tdr)
        self.assertFalse(roto["leido_tdr"])

    def test_detalle_se_sanea(self):
        c = convocatorias.normalizar_convocatoria({
            "nombre": "X", "documentos": [{"titulo": "a", "url": "javascript:alert(1)"}, {"titulo": "ok", "url": "https://a.co/x.pdf"}],
            "cronograma": [{"actividad": "Cierre", "fecha": "hoy", "fecha_iso": "no-es-fecha"}, "basura"],
            "requisitos_lista": "no es lista", "ejes_tematicos": ["a"] * 30})
        self.assertEqual([d["url"] for d in c["documentos"]], ["https://a.co/x.pdf"])
        self.assertIsNone(c["cronograma"][0]["fecha_iso"])
        self.assertEqual((c["requisitos_lista"], len(c["ejes_tematicos"])), ([], 10))

    def test_fecha_en_texto(self):
        self.assertEqual(recolector.fecha_en_texto("Viernes, Octubre 23, 2026"), date(2026, 10, 23))
        self.assertEqual(recolector.fecha_en_texto("abre el 5 de marzo de 2027"), date(2027, 3, 5))
        self.assertEqual(recolector.fecha_en_texto("2026-10-23"), date(2026, 10, 23))
        self.assertIsNone(recolector.fecha_en_texto("sin fecha"))

    def test_listado_en_tabla_filtra_menu_viejas_vencidas_y_recuerda(self):
        fuente = self._fuente(url_listado="https://sitio.test/lista", patron_enlaces=r"/node/\d+",
                              patron_solo_en_enlace=True, solo_filas_de_tabla=True,
                              max_antiguedad_dias=400, paginas=2)
        pedidas = []

        def descargar(url):
            pedidas.append(url)
            return FalsaRespuesta(self.TABLA if "/lista" in url else self.DETALLE, url=url)

        def extraer(texto, url):
            llamadas.append(url)
            return {"es_convocatoria": True, "nombre": "Conv " + url[-4:],
                    "fecha_limite": "2020-01-01" if url.endswith("9582") else "2999-01-01"}
        llamadas = []
        inf = recolector.recolectar(self.con, [fuente], descargar_fn=descargar, extraer_fn=extraer,
                                    pausa=0, revisar_robots=False)
        res = inf["fuentes"][0]
        self.assertIn("https://sitio.test/lista?page=1", pedidas)
        self.assertEqual(sorted(llamadas), ["https://sitio.test/node/9581", "https://sitio.test/node/9582"])
        self.assertEqual((res["guardadas"], res["vencidas"], res["omitidas"]), (1, 1, 1))
        llamadas.clear()
        inf = recolector.recolectar(self.con, [fuente], descargar_fn=descargar, extraer_fn=extraer,
                                    pausa=0, revisar_robots=False)
        self.assertEqual(llamadas, [])
        self.assertEqual(inf["fuentes"][0]["guardadas"], 0)

    def test_datos_clave_del_cronograma_y_recursos(self):
        largo = "Objetivo y requisitos de la convocatoria. " * 400
        texto = (f"{largo}\nRecursos disponibles: $28.094.792.444,00\nCronograma\nApertura\nviernes 25 septiembre 2026\n"
                 "Cierre\nviernes 23 octubre 2026 04:00 pm\nResultados\nmartes 03 noviembre 2026")
        clave = recolector.datos_clave(texto)
        self.assertEqual(clave, {"fecha_limite": "2026-10-23", "recursos_totales": 28094792444})
        self.assertEqual(recolector.datos_clave("Fecha de cierre: 15 de marzo de 2027")["fecha_limite"], "2027-03-15")
        self.assertEqual(recolector.datos_clave("sin datos útiles aquí"), {})

        pagina = "<html><body><p>" + largo + "</p><p>Recursos disponibles: $1.500.000.000,00</p><table><tr><td>Cierre</td><td>viernes 18 diciembre 2026 04:00 pm</td></tr></table></body></html>"
        conv = recolector.procesar_enlace("https://sitio.test/node/1", self._fuente(),
                                          lambda u: FalsaRespuesta(pagina, url=u),
                                          lambda t, u: {"es_convocatoria": True, "nombre": "Una", "fecha_limite": None})
        self.assertEqual(conv["fecha_limite"], "2026-12-18")
        self.assertEqual(conv["recursos_totales"], 1500000000)
        self.assertIn("1.500.000.000", conv["beneficios"])

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

    def test_pagina_que_se_dice_cerrada_se_descarta(self):
        texto = "Fulbright Visiting Scholar. Convocatoria cerrada. " + "Texto del programa. " * 30

        class R:
            headers = {"content-type": "text/html"}
            url = "https://x.org/p"
            text = "<html><body><p>" + texto + "</p></body></html>"
            content = text.encode()

        self.assertIsNone(recolector.procesar_enlace("https://x.org/p", {}, lambda u: R(), lambda *a, **k: {}))


if __name__ == "__main__":
    unittest.main()