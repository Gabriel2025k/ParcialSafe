import csv
import json
import tempfile
import unittest
from pathlib import Path

import receptor


def msg(**cambios):
    base = {"seq": 1, "frontal_cm": 85.3, "piso_cm": 30.1, "nivel_frontal": "PRECAUCION",
            "estado_piso": "NORMAL", "vibracion_pct": 17, "bocina": "SILENCIO", "activo": 1}
    base.update(cambios)
    return json.dumps(base).encode()


class TestValidacion(unittest.TestCase):
    def test_valido(self):
        d, err = receptor.validar_telemetria(msg())
        self.assertIsNone(err)
        self.assertEqual(d["seq"], 1)

    def test_sin_eco_null_es_valido(self):
        d, err = receptor.validar_telemetria(msg(piso_cm=None))
        self.assertIsNone(err)

    def test_json_ilegible(self):
        self.assertEqual(receptor.validar_telemetria(b"hola")[1], "JSON ilegible")
        self.assertEqual(receptor.validar_telemetria(b"\xff\xfe")[1], "JSON ilegible")

    def test_no_es_objeto(self):
        self.assertIn("objeto", receptor.validar_telemetria(b"[1,2]")[1])

    def test_campo_faltante(self):
        d = json.loads(msg())
        del d["bocina"]
        self.assertIn("bocina", receptor.validar_telemetria(json.dumps(d).encode())[1])

    def test_rangos(self):
        for malo in (dict(frontal_cm=1.0), dict(frontal_cm=401), dict(piso_cm=-5),
                     dict(frontal_cm="25"), dict(frontal_cm=True),
                     dict(vibracion_pct=101), dict(vibracion_pct=-1), dict(vibracion_pct=50.5),
                     dict(activo=2), dict(seq=-1), dict(nivel_frontal="ALTO"),
                     dict(estado_piso="HUECO"), dict(bocina="FUERTE")):
            with self.subTest(malo=malo):
                self.assertIsNone(receptor.validar_telemetria(msg(**malo))[0])

    def test_limites_exactos(self):
        self.assertIsNone(receptor.validar_telemetria(msg(frontal_cm=2.0, piso_cm=400))[1])


class TestCSV(unittest.TestCase):
    def test_cabecera_una_vez_e_invalidos_no_se_guardan(self):
        with tempfile.TemporaryDirectory() as t:
            r = receptor.Receptor(Path(t) / "sub" / "t.csv")
            self.assertTrue(r.procesar_telemetria(msg(seq=1)))
            self.assertFalse(r.procesar_telemetria(b"basura"))
            self.assertTrue(r.procesar_telemetria(msg(seq=2, frontal_cm=None)))
            filas = list(csv.reader(open(r.ruta, encoding="utf-8")))
            self.assertEqual(filas[0], receptor.CABECERA)
            self.assertEqual(len(filas), 3)
            self.assertEqual(filas[2][2], "")           # null -> vacio
            self.assertEqual((r.validos, r.descartados), (2, 1))

    def test_error_de_archivo_no_detiene(self):
        with tempfile.TemporaryDirectory() as t:
            r = receptor.Receptor(Path(t))              # una carpeta: no se puede abrir como archivo
            self.assertTrue(r.procesar_telemetria(msg()))   # no lanza excepcion
            self.assertEqual(r.validos, 0)


if __name__ == "__main__":
    unittest.main()
