from ultralytics import YOLO
import cv2
import numpy as np
import time
import os
import requests
import json
import threading
import atexit
import paho.mqtt.client as mqtt
import notifier

model = YOLO("yolov8n.onnx")

# Configuración desde archivo
with open("config.json") as f:
    config = json.load(f)
ESP32_CAM_IP      = config.get("esp32cam_ip")
ESP32_CAM_STREAM  = config.get("esp32cam_port_stream")
ESP32_CAM_CONTROL = config.get("esp32cam_port_control")
MOSTRAR_VIDEO     = config.get("mostrar_video", False)
USE_WEBCAM        = config.get("use_webcam", False)
CFG_CAMARA        = config.get("camara_inicio", {})
CFG_EXPO          = config.get("exposicion_auto", {})

if USE_WEBCAM:
    print("[CONFIG] Modo webcam local")
    cap = cv2.VideoCapture(0)
else:
    print(f"[CONFIG] IP: {ESP32_CAM_IP} | Stream: {ESP32_CAM_STREAM} | Control: {ESP32_CAM_CONTROL}")
    cap = cv2.VideoCapture(f"http://{ESP32_CAM_IP}:{ESP32_CAM_STREAM}/stream")
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 800)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 600)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

os.makedirs("evidencia", exist_ok=True)

# ── Exposición automática ─────────────────────────────────────────
_BRILLO_OBJETIVO  = CFG_EXPO.get("brillo_objetivo", 130)
_ZONA_MUERTA      = CFG_EXPO.get("zona_muerta", 25)
_EXPO_INTERVALO   = CFG_EXPO.get("intervalo_frames", 30)
_EXPO_ACTIVO      = CFG_EXPO.get("activo", True)

_ae_level_actual  = 0
_frame_expo       = 0

def _ctrl(var, val):
    """Envía comando al ESP32-CAM en hilo background — nunca bloquea el loop."""
    if USE_WEBCAM:
        return
    def _send():
        try:
            requests.get(
                f"http://{ESP32_CAM_IP}/control?var={var}&val={val}",
                timeout=1
            )
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

def configurar_camara():
    if USE_WEBCAM or not CFG_CAMARA:
        return
    print("[CAM] Aplicando configuración inicial...")
    for param, valor in CFG_CAMARA.items():
        _ctrl(param, valor)
    print("[CAM] Configuración aplicada.")

def ajustar_exposicion(frame):
    global _ae_level_actual, _frame_expo
    if USE_WEBCAM or not _EXPO_ACTIVO:
        return
    _frame_expo += 1
    if _frame_expo % _EXPO_INTERVALO != 0:
        return
    gris  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    media = float(gris.mean())
    dif   = _BRILLO_OBJETIVO - media
    if abs(dif) <= _ZONA_MUERTA:
        return
    nuevo = _ae_level_actual + (1 if dif > 0 else -1)
    nuevo = max(-2, min(2, nuevo))
    if nuevo == _ae_level_actual:
        return
    _ctrl("ae_level", nuevo)
    print(f"[EXPO] media={media:.0f}  objetivo={_BRILLO_OBJETIVO}  ae_level={nuevo}")
    _ae_level_actual = nuevo

# ── MQTT ──────────────────────────────────────────────────────────
MQTT_BROKER  = "localhost"
MQTT_TOPICS  = ["securevision/pir", "securevision/vibracion", "securevision/proximidad"]

NOMBRES_SENSOR = {
    "securevision/pir":         "PIR",
    "securevision/vibracion":   "Vibración",
    "securevision/proximidad":  "Proximidad",
}

# ── Estado de fusión de alertas ───────────────────────────────────
VENTANA_FUSION  = 60   # segundos — ventana para combinar visión + sensor
VENTANA_FOTO    = 15   # segundos — máximo para adjuntar foto en alerta combinada
COOLDOWN_SENSOR = 30   # segundos — mínimo entre alertas de sensor

