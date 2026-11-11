"""
SafeStep - Baston inteligente (Etapa 3: modulo de comunicacion MQTT)
Version MicroPython para ESP32 DevKit C. Extiende el firmware de la Etapa 2.
Universidad Tecnologica de Panama - Desarrollo de Software 8

Pines:
  HC-SR04 frontal : TRIG=GPIO5   ECHO=GPIO18
  HC-SR04 piso    : TRIG=GPIO17  ECHO=GPIO16
  Motor vibrador  : GPIO25 (PWM)      Bocina: GPIO26 (PWM, tono)
  Switch de inicio: GPIO14 (pull-up, interrupcion)
  LED verde estado: GPIO4
  LED comunicacion: GPIO15 (fijo = conectado, parpadeo = sin conexion)
"""
from machine import Pin, PWM, time_pulse_us
import machine
import time
import json
import network
import ubinascii
from umqtt.simple import MQTTClient

# ------------------------------------------------------------ Pines
PIN_TRIG_FRONTAL = 5
PIN_ECHO_FRONTAL = 18
PIN_TRIG_PISO = 17
PIN_ECHO_PISO = 16
PIN_MOTOR = 25
PIN_BOCINA = 26
PIN_SWITCH = 14
PIN_LED_ESTADO = 4
PIN_LED_COM = 15

# ------------------------------------------------------------ Parametros
DIST_MIN_CM = 2.0
DIST_MAX_CM = 400.0
UMBRAL_PRECAUCION_CM = 100.0   # variable: cambia con la orden umbral_precaucion
UMBRAL_PELIGRO_CM = 40.0       # variable: cambia con la orden umbral_peligro
DIST_VIBRACION_MAX_CM = 10.0
PISO_BASE_CM = 30.0
PISO_TOLERANCIA_CM = 15.0
CONFIRMACIONES_BAJADA = 2
MAX_FALLAS = 3
PERIODO_MS = 100
TIMEOUT_ECO_US = 25000
ANTIREBOTE_MS = 200
LOG_CADA = 5

# ------------------------------------------------------------ Comunicacion MQTT
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASS = ""
WIFI_CANAL = 6
BROKER = "broker.hivemq.com"
BROKER_PUERTO = 1883
GRUPO = "safestep_dgp27"                      # igual que backend/config.py
PROYECTO = "safestep"
BASE = "utp/ds8/%s/%s" % (GRUPO, PROYECTO)
T_TELEMETRIA = BASE + "/telemetria"   # publica, no retenido
T_ESTADO = BASE + "/estado"           # publica, retenido (online / offline por Last Will)
T_ORDENES = BASE + "/ordenes"         # se suscribe
T_RESPUESTA = BASE + "/respuesta"     # publica, no retenido
CLIENT_ID = "safestep-%s-%s" % (GRUPO, ubinascii.hexlify(machine.unique_id()).decode())

PUBLICAR_CADA_MS = 2000
KEEPALIVE_S = 20
PING_CADA_MS = 8000
WIFI_TIMEOUT_MS = 30000            # sin canal fijo, el escaneo del Wi-Fi puede tardar
BROKER_TIMEOUT_S = 5
ESPERA_INICIAL_S = 2               # espera creciente: 2, 4, 8, 16, 32, 60, 60...
ESPERA_MAX_S = 60

# Rangos validos para las ordenes
PELIGRO_MIN, PELIGRO_MAX = 15, 80
PRECAUCION_MIN, PRECAUCION_MAX = 60, 300
SEPARACION_MIN = 10                # precaucion - peligro >= 10 cm
CAIDA_MIN_S, CAIDA_MAX_S = 5, 60

COM_ESPERA, COM_WIFI, COM_CONECTADO = 0, 1, 2

INF = float("inf")

# Niveles
LIBRE, PRECAUCION, PELIGRO = 0, 1, 2
PISO_NORMAL, PISO_DESNIVEL = 0, 1
P_SILENCIO, P_OBSTACULO, P_DESNIVEL = 0, 1, 2
LECT_OK, LECT_SIN_ECO, LECT_FUERA_RANGO = 0, 1, 2

