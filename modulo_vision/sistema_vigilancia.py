from ultralytics import YOLO
import cv2
import numpy as np
import time
import os
import requests
import json
import threading
import paho.mqtt.client as mqtt
import notifier

model = YOLO("yolov8n.onnx")

# Configuración desde archivo
with open("config.json") as f:
    config = json.load(f)
ESP32_CAM_IP      = config.get("esp32cam_ip")
ESP32_CAM_STREAM  = config.get("esp32cam_port_stream")
ESP32_CAM_CONTROL = config.get("esp32cam_port_control")
MOSTRAR_VIDEO    = config.get("mostrar_video", False)

print(f"[CONFIG] IP: {ESP32_CAM_IP} | Stream: {ESP32_CAM_STREAM} | Control: {ESP32_CAM_CONTROL}")

cap = cv2.VideoCapture(f"http://{ESP32_CAM_IP}:{ESP32_CAM_STREAM}/stream")
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 800)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 600)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

os.makedirs("evidencia", exist_ok=True)

# ── MQTT ──────────────────────────────────────────────────────────
MQTT_BROKER  = "localhost"
MQTT_TOPICS  = ["securevision/pir", "securevision/vibracion", "securevision/sonido"]

NOMBRES_SENSOR = {
    "securevision/pir":       "PIR",
    "securevision/vibracion": "Vibración",
    "securevision/sonido":    "Sonido",
}

# ── Estado de fusión de alertas ───────────────────────────────────
VENTANA_FUSION  = 60   # segundos — ventana para combinar visión + sensor
COOLDOWN_SENSOR = 30   # segundos — mínimo entre alertas de sensor

_lock              = threading.Lock()
_ultima_vision     = {"ts": 0.0, "zona": None, "tid": None, "foto": None}
_ultimo_sensor     = {"ts": 0.0, "activos": []}
_ts_ultimo_envio   = 0.0   # controla cooldown de alertas de sensor

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
historial    = {}
flash_activo = False  # flag global — una sola fuente de verdad del flash

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
    """Cambia el flash solo si el estado es diferente al actual."""
    global flash_activo
    if encendido == flash_activo:
        return
    val = 1 if encendido else 0
    try:
        requests.get(f"http://{ESP32_CAM_IP}/control?var=flash&val={val}", timeout=2)
        flash_activo = encendido
        print(f"[FLASH] {'ON' if encendido else 'OFF'}")
    except Exception as e:
        print(f"[FLASH ERROR] {e}")

def tomar_foto(frame, track_id, zona, nivel):
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = f"evidencia/ID{track_id}_{zona}_N{nivel}_{ts}.jpg"
    try:
        response = requests.get(f"http://{ESP32_CAM_IP}/capture", timeout=5)
        if response.status_code == 200:
            with open(path, "wb") as f:
                f.write(response.content)
            print(f"[FOTO ESP32-CAM] {path}")
        else:
            cv2.imwrite(path, frame)
            print(f"[FOTO FALLBACK] {path}")
    except Exception as e:
        cv2.imwrite(path, frame)
        print(f"[FOTO FALLBACK] {path} — {e}")
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

def _disparar_sensor(sensores):
    hora = _hora()
    lista = " · ".join(sensores)
    mensaje = (
        f"📡 <b>SecureVision — Alerta de Sensor</b>\n\n"
        f"🔴 <b>Sensor:</b> {lista}\n"
        f"🕐 <b>Hora:</b> {hora}\n\n"
        f"<i>Revisa el panel para más detalles.</i>"
    )
    print(f"[ALERTA SENSOR] {lista}")
    notifier.enviar_texto(mensaje)

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

def _on_sensor_mqtt(topic, payload):
    global _ts_ultimo_envio
    if payload != "1":
        return
    ahora  = time.time()
    nombre = NOMBRES_SENSOR.get(topic, topic)
    with _lock:
        _ultimo_sensor.update({"ts": ahora, "activos": [nombre]})
        vision_reciente = (ahora - _ultima_vision["ts"]) < VENTANA_FUSION
        en_cooldown     = (ahora - _ts_ultimo_envio) < COOLDOWN_SENSOR
    if en_cooldown:
        print(f"[SENSOR] {nombre} — cooldown activo, ignorado")
        return
    _ts_ultimo_envio = ahora
    if vision_reciente:
        v = _ultima_vision
        _disparar_combinada(v["tid"], v["zona"], v["foto"], [nombre])
    else:
        _disparar_sensor([nombre])

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
print("Sistema de vigilancia iniciado. Ctrl+C para salir.")
fps_anterior = time.time()

while True:
    for _ in range(1):
        cap.grab()

    ret, frame = cap.read()
    if not ret:
        break

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