_lock                = threading.Lock()
_ultima_vision       = {"ts": 0.0, "zona": None, "tid": None, "foto": None}
_ultimo_sensor       = {"ts": 0.0, "activos": []}
_ts_ultimo_envio     = 0.0   # controla cooldown de alertas de sensor
VENTANA_SENSORES     = 0.5   # segundos — ventana para acumular sensores simultáneos
_sensores_pendientes = []
_timer_sensor        = None
VENTANA_REINCIDENCIA = 120   # segundos — ventana para detectar reincidencia
_historial_combos    = {}    # {"combo": [ts1, ts2, ...]}

# ── Umbrales de sospecha (segundos) ──────────────────────────────
T_PREC_BAJA     = 5    # 0–5s  en precaucion → sospecha baja
T_PREC_MEDIA    = 15   # 5–15s en precaucion → sospecha media
T_PREC_FOTO     = 13   # >= 13s en precaucion → toma foto
T_CRITICO_ALERT = 5    # >= 5s  en critico    → alerta

# ── Zonas ─────────────────────────────────────────────────────────
ZONAS = {
    "SEGURO": {
        "puntos": np.array([[50,30],[1230,30],[1230,250],[50,250]], np.int32),
        "nivel": 1,
    },
    "PRECAUCION": {
        "puntos": np.array([[50,230],[1230,230],[1230,480],[50,480]], np.int32),
        "nivel": 2,
    },
    "CRITICO": {
        "puntos": np.array([[50,460],[1230,460],[1230,715],[50,715]], np.int32),
        "nivel": 3,
    },
}

# ── Estado por ID ─────────────────────────────────────────────────
historial         = {}
flash_activo      = False  # flag global — una sola fuente de verdad del flash
_captura_activa   = False  # True mientras el hilo de alta resolución usa el flash

def get_zona(punto):
    mejor = None
    nivel = 0
    for nombre, z in ZONAS.items():
        if cv2.pointPolygonTest(z["puntos"], punto, False) >= 0:
            if z["nivel"] > nivel:
                mejor, nivel = nombre, z["nivel"]
    return mejor

def nivel_sospecha(zona, tiempo):
    """Devuelve (0-3): 0=ninguna 1=baja 2=media 3=alta/critico."""
    if zona == "PRECAUCION":
        if tiempo < T_PREC_BAJA:  return 1
        if tiempo < T_PREC_MEDIA: return 2
        return 3
    if zona == "CRITICO":
        if tiempo < T_CRITICO_ALERT: return 2
        return 3
    return 0

def set_flash(encendido):
    if USE_WEBCAM:
        return
    global flash_activo
    # No apagar el flash si el hilo de captura de alta resolución lo está usando
    if not encendido and _captura_activa:
        return
    if encendido == flash_activo:
        return
    flash_activo = encendido  # actualizar estado local antes del envío async
    print(f"[FLASH] {'ON' if encendido else 'OFF'}")
    _ctrl("flash", 1 if encendido else 0)

def _apagar_flash_al_salir():
    if USE_WEBCAM or not flash_activo:
        return
    try:
        requests.get(
            f"http://{ESP32_CAM_IP}/control?var=flash&val=0",
            timeout=3
        )
        print("[FLASH] Apagado al salir")
    except Exception:
        pass

atexit.register(_apagar_flash_al_salir)

def tomar_foto(frame, track_id, zona, nivel):
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = f"evidencia/ID{track_id}_{zona}_N{nivel}_{ts}.jpg"
    # Guardar frame actual inmediatamente — no bloquea el loop
    cv2.imwrite(path, frame)
    print(f"[FOTO] Evidencia guardada: {path}")
    if USE_WEBCAM:
        return path
    # Intentar reemplazar con foto de mayor resolución del ESP32 en background
    def _fetch():
        global _captura_activa
        _captura_activa = True
        try:
            requests.get(f"http://{ESP32_CAM_IP}/control?var=flash&val=1", timeout=2)
            time.sleep(0.4)  # esperar a que el AEC se ajuste al flash
            r = requests.get(f"http://{ESP32_CAM_IP}/capture", timeout=15)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
                print(f"[FOTO ESP32-CAM] {path} actualizada en alta resolución")
        except Exception as e:
            print(f"[FOTO] ESP32 no disponible — usando frame local ({e})")
        finally:
            _captura_activa = False
            try:
                requests.get(f"http://{ESP32_CAM_IP}/control?var=flash&val=0", timeout=2)
            except Exception:
                pass
    threading.Thread(target=_fetch, daemon=True).start()
    return path

