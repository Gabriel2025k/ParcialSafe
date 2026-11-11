# SafeStep - Guia de actividad (modulo de comunicacion MQTT)

Extiende el proyecto SafeStep (baston inteligente con ESP32) con comunicacion MQTT:
el dispositivo en Wokwi (MicroPython) publica telemetria JSON, recibe ordenes y se
mantiene estable ante fallos; un receptor en Python valida y guarda los datos en CSV;
un monitor vigila ONLINE / SIN_DATOS / OFFLINE.

```
Wokwi (ESP32) --telemetria/estado/respuesta--> broker.hivemq.com --> receptor.py --> salida/telemetria.csv
      ^                                              |
      +------------------ordenes--------------------+--> monitor.py  --> salida/eventos_comunicacion.csv
```

## Estructura
```
wokwi/      main.py, diagram.json, libraries.txt   (codigo del dispositivo)
backend/    config.py, receptor.py, monitor.py, enviar.py, tests/
requirements.txt
```
La simulacion en Python de la Etapa 2 (`simulacion_python/`) sigue igual en el repositorio.

## 1. Dispositivo (Wokwi)
1. En wokwi.com abre el proyecto de la Etapa 2 (MicroPython, ESP32).
2. Reemplaza `main.py` y `diagram.json` por los de `wokwi/`.
3. Cambia `GRUPO` en `main.py` **y** en `backend/config.py` (deben ser iguales y unicos: el broker es publico).
4. Ejecuta. Consola: `[COM] Conectado ...`. LED azul (GPIO 15): fijo = conectado, parpadeo = sin conexion.
5. Pulsa el switch de inicio (o envia la orden `sistema on`) para activar los sensores.

## 2. Backend (Python 3.9+)
```
pip install -r requirements.txt
cd backend
python receptor.py                       # terminal 1 -> salida/telemetria.csv
python monitor.py --silencio 10          # terminal 2 -> salida/eventos_comunicacion.csv
python -m unittest discover -s tests -t .    # 16 pruebas
```
`--silencio` debe ser >= 3 x intervalo de publicacion (2 s) = 6 s minimo.
Para probar con un broker local: `SAFESTEP_BROKER=localhost python receptor.py`.

## 3. Esquema de temas (base `utp/ds8/<GRUPO>/safestep`)
| Tema | Direccion | Contenido | Retenido |
|---|---|---|---|
| `.../telemetria` | dispositivo -> broker | JSON con lecturas y estado, cada 2 s | No |
| `.../estado` | dispositivo -> broker | `online` al conectar; `offline` por Last Will | **Si** |
| `.../ordenes` | broker -> dispositivo | JSON `{"cmd":..., "valor":...}` | No |
| `.../respuesta` | dispositivo -> broker | resultado de cada orden (aceptada/rechazada) | No |

Telemetria: `{"seq":42,"frontal_cm":85.3,"piso_cm":30.1,"nivel_frontal":"PRECAUCION","estado_piso":"NORMAL","vibracion_pct":17,"bocina":"SILENCIO","activo":1}`
(`null` en una distancia = sin eco o sistema apagado; `seq` permite detectar muestras perdidas).

## 4. Ordenes (cada una se valida; las invalidas se rechazan sin cambiar nada)
| Orden | Valores validos |
|---|---|
| `{"cmd":"sistema","valor":"on"}` | `"on"` / `"off"` |
| `{"cmd":"umbral_peligro","valor":50}` | 15 a 80 cm, y al menos 10 cm menor que precaucion |
| `{"cmd":"umbral_precaucion","valor":120}` | 60 a 300 cm, y al menos 10 cm mayor que peligro |
| `{"cmd":"simular_caida","valor":20,"medio":"broker"}` | 5 a 60 s; medio `broker` o `wifi` (solo para pruebas) |

## 5. Comportamiento ante fallos
- Reintento de conexion con espera 2, 4, 8, 16, 32, 60, 60... s (sin bloquear los sensores).
- **Mientras esta desconectado el baston sigue operando en modo local**: sensores, vibracion y bocina
  no dependen de la red. Solo se pausa la telemetria (no se acumula; `seq` revela el hueco).
- Last Will: `offline` retenido; al reconectar publica `online` retenido.

## 6. Como provocar cada prueba
Publica ordenes con `python enviar.py ordenes '<json>'` (o con el cliente web de HiveMQ).
| Escenario | Como provocarlo |
|---|---|
| Normal | Ejecutar todo y ver `[TX]` en Wokwi y filas en el CSV |
| Orden valida | `enviar.py ordenes '{"cmd":"umbral_peligro","valor":50}'` |
| Orden invalida | `enviar.py ordenes '{"cmd":"umbral_peligro","valor":500}'` |
| Caida broker / Wi-Fi | `enviar.py ordenes '{"cmd":"simular_caida","valor":20,"medio":"broker"}'` (o `"wifi"`) |
| Dato invalido al receptor | `enviar.py telemetria 'esto no es json'` |
| Reinicio del dispositivo | Stop y Play en Wokwi (el receptor avisa que `seq` reinicio) |
| Conectado sin publicar | Poner `PUBLICAR_CADA_MS = 60000` y reiniciar (el monitor pasa a SIN_DATOS) |
| Detener simulacion | Boton Stop de Wokwi: el broker publica el testamento -> OFFLINE |