# Patrones de bocina: (hz, ms_encendido, ms_apagado)
PATRONES = {
    P_OBSTACULO: (1000, 150, 150),
    P_DESNIVEL: (2200, 500, 100),
}

NOMBRES_NIVEL = ("LIBRE", "PRECAUCION", "PELIGRO")
NOMBRES_PISO = ("NORMAL", "DESNIVEL")
NOMBRES_PATRON = ("SILENCIO", "OBSTACULO", "DESNIVEL")


# ------------------------------------------------------------ Tipos
class FiltroMediana3:
    """Mediana de 3 muestras (INF = 'sin eco' es una muestra valida)."""

    def __init__(self):
        self.reiniciar()

    def reiniciar(self):
        self.v = []

    def agregar(self, x):
        self.v.append(x)
        if len(self.v) > 3:
            self.v.pop(0)
        n = len(self.v)
        if n == 1:
            return self.v[0]
        if n == 2:
            return (self.v[0] + self.v[1]) / 2
        return sorted(self.v)[1]


class Canal:
    def __init__(self):
        self.filtro = FiltroMediana3()
        self.reiniciar()

    def reiniciar(self):
        self.filtro.reiniciar()
        self.distancia = INF
        self.estado = 0
        self.cuenta_bajada = 0
        self.fallas = 0
        self.en_falla = False
        self.desconectado = False


# ------------------------------------------------------------ Hardware
trig_frontal = Pin(PIN_TRIG_FRONTAL, Pin.OUT, value=0)
echo_frontal = Pin(PIN_ECHO_FRONTAL, Pin.IN)
trig_piso = Pin(PIN_TRIG_PISO, Pin.OUT, value=0)
echo_piso = Pin(PIN_ECHO_PISO, Pin.IN)
led_estado = Pin(PIN_LED_ESTADO, Pin.OUT, value=0)
led_com = Pin(PIN_LED_COM, Pin.OUT, value=0)
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
switch = Pin(PIN_SWITCH, Pin.IN, Pin.PULL_UP)

pwm_motor = PWM(Pin(PIN_MOTOR), freq=5000, duty_u16=0)
pwm_bocina = PWM(Pin(PIN_BOCINA), freq=2000, duty_u16=0)

# ------------------------------------------------------------ Estado global
canal_frontal = Canal()
canal_piso = Canal()
activo = False
tick = 0
ultimo_ciclo = 0
vibracion_pct = 0
patron_actual = P_SILENCIO
bocina_sonando = False
inicio_patron = 0

pedir_alternar = False
ultima_interrupcion = 0

# Estado de comunicacion
mqtt = None
com_estado = COM_ESPERA
proximo_intento = 0
espera_s = ESPERA_INICIAL_S
t_wifi = 0
ultimo_ping = 0
ultima_pub = 0
seq = 0
perdidas = 0
caida_pendiente = None             # (segundos, medio) pedida por la orden simular_caida


# ------------------------------------------------------------ Salidas
def tono_bocina(hz):
    """hz = 0 -> silencio."""
    global pwm_bocina
    if hz <= 0:
        pwm_bocina.duty_u16(0)
    else:
        pwm_bocina.freq(hz)
        pwm_bocina.duty_u16(32768)  # 50 %