def _hora():
    return time.strftime("%d/%m/%Y  %H:%M:%S")

def _disparar_vision(tid, zona, foto):
    hora = _hora()
    caption = (
        f"⚠️ <b>SecureVision — Alerta de Visión</b>\n\n"
        f"📍 <b>Zona:</b> {zona}\n"
        f"👤 <b>ID:</b> #{tid}\n"
        f"🕐 <b>Hora:</b> {hora}"
    )
    print(f"[ALERTA VISION] {zona} ID#{tid}")
    if foto:
        notifier.enviar_foto(foto, caption)
    else:
        notifier.enviar_texto(caption)

def _check_reincidencia(combo_key):
    ahora     = time.time()
    registros = _historial_combos.get(combo_key, [])
    registros = [t for t in registros if ahora - t < VENTANA_REINCIDENCIA]
    registros.append(ahora)
    _historial_combos[combo_key] = registros
    return len(registros) > 1

def _determinar_nivel(sensores):
    tiene_pir  = any("PIR"        in s for s in sensores)
    tiene_vib  = any("Vibración"  in s for s in sensores)
    tiene_prox = any("Proximidad" in s for s in sensores)

    if tiene_prox and not tiene_pir and not tiene_vib:
        return None          # proximidad sola → falso positivo

    if tiene_prox:
        return "CRITICO"     # proximidad + cualquier otro → máxima certeza

    if tiene_pir and tiene_vib:
        vib_primero = "Vibración" in sensores[0]
        reincidente = _check_reincidencia("pir_vib")
        return "ALTO" if (vib_primero or reincidente) else "MEDIO"

    if tiene_vib:  return "MEDIO"
    if tiene_pir:  return "BAJO"
    return None

def _disparar_sensor(sensores, nivel="MEDIO", silencioso=False):
    hora  = _hora()
    lista = " · ".join(sensores)
    iconos = {"MEDIO": "🟡", "ALTO": "🟠", "CRITICO": "🔴"}
    mensaje = (
        f"{iconos.get(nivel, '🔴')} <b>SecureVision — Alerta de Sensor</b>\n\n"
        f"🔍 <b>Sensor:</b> {lista}\n"
        f"⚡ <b>Nivel:</b> {nivel}\n"
        f"🕐 <b>Hora:</b> {hora}\n\n"
        f"<i>Revisa el panel para más detalles.</i>"
    )
    print(f"[ALERTA SENSOR] [{nivel}] {lista}")
    notifier.enviar_texto(mensaje, silencioso=silencioso)

def _disparar_combinada(tid, zona, foto, sensores):
    hora = _hora()
    lista = " · ".join(sensores)
    caption = (
        f"🚨 <b>SecureVision — ALERTA COMBINADA</b>\n\n"
        f"👁️ <b>Visión:</b> ID#{tid} en zona {zona}\n"
        f"📡 <b>Sensores:</b> {lista}\n"
        f"🕐 <b>Hora:</b> {hora}\n\n"
        f"⚠️ <i>Alta certeza — múltiples fuentes confirmadas.</i>"
    )
    print(f"[ALERTA COMBINADA] {zona} ID#{tid} + {lista}")
    if foto:
        notifier.enviar_foto(foto, caption)
    else:
        notifier.enviar_texto(caption)

