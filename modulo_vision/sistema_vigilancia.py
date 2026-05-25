"""
SecureVision — Sistema de Vigilancia Inteligente
Arquitectura: Productor-Consumidor con 3 hilos independientes

  Hilo 1 Capturador    : lee cap.read() a máxima velocidad
  Hilo 2 Inferencia    : YOLO + zonas + servo
  Hilo 3 Comunicaciones: Telegram (no bloqueante para el video)
"""
from ultralytics import YOLO
import cv2
import numpy as np
import time
import os
import sys
import fcntl
import queue
import requests
import json
import threading
import atexit
import paho.mqtt.client as mqtt
import notifier
import cloud_client

# ── Instancia única — evita alertas duplicadas ────────────────────
_lockfile = open("/tmp/securevision.lock", "w")
try:
    fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.environ["SECUREVISION_MAIN"] = "1"   # heredado por subprocesos OpenVINO
except IOError:
    # Subprocesos de OpenVINO heredan SECUREVISION_MAIN → salir silenciosamente
    if not os.environ.get("SECUREVISION_MAIN"):
        print("[ERROR] Ya hay una instancia corriendo.")
        print("        Usa: pkill -f sistema_vigilancia.py")
    sys.exit(0)

# ── Modelo YOLO ───────────────────────────────────────────────────
model = YOLO("yolov8n.onnx", task="detect")
# model = YOLO("yolov8n_openvino_model/", task="detect")

# ── Configuración ─────────────────────────────────────────────────
with open("config.json") as f:
    config = json.load(f)

CLIENTE_ID    = config.get("cliente_id", "default")
ESP32_CAM_IP  = config.get("esp32cam_ip")
ESP32_STREAM  = config.get("esp32cam_port_stream")
MOSTRAR_VIDEO = config.get("mostrar_video", False)
USE_WEBCAM    = config.get("use_webcam", False)
CFG_CAMARA    = config.get("camara_inicio", {})
CFG_EXPO      = config.get("exposicion_auto", {})

os.makedirs("evidencia", exist_ok=True)

# ── Queues entre hilos ────────────────────────────────────────────
_frame_q   = queue.Queue(maxsize=1)   # Capturador  → Inferencia
_alert_q   = queue.Queue()            # Inferencia  → Comunicaciones
_display_q = queue.Queue(maxsize=1)   # Inferencia  → ventana OpenCV

# ── Señal de parada global ────────────────────────────────────────
_corriendo = True

# ─────────────────────────────────────────────────────────────────
# SERVO — worker dedicado (1 conexión HTTP en vuelo a la vez)
# ─────────────────────────────────────────────────────────────────
SERVO_MIN          = 10
SERVO_MAX          = 170
SERVO_TRACK_DEAD   = 80     # píxeles desde el centro — zona muerta de tracking
SERVO_TRACK_STEP   = 3      # grados por corrección de tracking
SERVO_CMD_INTERVAL = 0.12   # segundos entre comandos de tracking
SERVO_SWEEP_STEP   = 2      # grados por paso en sweep (pasos pequeños = movimiento fluido)
SERVO_SWEEP_INT    = 0.10   # segundos entre pasos de sweep → 20°/s
SERVO_SWEEP_DELAY  = 2.0    # segundos sin detección antes de reanudar sweep

_servo_angulo        = 90.0  # ángulo actual
_servo_dir           = 1     # dirección sweep: +1=derecha, -1=izquierda
_servo_cmd_ts        = 0.0   # timestamp del último comando enviado
_servo_sweep_ts      = 0.0   # timestamp del último paso de sweep
_servo_deteccion_ts  = 0.0   # timestamp de la última detección de persona

_servo_pending = None
_servo_cond    = threading.Condition()

def _enviar_servo(angulo):
    """Encola ángulo; el worker siempre envía el último valor."""
    global _servo_pending
    with _servo_cond:
        _servo_pending = int(angulo)
        _servo_cond.notify()

def _servo_worker():
    global _servo_pending
    while True:
        with _servo_cond:
            while _servo_pending is None:
                _servo_cond.wait()
            angulo = _servo_pending
            _servo_pending = None
        try:
            requests.get(
                f"http://{ESP32_CAM_IP}/control?var=servo&val={angulo}",
                timeout=1
            )
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────
# CONTROL HTTP — fire-and-forget
# ─────────────────────────────────────────────────────────────────
def _ctrl(var, val):
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

