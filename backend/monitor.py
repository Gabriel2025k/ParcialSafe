"""Monitor de estado de comunicacion de SafeStep.

Estados:
  ONLINE     llega telemetria con regularidad (o el dispositivo acaba de anunciar online)
  SIN_DATOS  conectado al broker pero sin publicar por mas de --silencio segundos
  OFFLINE    el broker publico el testamento (Last Will) en .../estado

Escribe en salida/eventos_comunicacion.csv SOLO cuando el estado cambia.

Uso:  python monitor.py [--silencio 10] [--intervalo 2]
"""
import argparse
import csv
import threading
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

import config

SALIDA = Path(__file__).parent / "salida"
CSV_EVENTOS = SALIDA / "eventos_comunicacion.csv"

ONLINE, SIN_DATOS, OFFLINE = "ONLINE", "SIN_DATOS", "OFFLINE"
GRAVEDAD = {ONLINE: "INFO", SIN_DATOS: "ADVERTENCIA", OFFLINE: "CRITICA"}
CABECERA = ["fecha", "tipo", "gravedad", "detalle"]


class Monitor:
    def __init__(self, silencio_s, intervalo_s, ruta_csv=CSV_EVENTOS, reloj=time.monotonic):
        if silencio_s < 3 * intervalo_s:
            raise ValueError(
                f"El silencio maximo ({silencio_s} s) debe ser al menos 3 veces el "
                f"intervalo de publicacion ({intervalo_s} s = minimo {3 * intervalo_s} s)")
        self.silencio_s = silencio_s
        self.ruta = Path(ruta_csv)
        self.reloj = reloj
        self.lock = threading.Lock()
        self.estado = None            # aun no se sabe nada
        self.inicio = reloj()
        self.ultimo_dato = None       # instante del ultimo mensaje vivo (telemetria / online)

    # ---- entradas (las llama paho desde su hilo) ----
    def on_estado(self, texto, retenido=False):
        texto = texto.strip().lower()
        origen = "retenido" if retenido else "en vivo"
        with self.lock:
            if texto == "offline":
                self._cambiar(OFFLINE, f"el broker publico el testamento ({origen})")
            elif texto == "online":
                self.ultimo_dato = self.reloj()   # periodo de gracia para la 1a telemetria
                self._cambiar(ONLINE, f"el dispositivo anuncio online ({origen})")

    def on_telemetria(self):
        with self.lock:
            self.ultimo_dato = self.reloj()
            self._cambiar(ONLINE, "llega telemetria")

    # ---- revision periodica (hilo principal) ----
    def revisar(self):
        with self.lock:
            if self.estado in (OFFLINE, SIN_DATOS):
                return
            ahora = self.reloj()
            if self.estado == ONLINE:
                silencio = ahora - self.ultimo_dato
                if silencio > self.silencio_s:
                    self._cambiar(SIN_DATOS, f"sin telemetria hace {silencio:.1f} s "
                                             f"(maximo {self.silencio_s:g} s) y sin testamento")
            elif self.estado is None and ahora - self.inicio > self.silencio_s:
                self._cambiar(SIN_DATOS, "ningun mensaje del dispositivo desde que inicio el monitor")

    # ---- interno: solo registra si el estado cambio ----
    def _cambiar(self, nuevo, detalle):
        if nuevo == self.estado:
            return False
        anterior = self.estado
        self.estado = nuevo
        detalle = f"{anterior or 'INICIO'} -> {nuevo}: {detalle}"
        print(f"[EVENTO] {nuevo} ({GRAVEDAD[nuevo]}) {detalle}")
        self._escribir(nuevo, detalle)
        return True

    def _escribir(self, tipo, detalle):
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            nuevo = not self.ruta.exists() or self.ruta.stat().st_size == 0
            with open(self.ruta, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if nuevo:
                    w.writerow(CABECERA)
                w.writerow([datetime.now().isoformat(timespec="seconds"),
                            tipo, GRAVEDAD[tipo], detalle])
        except OSError as e:
            print(f"[AVISO] No se pudo escribir el CSV de eventos ({e}). Se continua.")


def main():
    ap = argparse.ArgumentParser(description="Monitor de comunicacion SafeStep")
    ap.add_argument("--silencio", type=float, default=config.SILENCIO_MAX_S,
                    help="segundos maximos sin telemetria (>= 3 x intervalo)")
    ap.add_argument("--intervalo", type=float, default=config.INTERVALO_PUBLICACION_S,
                    help="intervalo de publicacion del dispositivo en segundos")
    args = ap.parse_args()
    try:
        monitor = Monitor(args.silencio, args.intervalo)
    except ValueError as e:
        raise SystemExit(f"[ERROR] {e}")

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[ERROR] Conexion rechazada: {reason_code}")
            return
        print(f"[MONITOR] Conectado a {config.BROKER}. Silencio maximo: {args.silencio:g} s")
        client.subscribe(config.T_ESTADO)
        client.subscribe(config.T_TELEMETRIA)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"[MONITOR] Sin conexion con el broker ({reason_code}). Reintentando...")

    def on_message(client, userdata, msg):
        try:
            if msg.topic == config.T_ESTADO:
                monitor.on_estado(msg.payload.decode("utf-8", "replace"), bool(msg.retain))
            elif msg.topic == config.T_TELEMETRIA:
                monitor.on_telemetria()
        except Exception as e:
            print(f"[ERROR] Mensaje no procesado: {e}")

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id=f"safestep-monitor-{config.GRUPO}")
    cliente.on_connect = on_connect
    cliente.on_disconnect = on_disconnect
    cliente.on_message = on_message
    cliente.reconnect_delay_set(min_delay=2, max_delay=60)
    cliente.connect_async(config.BROKER, config.PUERTO, keepalive=30)
    cliente.loop_start()
    try:
        while True:
            time.sleep(0.5)
            monitor.revisar()
    except KeyboardInterrupt:
        print("\n[MONITOR] Fin.")
    finally:
        cliente.loop_stop()
        cliente.disconnect()


if __name__ == "__main__":
    main()