def disparar_alerta_vision(tid, zona, foto):
    global _ts_ultimo_envio
    ahora = time.time()
    with _lock:
        _ultima_vision.update({"ts": ahora, "zona": zona, "tid": tid, "foto": foto})
        sensores_recientes = (
            _ultimo_sensor["activos"]
            and (ahora - _ultimo_sensor["ts"]) < VENTANA_FUSION
        )
    if sensores_recientes:
        _disparar_combinada(tid, zona, foto, _ultimo_sensor["activos"])
    else:
        _disparar_vision(tid, zona, foto)

def _enviar_sensores_acumulados():
    global _ts_ultimo_envio, _timer_sensor
    with _lock:
        sensores      = list(_sensores_pendientes)
        _sensores_pendientes.clear()
        _timer_sensor = None
        if not sensores:
            return
        ahora           = time.time()
        en_cooldown     = (ahora - _ts_ultimo_envio) < COOLDOWN_SENSOR
        vision_reciente = (ahora - _ultima_vision["ts"]) < VENTANA_FUSION
        _ultimo_sensor.update({"ts": ahora, "activos": sensores})

    # Determinar nivel antes de tocar el cooldown
    nivel = _determinar_nivel(sensores)
    if nivel is None:
        print(f"[SENSOR] {sensores} — filtrado por tabla lógica")
        return
    if nivel == "BAJO":
        print(f"[SENSOR] {sensores} — nivel bajo, solo log")
        return

    # ALTO y CRITICO ignoran cooldown — MEDIO lo respeta
    if en_cooldown and nivel == "MEDIO":
        print(f"[SENSOR] {sensores} — cooldown activo, ignorado")
        return

    with _lock:
        _ts_ultimo_envio = ahora

    silencioso = nivel == "MEDIO"

    if vision_reciente:
        v    = _ultima_vision
        foto = v["foto"] if (ahora - v["ts"]) < VENTANA_FOTO else None
        _disparar_combinada(v["tid"], v["zona"], foto, sensores)
    else:
        _disparar_sensor(sensores, nivel=nivel, silencioso=silencioso)

def _on_sensor_mqtt(topic, payload):
    global _timer_sensor
    nombre = NOMBRES_SENSOR.get(topic, topic)

    if topic == "securevision/proximidad":
        try:
            dist_cm = float(payload)
            nombre  = f"Proximidad ({dist_cm / 100:.2f}m)"
        except ValueError:
            return
    elif payload != "1":
        return

    with _lock:
        if nombre not in _sensores_pendientes:
            _sensores_pendientes.append(nombre)
        if _timer_sensor is None:
            _timer_sensor = threading.Timer(VENTANA_SENSORES, _enviar_sensores_acumulados)
            _timer_sensor.daemon = True
            _timer_sensor.start()
            print(f"[SENSOR] {nombre} — ventana abierta ({VENTANA_SENSORES}s)")
        else:
            print(f"[SENSOR] {nombre} — acumulado en ventana")

# ── Cliente MQTT (corre en hilo propio) ───────────────────────────
def _on_connect(client, userdata, flags, rc, props=None):
    if rc == 0:
        for t in MQTT_TOPICS:
            client.subscribe(t)
        print(f"[MQTT] Conectado · suscrito a {MQTT_TOPICS}")
    else:
        print(f"[MQTT] Error de conexión rc={rc}")

def _on_message(client, userdata, msg):
    _on_sensor_mqtt(msg.topic, msg.payload.decode().strip())

_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
_mqtt.on_connect = _on_connect
_mqtt.on_message = _on_message
try:
    _mqtt.connect(MQTT_BROKER, 1883)
    _mqtt.loop_start()
    print(f"[MQTT] Iniciando conexión a {MQTT_BROKER}:1883")
except Exception as e:
    print(f"[MQTT] No se pudo conectar: {e} — continuando sin sensores")