def configurar_camara(centrar_servo=False):
    if USE_WEBCAM or not CFG_CAMARA:
        return
    print("[CAM] Aplicando configuración...")
    for param, valor in CFG_CAMARA.items():
        _ctrl(param, valor)
    if centrar_servo:
        _enviar_servo(90)
    print("[CAM] Listo.")

# ─────────────────────────────────────────────────────────────────
# EXPOSICIÓN AUTOMÁTICA
# ─────────────────────────────────────────────────────────────────
_BRILLO_OBJ    = CFG_EXPO.get("brillo_objetivo",  130)
_ZONA_MUERTA_E = CFG_EXPO.get("zona_muerta",       25)
_EXPO_INT      = CFG_EXPO.get("intervalo_frames",  30)
_EXPO_ACTIVO   = CFG_EXPO.get("activo",           True)
_ae_level      = 0
_frame_expo    = 0

def ajustar_exposicion(frame):
    global _ae_level, _frame_expo
    if USE_WEBCAM or not _EXPO_ACTIVO:
        return
    _frame_expo += 1
    if _frame_expo % _EXPO_INT != 0:
        return
    media = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
    dif   = _BRILLO_OBJ - media
    if abs(dif) <= _ZONA_MUERTA_E:
        return
    nuevo = max(-2, min(2, _ae_level + (1 if dif > 0 else -1)))
    if nuevo == _ae_level:
        return
    _ctrl("ae_level", nuevo)
    _ae_level = nuevo

# ─────────────────────────────────────────────────────────────────
# FLASH
# ─────────────────────────────────────────────────────────────────
_flash_activo = False

def set_flash(encendido):
    global _flash_activo
    if USE_WEBCAM or encendido == _flash_activo:
        return
    _flash_activo = encendido
    _ctrl("flash", 1 if encendido else 0)

@atexit.register
def _apagar_flash():
    if USE_WEBCAM or not _flash_activo:
        return
    try:
        requests.get(f"http://{ESP32_CAM_IP}/control?var=flash&val=0", timeout=3)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────
# ZONAS Y LÓGICA DE SOSPECHA
# ─────────────────────────────────────────────────────────────────
ZONAS = {
    "SEGURO": {
        "puntos": np.array([[0,0],[640,0],[640,155],[0,155]], np.int32),
        "nivel":  1,
    },
    "PRECAUCION": {
        "puntos": np.array([[0,150],[640,150],[640,320],[0,320]], np.int32),
        "nivel":  2,
    },
    "CRITICO": {
        "puntos": np.array([[0,315],[640,315],[640,480],[0,480]], np.int32),
        "nivel":  3,
    },
}
_ZONA_COLOR = {
    "SEGURO":     ( 34, 180,  34),
    "PRECAUCION": (  0, 165, 255),
    "CRITICO":    (  0,   0, 220),
}

T_PREC_BAJA     = 5
T_PREC_MEDIA    = 15
T_CRITICO_ALERT = 5

def get_zona(punto):
    mejor, nivel = None, 0
    for nombre, z in ZONAS.items():
        if cv2.pointPolygonTest(z["puntos"], punto, False) >= 0:
            if z["nivel"] > nivel:
                mejor, nivel = nombre, z["nivel"]
    return mejor

def nivel_sospecha(zona, tiempo):
    if zona == "PRECAUCION":
        if tiempo < T_PREC_BAJA:  return 1
        if tiempo < T_PREC_MEDIA: return 2
        return 3
    if zona == "CRITICO":
        return 2 if tiempo < T_CRITICO_ALERT else 3
    return 0