def aplicar_salidas(pct, patron):
    global vibracion_pct, patron_actual, inicio_patron, bocina_sonando
    if pct != vibracion_pct:
        vibracion_pct = pct
        pwm_motor.duty_u16(pct * 65535 // 100)
    if patron != patron_actual:
        patron_actual = patron
        inicio_patron = time.ticks_ms()
        tono_bocina(0)
        bocina_sonando = False


def actualizar_bocina():
    """Maquina de estados no bloqueante: alterna encendido/apagado."""
    global bocina_sonando
    if patron_actual == P_SILENCIO:
        if bocina_sonando:
            tono_bocina(0)
            bocina_sonando = False
        return
    hz, ms_on, ms_off = PATRONES[patron_actual]
    fase = time.ticks_diff(time.ticks_ms(), inicio_patron) % (ms_on + ms_off)
    debe_sonar = fase < ms_on
    if debe_sonar and not bocina_sonando:
        tono_bocina(hz)
        bocina_sonando = True
    elif not debe_sonar and bocina_sonando:
        tono_bocina(0)
        bocina_sonando = False


# ------------------------------------------------------------ Interrupcion
def isr_switch(pin):
    global pedir_alternar, ultima_interrupcion
    ahora = time.ticks_ms()
    if time.ticks_diff(ahora, ultima_interrupcion) > ANTIREBOTE_MS:
        ultima_interrupcion = ahora
        pedir_alternar = True


def alternar_sistema():
    global activo, ultimo_ciclo, bocina_sonando
    activo = not activo
    if activo:
        canal_frontal.reiniciar()
        canal_piso.reiniciar()
        ultimo_ciclo = time.ticks_add(time.ticks_ms(), -PERIODO_MS)  # primer ciclo inmediato
        led_estado.value(1)
        print("  >> EVENTO SISTEMA_ACTIVADO")
    else:
        aplicar_salidas(0, P_SILENCIO)
        tono_bocina(0)
        bocina_sonando = False
        led_estado.value(0)
        print("  >> EVENTO SISTEMA_DESACTIVADO")


def fijar_sistema(encender):
    if encender != activo:
        alternar_sistema()


# ------------------------------------------------------------ Sensores
def leer_sensor(trig, echo):
    """Devuelve (resultado, cm)."""
    trig.value(0)
    time.sleep_us(2)
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)
    us = time_pulse_us(echo, 1, TIMEOUT_ECO_US)
    if us <= 0:  # -1 / -2 = timeout
        return LECT_SIN_ECO, INF
    cm = us * 0.0343 / 2
    if cm < DIST_MIN_CM or cm > DIST_MAX_CM:
        return LECT_FUERA_RANGO, cm
    return LECT_OK, cm


# ------------------------------------------------------------ Clasificacion
def nivel_instantaneo(d):
    if d < UMBRAL_PELIGRO_CM:
        return PELIGRO
    if d < UMBRAL_PRECAUCION_CM:
        return PRECAUCION
    return LIBRE


def piso_instantaneo(d):
    return PISO_DESNIVEL if d > PISO_BASE_CM + PISO_TOLERANCIA_CM else PISO_NORMAL


def actualizar_estado(c, instantaneo):
    """Sube de inmediato, baja tras CONFIRMACIONES_BAJADA lecturas seguras."""
    previo = c.estado
    if instantaneo > c.estado:
        c.estado = instantaneo
        c.cuenta_bajada = 0
    elif instantaneo < c.estado:
        c.cuenta_bajada += 1
        if c.cuenta_bajada >= CONFIRMACIONES_BAJADA:
            c.estado = instantaneo
            c.cuenta_bajada = 0
    else:
        c.cuenta_bajada = 0
    return c.estado != previo


def intensidad_vibracion(d):
    if d >= UMBRAL_PRECAUCION_CM:
        return 0
    if d <= DIST_VIBRACION_MAX_CM:
        return 100
    return int(round(100 * (UMBRAL_PRECAUCION_CM - d) / (UMBRAL_PRECAUCION_CM - DIST_VIBRACION_MAX_CM)))


def procesar_canal(c, trig, echo, nombre, es_frontal):
    """Procesa un sensor. Devuelve True si el estado de alerta cambio."""
    r, cm = leer_sensor(trig, echo)

    if r == LECT_FUERA_RANGO:
        c.fallas += 1
        if c.fallas >= MAX_FALLAS and not c.desconectado:
            c.desconectado = True
            print("  >> EVENTO SENSOR_DESCONECTADO: [%s] %d fallas consecutivas" % (nombre, c.fallas))
        elif not c.en_falla:
            print("  >> EVENTO SENSOR_FALLA: [%s] lectura fuera de rango: %.1f cm" % (nombre, cm))
        c.en_falla = True
        return False

    if c.en_falla:
        c.en_falla = False
        c.desconectado = False
        print("  >> EVENTO SENSOR_RECUPERADO: %s" % nombre)
    c.fallas = 0

    c.distancia = c.filtro.agregar(cm)
    previo = c.estado
    if es_frontal:
        cambio = actualizar_estado(c, nivel_instantaneo(c.distancia))
    else:
        cambio = actualizar_estado(c, piso_instantaneo(c.distancia))

    if cambio:
        if es_frontal:
            print("  >> EVENTO NIVEL_OBSTACULO_CAMBIO: %s -> %s" % (NOMBRES_NIVEL[previo], NOMBRES_NIVEL[c.estado]))
        else:
            print("  >> EVENTO ESTADO_PISO_CAMBIO: %s -> %s" % (NOMBRES_PISO[previo], NOMBRES_PISO[c.estado]))
    return cambio


