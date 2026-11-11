import csv
import tempfile
import unittest
from pathlib import Path

import monitor
from monitor import ONLINE, SIN_DATOS, OFFLINE


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestMonitor(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.ruta = Path(self._dir.name) / "eventos.csv"
        self.reloj = Reloj()
        self.m = monitor.Monitor(10, 2, self.ruta, self.reloj)

    def eventos(self):
        if not self.ruta.exists():
            return []
        return list(csv.reader(open(self.ruta, encoding="utf-8")))[1:]

    def test_silencio_minimo_3_veces_el_intervalo(self):
        with self.assertRaises(ValueError):
            monitor.Monitor(5, 2, self.ruta)
        monitor.Monitor(6, 2, self.ruta)   # justo 3x: valido

    def test_telemetria_regular_un_solo_evento(self):
        for i in range(20):
            self.reloj.t = i * 2
            self.m.on_telemetria()
            self.m.revisar()
        self.assertEqual(len(self.eventos()), 1)           # solo INICIO -> ONLINE
        self.assertEqual(self.eventos()[0][1:3], [ONLINE, "INFO"])

    def test_silencio_genera_sin_datos_una_vez_y_recupera(self):
        self.m.on_estado("online")
        self.m.on_telemetria()
        self.reloj.t = 10.0
        self.m.revisar()
        self.assertEqual(self.m.estado, ONLINE)            # justo en el limite: aun ONLINE
        self.reloj.t = 10.5
        self.m.revisar()
        self.assertEqual(self.m.estado, SIN_DATOS)
        for t in (11, 15, 30):
            self.reloj.t = t
            self.m.revisar()
        self.assertEqual([e[1] for e in self.eventos()], [ONLINE, SIN_DATOS])
        self.m.on_telemetria()
        self.assertEqual(self.m.estado, ONLINE)
        self.assertEqual([e[1] for e in self.eventos()], [ONLINE, SIN_DATOS, ONLINE])

    def test_testamento_offline_critico(self):
        self.m.on_estado("online")
        self.m.on_estado("offline")
        self.m.on_estado("offline")                        # repetido: no hay evento nuevo
        ev = self.eventos()
        self.assertEqual([e[1] for e in ev], [ONLINE, OFFLINE])
        self.assertEqual(ev[1][2], "CRITICA")

    def test_offline_no_pasa_a_sin_datos_y_online_recupera(self):
        self.m.on_estado("offline")
        self.reloj.t = 100
        self.m.revisar()
        self.assertEqual(self.m.estado, OFFLINE)
        self.m.on_estado("online")
        self.assertEqual(self.m.estado, ONLINE)

    def test_nunca_se_supo_nada(self):
        self.reloj.t = 11
        self.m.revisar()
        self.assertEqual(self.m.estado, SIN_DATOS)

    def test_archivo_ilegible_no_detiene(self):
        m = monitor.Monitor(10, 2, Path(self._dir.name), self.reloj)  # carpeta, no archivo
        m.on_estado("offline")
        self.assertEqual(m.estado, OFFLINE)


if __name__ == "__main__":
    unittest.main()