# ── Loop principal ────────────────────────────────────────────────
def _reconectar():
    """Reabre el stream hasta lograrlo. Bloquea hasta que haya señal."""
    global cap
    cap.release()
    fuente = 0 if USE_WEBCAM else f"http://{ESP32_CAM_IP}:{ESP32_CAM_STREAM}/stream"
    intento = 0
    while True:
        intento += 1
        print(f"[CAM] Reconectando... intento {intento}")
        time.sleep(min(intento * 2, 30))  # back-off: 2s, 4s, 6s … tope 30s
        cap = cv2.VideoCapture(fuente)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cap.isOpened():
            print("[CAM] Stream recuperado.")
            configurar_camara()
            return

configurar_camara()
print("Sistema de vigilancia iniciado. Ctrl+C para salir.")
fps_anterior = time.time()

while True:
    for _ in range(1):
        cap.grab()

    ret, frame = cap.read()
    if not ret:
        if USE_WEBCAM:
            break  # webcam no tiene sentido reconectar
        print("[CAM] Stream perdido.")
        _reconectar()
        fps_anterior = time.time()
        continue

    results = model.track(frame, classes=[0], conf=0.65,
                          persist=True, verbose=False, imgsz=416)

    total          = 0
    necesita_flash = False  # se evalúa una vez por frame

    if results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        ids   = results[0].boxes.id.cpu().numpy().astype(int)
        total = len(ids)
        ahora = time.time()

        for box, tid in zip(boxes, ids):
            x1, y1, x2, y2 = map(int, box)
            pie  = ((x1 + x2) // 2, y2)
            zona = get_zona(pie)

            if tid not in historial:
                historial[tid] = {
                    "zona": zona, "desde": ahora,
                    "alerta_enviada": False, "foto_tomada": False,
                    "necesita_flash": False
                }
            estado = historial[tid]

            # Cambio de zona → reset
            if estado["zona"] != zona:
                estado["zona"]           = zona
                estado["desde"]          = ahora
                estado["alerta_enviada"] = False
                estado["foto_tomada"]    = False
                estado["necesita_flash"] = False

            tiempo = ahora - estado["desde"]
            niv    = nivel_sospecha(zona, tiempo)

            # ── Acciones por zona ─────────────────────────────────
            if zona == "PRECAUCION" and niv == 3 and not estado["foto_tomada"]:
                estado["ultima_foto"] = tomar_foto(frame, tid, zona, niv)
                estado["foto_tomada"] = True

            if zona == "PRECAUCION" and niv == 3 and not estado["alerta_enviada"]:
                disparar_alerta_vision(tid, zona, estado.get("ultima_foto"))
                estado["alerta_enviada"] = True

            if zona == "CRITICO":
                # 2s antes del umbral → marcar que necesita flash
                if tiempo >= (T_CRITICO_ALERT - 2) and not estado["foto_tomada"]:
                    estado["necesita_flash"] = True

                # Al umbral → foto + alerta + ya no necesita flash
                if tiempo >= T_CRITICO_ALERT and not estado["foto_tomada"]:
                    estado["ultima_foto"] = tomar_foto(frame, tid, zona, niv)
                    estado["foto_tomada"]    = True
                    estado["necesita_flash"] = False

                if tiempo >= T_CRITICO_ALERT and not estado["alerta_enviada"]:
                    foto = f"evidencia/ID{tid}_CRITICO_N3_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                    disparar_alerta_vision(tid, zona, foto if os.path.exists(foto) else estado.get("ultima_foto"))
                    estado["alerta_enviada"] = True

            # Acumular si algún ID necesita flash este frame
            if estado["necesita_flash"]:
                necesita_flash = True

    # ── Decisión única de flash por frame ─────────────────────────
    set_flash(necesita_flash)
    ajustar_exposicion(frame)

    fps = 1 / (time.time() - fps_anterior)
    fps_anterior = time.time()
    if MOSTRAR_VIDEO:
        for box, tid in zip(boxes if total > 0 else [], ids if total > 0 else []):
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, f"ID#{tid}", (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.imshow("SecureVision", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    print(f"FPS: {fps:.1f}  Personas: {total}", end='\r')

cap.release()