def decidir_salida():
    """Prioridad: desnivel > peligro > precaucion > nada."""
    vib = 0
    patron = P_SILENCIO
    if canal_piso.estado == PISO_DESNIVEL:
        vib = 100
        patron = P_DESNIVEL
    elif canal_frontal.estado == PELIGRO:
        vib = intensidad_vibracion(canal_frontal.distancia)
        patron = P_OBSTACULO
    elif canal_frontal.estado == PRECAUCION:
        vib = intensidad_vibracion(canal_frontal.distancia)
    aplicar_salidas(vib, patron)


def fmt_dist(d):
    return " sin eco" if d == INF else "%6.1fcm" % d


def imprimir_linea():
    print("t=%03d | F:%s %-10s | P:%s %-8s | VIB %3d%% | BOCINA %s" % (
        tick,
        fmt_dist(canal_frontal.distancia), NOMBRES_NIVEL[canal_frontal.estado],
        fmt_dist(canal_piso.distancia), NOMBRES_PISO[canal_piso.estado],
        vibracion_pct, NOMBRES_PATRON[patron_actual]))


def ciclo_sensores():
    global tick
    c1 = procesar_canal(canal_frontal, trig_frontal, echo_frontal, "frontal", True)
    c2 = procesar_canal(canal_piso, trig_piso, echo_piso, "piso", False)
    decidir_salida()
    if c1 or c2 or tick % LOG_CADA == 0:
        imprimir_linea()
    tick += 1


# ------------------------------------------------------------ Comunicacion
def cerrar(cliente):
    """Cierra el socket sin lanzar errores."""
    try:
        cliente.sock.close()
    except Exception:
        pass


def fallo_com(motivo):
    """Pasa a ESPERA y programa el siguiente intento con espera creciente."""
    global com_estado, proximo_intento, espera_s
    com_estado = COM_ESPERA
    print("[COM] Falla (%s). Reintento en %d s" % (motivo, espera_s))
    proximo_intento = time.ticks_add(time.ticks_ms(), espera_s * 1000)
    espera_s = min(espera_s * 2, ESPERA_MAX_S)


def perder_conexion(motivo):
    global mqtt
    if mqtt is not None:
        cerrar(mqtt)
        mqtt = None
    print("[COM] Conexion perdida. El baston sigue operando en modo local.")
    fallo_com(motivo)


def responder(cmd, aceptada, detalle):
    """Publica el resultado de una orden (no es critico si falla)."""
    try:
        mqtt.publish(T_RESPUESTA, json.dumps(
            {"cmd": cmd, "resultado": "aceptada" if aceptada else "rechazada", "detalle": detalle}))
    except Exception:
        pass


def rechazar(cmd, motivo):
    print("[ORDEN] RECHAZADA (%s): %s" % (cmd, motivo))
    responder(cmd, False, motivo)


def aceptar(cmd, detalle):
    print("[ORDEN] aceptada (%s): %s" % (cmd, detalle))
    responder(cmd, True, detalle)