# ─────────────────────────────────────────────────────────────────
# HUD
# ─────────────────────────────────────────────────────────────────
def _dibujar_hud(frame, boxes_list, fps):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    for nombre, z in ZONAS.items():
        cv2.fillPoly(overlay, [z["puntos"]], _ZONA_COLOR[nombre])
    cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
    # Bordes y etiquetas
    ety = {"SEGURO": int(h*0.18), "PRECAUCION": int(h*0.48), "CRITICO": int(h*0.78)}
    for nombre, z in ZONAS.items():
        color = _ZONA_COLOR[nombre]
        cv2.polylines(frame, [z["puntos"]], True, color, 2)
        cv2.putText(frame, nombre, (w-160, ety[nombre]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    # Bounding boxes
    for box in boxes_list:
        x1, y1, x2, y2 = map(int, box)
        zona  = get_zona(((x1+x2)//2, y2))
        color = _ZONA_COLOR.get(zona, (255, 255, 255))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    # Barra superior
    cv2.rectangle(frame, (0, 0), (w, 38), (20, 20, 20), -1)
    cv2.putText(frame, "SecureVision", (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    cv2.putText(frame, time.strftime("%H:%M:%S"), (w//2-50, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1)
    cv2.putText(frame, f"Personas: {len(boxes_list)}  FPS: {fps:.0f}", (w-220, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1)

# ─────────────────────────────────────────────────────────────────
# ALERTAS DE VISIÓN
# ─────────────────────────────────────────────────────────────────
_historial_ids = {}   # {track_id: {zona, desde, alerta_enviada, foto_tomada, necesita_flash}}
_ultima_vision = {"ts": 0.0, "zona": None, "tid": None, "foto": None}
_lock_vision   = threading.Lock()

def _hora():
    return time.strftime("%d/%m/%Y  %H:%M:%S")

def disparar_alerta_vision(tid, zona, frame):
    """Encola alerta — el imwrite lo hace hilo_comunicaciones para no bloquear inferencia."""
    ahora = time.time()
    ts    = time.strftime("%Y%m%d_%H%M%S")
    path  = f"evidencia/ID{tid}_{zona}_{ts}.jpg"

    with _lock_vision:
        _ultima_vision.update({"ts": ahora, "zona": zona, "tid": tid, "foto": path})
        sensores_recientes = (
            _ultimo_sensor["activos"]
            and (ahora - _ultimo_sensor["ts"]) < VENTANA_FUSION
        )

    hora = _hora()
    if sensores_recientes:
        lista = " · ".join(_ultimo_sensor["activos"])
        caption = (
            f"🚨 <b>SecureVision — ALERTA COMBINADA</b>\n\n"
            f"👁️ <b>Visión:</b> ID#{tid} en zona {zona}\n"
            f"📡 <b>Sensores:</b> {lista}\n"
            f"🕐 <b>Hora:</b> {hora}"
        )
    else:
        caption = (
            f"⚠️ <b>SecureVision — Alerta de Visión</b>\n\n"
            f"📍 <b>Zona:</b> {zona}\n"
            f"👤 <b>ID:</b> #{tid}\n"
            f"🕐 <b>Hora:</b> {hora}"
        )

    tipo_evento  = "combinado" if sensores_recientes else "vision"
    nivel_evento = "CRITICO" if zona == "CRITICO" else "ALTO"
    if sensores_recientes:
        lista = " + ".join(_ultimo_sensor["activos"])
        mensaje_cloud = f"ID#{tid} — {lista}"
    else:
        mensaje_cloud = f"ID#{tid} detectado"
    _alert_q.put({"tipo": "foto", "frame": frame.copy(), "path": path,
                  "caption": caption, "silencioso": False,
                  "tipo_evento": tipo_evento, "zona": zona, "nivel": nivel_evento,
                  "mensaje": mensaje_cloud})
    print(f"[ALERTA VISION] {zona} ID#{tid}")

# ─────────────────────────────────────────────────────────────────
# MQTT Y FUSIÓN DE SENSORES
# ─────────────────────────────────────────────────────────────────
MQTT_BROKER          = "localhost"
MQTT_TOPICS          = ["securevision/pir", "securevision/vibracion", "securevision/proximidad"]
NOMBRES_SENSOR       = {
    "securevision/pir":        "PIR",
    "securevision/vibracion":  "Vibración",
    "securevision/proximidad": "Proximidad",
}

VENTANA_FUSION       = 60
VENTANA_FOTO         = 15
COOLDOWN_SENSOR      = 30
VENTANA_SENSORES     = 0.5
VENTANA_REINCIDENCIA = 120
DEBOUNCE_SENSOR      = 2.0

_lock_sensor         = threading.Lock()
_ultimo_sensor       = {"ts": 0.0, "activos": []}
_ts_ultimo_envio     = 0.0
_sensores_pendientes = []
_timer_sensor        = None
_historial_combos    = {}
_debounce_ts         = {}

def _check_reincidencia(combo_key):
    ahora     = time.time()
    registros = [t for t in _historial_combos.get(combo_key, [])
                 if ahora - t < VENTANA_REINCIDENCIA]
    registros.append(ahora)
    _historial_combos[combo_key] = registros
    return len(registros) > 1

def _determinar_nivel(sensores):
    tiene_pir  = any("PIR"        in s for s in sensores)
    tiene_vib  = any("Vibración"  in s for s in sensores)
    tiene_prox = any("Proximidad" in s for s in sensores)
    if tiene_prox and not tiene_pir and not tiene_vib:
        return None
    if tiene_prox:
        return "CRITICO"
    if tiene_pir and tiene_vib:
        reincidente = _check_reincidencia("pir_vib")
        return "ALTO" if ("Vibración" in sensores[0] or reincidente) else "MEDIO"
    if tiene_vib: return "MEDIO"
    if tiene_pir: return "BAJO"
    return None

def _enviar_sensores_acumulados():
    global _ts_ultimo_envio, _timer_sensor
    with _lock_sensor:
        sensores      = list(_sensores_pendientes)
        _sensores_pendientes.clear()
        _timer_sensor = None
        if not sensores:
            return
        ahora           = time.time()
        en_cooldown     = (ahora - _ts_ultimo_envio) < COOLDOWN_SENSOR
        vision_reciente = (ahora - _ultima_vision["ts"]) < VENTANA_FUSION
        _ultimo_sensor.update({"ts": ahora, "activos": sensores})

    nivel = _determinar_nivel(sensores)
    if nivel is None:
        print(f"[SENSOR] {sensores} — filtrado por tabla lógica")
        return
    if nivel == "BAJO":
        print(f"[SENSOR] {sensores} — nivel bajo, solo log")
        return
    if en_cooldown and nivel == "MEDIO":
        print(f"[SENSOR] {sensores} — cooldown activo")
        return

    with _lock_sensor:
        _ts_ultimo_envio = time.time()

    silencioso = nivel == "MEDIO"
    hora       = _hora()
    lista      = " · ".join(sensores)
    iconos     = {"MEDIO": "🟡", "ALTO": "🟠", "CRITICO": "🔴"}

    if vision_reciente:
        v    = _ultima_vision
        foto = v["foto"] if (time.time() - v["ts"]) < VENTANA_FOTO else None
        caption = (
            f"🚨 <b>SecureVision — ALERTA COMBINADA</b>\n\n"
            f"👁️ <b>Visión:</b> ID#{v['tid']} en zona {v['zona']}\n"
            f"📡 <b>Sensores:</b> {lista}\n"
            f"🕐 <b>Hora:</b> {hora}"
        )
        tarea = {"tipo": "foto", "path": foto, "caption": caption, "silencioso": silencioso,
                 "tipo_evento": "combinado", "zona": v["zona"], "nivel": nivel} \
                if foto else {"tipo": "texto", "mensaje": caption, "silencioso": silencioso,
                              "tipo_evento": "combinado", "zona": v["zona"], "nivel": nivel}
    else:
        tarea = {
            "tipo": "texto",
            "silencioso": silencioso,
            "tipo_evento": "sensor",
            "nivel": nivel,
            "mensaje": (
                f"{iconos.get(nivel,'🔴')} <b>SecureVision — Alerta de Sensor</b>\n\n"
                f"🔍 <b>Sensor:</b> {lista}\n"
                f"⚡ <b>Nivel:</b> {nivel}\n"
                f"🕐 <b>Hora:</b> {hora}"
            ),
        }

    _alert_q.put(tarea)
    print(f"[ALERTA SENSOR] [{nivel}] {lista}")

def _on_sensor_mqtt(topic, payload):
    global _timer_sensor
    ahora = time.time()
    if ahora - _debounce_ts.get(topic, 0) < DEBOUNCE_SENSOR:
        return
    _debounce_ts[topic] = ahora

    nombre = NOMBRES_SENSOR.get(topic, topic)
    if topic == "securevision/proximidad":
        try:
            dist_cm = float(payload)
            nombre  = f"Proximidad ({dist_cm/100:.2f}m)"
        except ValueError:
            return
    elif payload != "1":
        return

    with _lock_sensor:
        if nombre not in _sensores_pendientes:
            _sensores_pendientes.append(nombre)
        if _timer_sensor is None:
            _timer_sensor = threading.Timer(VENTANA_SENSORES, _enviar_sensores_acumulados)
            _timer_sensor.daemon = True
            _timer_sensor.start()
            print(f"[SENSOR] {nombre} — ventana abierta")
        else:
            print(f"[SENSOR] {nombre} — acumulado")

def _on_mqtt_connect(client, userdata, flags, rc, props=None):
    if rc == 0:
        for t in MQTT_TOPICS:
            client.subscribe(t)
        print(f"[MQTT] Conectado · topics: {MQTT_TOPICS}")
    else:
        print(f"[MQTT] Error rc={rc}")

def _on_mqtt_message(client, userdata, msg):
    _on_sensor_mqtt(msg.topic, msg.payload.decode().strip())

_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
_mqtt.on_connect = _on_mqtt_connect
_mqtt.on_message = _on_mqtt_message
try:
    _mqtt.connect(MQTT_BROKER, 1883)
    _mqtt.loop_start()
    print(f"[MQTT] Conectando a {MQTT_BROKER}:1883...")
except Exception as e:
    print(f"[MQTT] No disponible: {e} — continuando sin sensores")

# ─────────────────────────────────────────────────────────────────
# VIDEO CAPTURE
# ─────────────────────────────────────────────────────────────────
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;8000000"  # 8s

if USE_WEBCAM:
    cap = cv2.VideoCapture(0)
else:
    cap = cv2.VideoCapture(f"http://{ESP32_CAM_IP}:{ESP32_STREAM}/stream")
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

def _reconectar():
    global cap
    cap.release()
    fuente  = 0 if USE_WEBCAM else f"http://{ESP32_CAM_IP}:{ESP32_STREAM}/stream"
    intento = 0
    while True:
        intento += 1
        if intento > 1:
            time.sleep(min((intento - 1) * 2, 10))
        print(f"[CAM] Reconectando... intento {intento}")
        cap = cv2.VideoCapture(fuente)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cap.isOpened():
            print("[CAM] Stream recuperado.")
            time.sleep(1.5)   # esperar a que el ESP32 libere el handler anterior
            return

# ─────────────────────────────────────────────────────────────────
# HILO 1 — CAPTURADOR
# ─────────────────────────────────────────────────────────────────
def hilo_capturador():
    """Lee frames a máxima velocidad y mantiene solo el más reciente."""
    fallos = 0
    while _corriendo:
        ret, frame = cap.read()
        if not ret:
            fallos += 1
            if fallos >= 5:
                print("[CAM] Stream perdido.")
                _reconectar()
                fallos = 0
            continue
        fallos = 0
        # Si la queue está llena, descartar frame viejo
        try:
            _frame_q.put_nowait(frame)
        except queue.Full:
            try:
                _frame_q.get_nowait()
            except queue.Empty:
                pass
            _frame_q.put_nowait(frame)

# ─────────────────────────────────────────────────────────────────
# HILO 2 — INFERENCIA
# ─────────────────────────────────────────────────────────────────
def hilo_inferencia():
    global _servo_angulo, _servo_dir, _servo_cmd_ts, _servo_sweep_ts, _servo_deteccion_ts
    t_ant = time.time()

    while _corriendo:
        try:
            frame = _frame_q.get(timeout=1)
        except queue.Empty:
            continue

        ahora = time.time()
        fps   = 1 / max(ahora - t_ant, 0.001)
        t_ant = ahora

        results = model.track(frame, classes=[0], conf=0.60,
                              persist=True, verbose=False, imgsz=320)

        total = 0
        boxes = []
        necesita_flash = False

        if results[0].boxes is not None and results[0].boxes.id is not None:
            raw_boxes = results[0].boxes.xyxy.cpu().numpy()
            ids       = results[0].boxes.id.cpu().numpy().astype(int)
            total     = len(ids)
            boxes     = list(raw_boxes)

            for box, tid in zip(raw_boxes, ids):
                x1, y1, x2, y2 = map(int, box)
                zona = get_zona(((x1 + x2) // 2, y2))

                if tid not in _historial_ids:
                    _historial_ids[tid] = {
                        "zona": zona, "desde": ahora,
                        "alerta_enviada": False, "foto_tomada": False,
                        "necesita_flash": False,
                    }
                est = _historial_ids[tid]

                if est["zona"] != zona:
                    est.update({"zona": zona, "desde": ahora,
                                "alerta_enviada": False, "foto_tomada": False,
                                "necesita_flash": False})

                tiempo = ahora - est["desde"]
                niv    = nivel_sospecha(zona, tiempo)

                if zona == "PRECAUCION" and niv == 3 and not est["foto_tomada"]:
                    disparar_alerta_vision(tid, zona, frame)
                    est["foto_tomada"] = est["alerta_enviada"] = True

                if zona == "CRITICO":
                    if tiempo >= (T_CRITICO_ALERT - 2) and not est["foto_tomada"]:
                        est["necesita_flash"] = True
                    if tiempo >= T_CRITICO_ALERT and not est["foto_tomada"]:
                        disparar_alerta_vision(tid, zona, frame)
                        est["foto_tomada"] = est["alerta_enviada"] = True
                        est["necesita_flash"] = False

                if est["necesita_flash"]:
                    necesita_flash = True

        set_flash(necesita_flash)
        ajustar_exposicion(frame)

        # ── Servo ─────────────────────────────────────────────────
        if not USE_WEBCAM:
            ancho = frame.shape[1]
            if total > 0:
                _servo_deteccion_ts = ahora
                _servo_sweep_ts     = ahora   # evita que sweep dispare al perder detección
                # Tracking: paso fijo en la dirección de la persona
                cx       = int((boxes[0][0] + boxes[0][2]) / 2)
                error_px = cx - ancho // 2
                if abs(error_px) > SERVO_TRACK_DEAD and ahora - _servo_cmd_ts >= SERVO_CMD_INTERVAL:
                    direction     = -1 if error_px > 0 else 1   # invertido: ↑ángulo = cámara va a la IZQUIERDA
                    _servo_angulo = max(SERVO_MIN, min(SERVO_MAX, _servo_angulo + direction * SERVO_TRACK_STEP))
                    _enviar_servo(int(_servo_angulo))
                    _servo_cmd_ts = ahora
            else:
                # Sweep: solo reanudar tras SERVO_SWEEP_DELAY segundos sin detección
                sin_deteccion = ahora - _servo_deteccion_ts
                if sin_deteccion >= SERVO_SWEEP_DELAY and ahora - _servo_sweep_ts >= SERVO_SWEEP_INT:
                    _servo_angulo += _servo_dir * SERVO_SWEEP_STEP
                    if _servo_angulo >= SERVO_MAX:
                        _servo_angulo, _servo_dir = float(SERVO_MAX), -1
                    elif _servo_angulo <= SERVO_MIN:
                        _servo_angulo, _servo_dir = float(SERVO_MIN), 1
                    _enviar_servo(int(_servo_angulo))
                    _servo_sweep_ts = ahora
                    _servo_cmd_ts   = ahora

        # ── Display ───────────────────────────────────────────────
        if MOSTRAR_VIDEO:
            frame_d = frame.copy()
            _dibujar_hud(frame_d, boxes, fps)
            try:
                _display_q.put_nowait(frame_d)
            except queue.Full:
                try:
                    _display_q.get_nowait()
                except queue.Empty:
                    pass
                _display_q.put_nowait(frame_d)

        print(f"FPS: {fps:.1f}  Personas: {total}", end='\r')

# ─────────────────────────────────────────────────────────────────
# HILO 3 — COMUNICACIONES (Telegram — no bloquea el video)
# ─────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
# HILO 4 — COMANDOS REMOTOS (polling cloud cada 3s)
# ─────────────────────────────────────────────────────────────────
def _aplicar_comando(cmd):
    global ZONAS
    tipo    = cmd.get("tipo")
    payload = cmd.get("payload", {})
    if tipo == "brillo":
        nivel = int(payload.get("ae_level", 0))
        _ctrl("ae_level", max(-2, min(2, nivel)))
        print(f"[CMD] Brillo → ae_level={nivel}")
    elif tipo == "zonas":
        y1 = int(payload.get("y_seguro",    155))
        y2 = int(payload.get("y_precaucion", 320))
        y1 = max(50,  min(y1, 380))
        y2 = max(y1 + 50, min(y2, 460))
        ZONAS = {
            "SEGURO":     {"puntos": np.array([[0,0],[640,0],[640,y1],[0,y1]], np.int32), "nivel": 1},
            "PRECAUCION": {"puntos": np.array([[0,y1-5],[640,y1-5],[640,y2],[0,y2]], np.int32), "nivel": 2},
            "CRITICO":    {"puntos": np.array([[0,y2-5],[640,y2-5],[640,480],[0,480]], np.int32), "nivel": 3},
        }
        print(f"[CMD] Zonas → SEGURO 0-{y1} | PRECAUCION {y1}-{y2} | CRITICO {y2}-480")

def hilo_comandos():
    _api = os.getenv("CLOUD_API_URL", "").rstrip("/")
    if not _api:
        return
    while _corriendo:
        try:
            r = requests.get(f"{_api}/comandos/{CLIENTE_ID}/pendientes", timeout=5)
            if r.ok:
                for cmd in r.json():
                    _aplicar_comando(cmd)
        except Exception:
            pass
        time.sleep(3)

def hilo_comunicaciones():
    while _corriendo:
        try:
            tarea = _alert_q.get(timeout=1)
        except queue.Empty:
            continue
        tipo = tarea.get("tipo")
        if tipo == "texto":
            notifier.enviar_texto(tarea["mensaje"],
                                  silencioso=tarea.get("silencioso", False))
            cloud_client.registrar_evento(
                cliente_id = CLIENTE_ID,
                tipo       = tarea.get("tipo_evento", "sensor"),
                zona       = tarea.get("zona"),
                nivel      = tarea.get("nivel"),
                mensaje    = tarea.get("mensaje", "")[:300],
            )
        elif tipo == "foto":
            frame_data = tarea.get("frame")
            path       = tarea.get("path", "")
            if frame_data is not None and path:
                cv2.imwrite(path, frame_data)
            notifier.enviar_foto(path,
                                 tarea.get("caption", ""),
                                 silencioso=tarea.get("silencioso", False))
            cloud_client.registrar_evento(
                cliente_id = CLIENTE_ID,
                tipo       = tarea.get("tipo_evento", "vision"),
                zona       = tarea.get("zona"),
                nivel      = tarea.get("nivel"),
                mensaje    = tarea.get("mensaje", tarea.get("caption", ""))[:300],
            )
        _alert_q.task_done()

# ─────────────────────────────────────────────────────────────────
# ARRANQUE
# ─────────────────────────────────────────────────────────────────
configurar_camara(centrar_servo=True)

_hilos = [
    threading.Thread(target=hilo_capturador,     daemon=True, name="Capturador"),
    threading.Thread(target=hilo_inferencia,     daemon=True, name="Inferencia"),
    threading.Thread(target=hilo_comunicaciones, daemon=True, name="Comunicaciones"),
    threading.Thread(target=_servo_worker,       daemon=True, name="Servo"),
    threading.Thread(target=hilo_comandos,       daemon=True, name="Comandos"),
]
for h in _hilos:
    h.start()

print("SecureVision iniciado. Ctrl+C para salir.")

try:
    while True:
        if MOSTRAR_VIDEO:
            try:
                frame = _display_q.get(timeout=0.1)
                cv2.imshow("SecureVision", frame)
            except queue.Empty:
                pass
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        else:
            time.sleep(0.1)
except KeyboardInterrupt:
    print("\n[INFO] Deteniendo sistema...")

_corriendo = False

# Esperar a que los hilos vean _corriendo=False antes de liberar cap
for h in _hilos:
    h.join(timeout=3)

try:
    _mqtt.loop_stop()
    _mqtt.disconnect()
except Exception:
    pass

cap.release()
if MOSTRAR_VIDEO:
    cv2.destroyAllWindows()