def es_numero(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def procesar_orden(msg):
    """Valida y aplica una orden. Una orden invalida NO cambia nada."""
    global UMBRAL_PELIGRO_CM, UMBRAL_PRECAUCION_CM, caida_pendiente
    try:
        o = json.loads(msg)
    except ValueError:
        return rechazar("?", "JSON ilegible")
    if not isinstance(o, dict) or "cmd" not in o or "valor" not in o:
        return rechazar("?", "faltan campos cmd/valor")
    cmd, val = o["cmd"], o["valor"]

    if cmd == "sistema":
        if val not in ("on", "off"):
            return rechazar(cmd, "valor debe ser 'on' u 'off'")
        fijar_sistema(val == "on")
        return aceptar(cmd, val)

    if cmd == "umbral_peligro":
        if not es_numero(val) or not (PELIGRO_MIN <= val <= PELIGRO_MAX):
            return rechazar(cmd, "debe estar entre %d y %d cm" % (PELIGRO_MIN, PELIGRO_MAX))
        if UMBRAL_PRECAUCION_CM - val < SEPARACION_MIN:
            return rechazar(cmd, "debe ser al menos %d cm menor que precaucion (%d)" % (SEPARACION_MIN, UMBRAL_PRECAUCION_CM))
        UMBRAL_PELIGRO_CM = float(val)
        return aceptar(cmd, "peligro = %.0f cm" % val)

    if cmd == "umbral_precaucion":
        if not es_numero(val) or not (PRECAUCION_MIN <= val <= PRECAUCION_MAX):
            return rechazar(cmd, "debe estar entre %d y %d cm" % (PRECAUCION_MIN, PRECAUCION_MAX))
        if val - UMBRAL_PELIGRO_CM < SEPARACION_MIN:
            return rechazar(cmd, "debe superar a peligro (%d) por al menos %d cm" % (UMBRAL_PELIGRO_CM, SEPARACION_MIN))
        UMBRAL_PRECAUCION_CM = float(val)
        return aceptar(cmd, "precaucion = %.0f cm" % val)

    if cmd == "simular_caida":   # herramienta de prueba para provocar una caida de la comunicacion
        if not es_numero(val) or not (CAIDA_MIN_S <= val <= CAIDA_MAX_S):
            return rechazar(cmd, "debe estar entre %d y %d s" % (CAIDA_MIN_S, CAIDA_MAX_S))
        medio = o.get("medio", "broker")
        if medio not in ("broker", "wifi"):
            return rechazar(cmd, "medio debe ser 'broker' o 'wifi'")
        aceptar(cmd, "caida de %s por %d s" % (medio, val))
        caida_pendiente = (int(val), medio)   # se ejecuta fuera del callback
        return

    rechazar(str(cmd), "orden desconocida")


def al_recibir(topic, msg):
    if topic.decode() == T_ORDENES:
        print("[RX] orden: %s" % msg)
        procesar_orden(msg)


def intentar_broker():
    """Conecta al broker con Last Will, anuncia online (retenido) y se suscribe a ordenes."""
    global mqtt, com_estado, espera_s, ultimo_ping
    cliente = None
    try:
        cliente = MQTTClient(CLIENT_ID, BROKER, port=BROKER_PUERTO, keepalive=KEEPALIVE_S)
        cliente.set_last_will(T_ESTADO, b"offline", retain=True)   # testamento
        cliente.set_callback(al_recibir)
        try:
            cliente.connect(timeout=BROKER_TIMEOUT_S)
        except TypeError:          # versiones antiguas de umqtt sin parametro timeout
            cliente.connect()
        cliente.subscribe(T_ORDENES)
        cliente.publish(T_ESTADO, b"online", retain=True)
    except Exception as e:         # OSError, MQTTException...: nunca debe detener el programa
        if cliente is not None:
            cerrar(cliente)
        return fallo_com("broker: %s" % e)
    mqtt = cliente
    com_estado = COM_CONECTADO
    espera_s = ESPERA_INICIAL_S    # exito: la espera vuelve al valor inicial
    ultimo_ping = time.ticks_ms()
    print("[COM] Conectado a %s. Estado 'online' publicado." % BROKER)


def com_paso():
    """Maquina de estados de conexion. No bloquea el ciclo de sensores (salvo el connect al broker)."""
    global com_estado, t_wifi, ultimo_ping, caida_pendiente, mqtt, proximo_intento
    ahora = time.ticks_ms()

    if com_estado == COM_CONECTADO:
        try:
            mqtt.check_msg()                        # recibe ordenes (no bloqueante)
            if caida_pendiente is not None:
                seg, medio = caida_pendiente
                caida_pendiente = None
                cerrar(mqtt)                        # corte abrupto: el broker publicara el testamento
                mqtt = None
                if medio == "wifi":
                    wlan.disconnect()
                com_estado = COM_ESPERA
                proximo_intento = time.ticks_add(ahora, seg * 1000)
                print("[COM] Caida simulada (%s) durante %d s" % (medio, seg))
                return
            if not wlan.isconnected():
                raise OSError("Wi-Fi perdido")
            if time.ticks_diff(ahora, ultimo_ping) >= PING_CADA_MS:
                mqtt.ping()
                ultimo_ping = ahora
        except Exception as e:
            perder_conexion(str(e))
        return

    if com_estado == COM_ESPERA:
        if time.ticks_diff(ahora, proximo_intento) < 0:
            return
        if wlan.isconnected():
            intentar_broker()
        else:
            print("[COM] Conectando a Wi-Fi %s..." % WIFI_SSID)
            wlan.connect(WIFI_SSID, WIFI_PASS)
            t_wifi = ahora
            com_estado = COM_WIFI
        return

    # COM_WIFI: esperando que el Wi-Fi conecte (sin bloquear)
    if wlan.isconnected():
        print("[COM] Wi-Fi conectado, IP %s" % wlan.ifconfig()[0])
        intentar_broker()
    elif time.ticks_diff(ahora, t_wifi) > WIFI_TIMEOUT_MS:
        try:
            wlan.disconnect()      # cancela el intento anterior antes de reintentar
        except Exception:
            pass
        fallo_com("Wi-Fi sin respuesta (status=%s)" % wlan.status())


def construir_telemetria():
    if activo:
        f = None if canal_frontal.distancia == INF else round(canal_frontal.distancia, 1)
        p = None if canal_piso.distancia == INF else round(canal_piso.distancia, 1)
        nivel, piso = NOMBRES_NIVEL[canal_frontal.estado], NOMBRES_PISO[canal_piso.estado]
        vib, bocina = vibracion_pct, NOMBRES_PATRON[patron_actual]
    else:                          # sistema apagado: se sigue publicando como latido
        f, p, nivel, piso, vib, bocina = None, None, "LIBRE", "NORMAL", 0, "SILENCIO"
    return {"seq": seq, "frontal_cm": f, "piso_cm": p, "nivel_frontal": nivel,
            "estado_piso": piso, "vibracion_pct": vib, "bocina": bocina,
            "activo": 1 if activo else 0}


def telemetria_paso():
    """Cada PUBLICAR_CADA_MS toma una muestra. Si no hay conexion no la guarda (solo avanza seq)."""
    global ultima_pub, seq, perdidas
    ahora = time.ticks_ms()
    if time.ticks_diff(ahora, ultima_pub) < PUBLICAR_CADA_MS:
        return
    ultima_pub = ahora
    seq += 1
    if com_estado != COM_CONECTADO:
        perdidas += 1              # el receptor vera el salto en seq al reconectar
        return
    try:
        texto = json.dumps(construir_telemetria())
        mqtt.publish(T_TELEMETRIA, texto)
        print("[TX] %s" % texto)
    except Exception as e:
        perder_conexion(str(e))


def actualizar_led_com():
    """Fijo = conectado al broker. Parpadeo = sin conexion / reintentando."""
    if com_estado == COM_CONECTADO:
        led_com.value(1)
    else:
        led_com.value((time.ticks_ms() // 250) % 2)


# ------------------------------------------------------------ Programa principal
def main():
    global pedir_alternar, ultimo_ciclo
    switch.irq(trigger=Pin.IRQ_FALLING, handler=isr_switch)
    print("SafeStep Etapa 3 listo. Presiona el switch de inicio para activar.")

    while True:
        if pedir_alternar:
            pedir_alternar = False
            alternar_sistema()

        if activo and time.ticks_diff(time.ticks_ms(), ultimo_ciclo) >= PERIODO_MS:
            ultimo_ciclo = time.ticks_ms()
            ciclo_sensores()

        com_paso()
        telemetria_paso()
        actualizar_led_com()
        actualizar_bocina()
        time.sleep_ms(2)


main()