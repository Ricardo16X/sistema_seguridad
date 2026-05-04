# SecureVision
### Sistema Inteligente de Vigilancia Perimetral

> Proyecto Final — Arquitectura de Computadoras I  
> Universidad Mariano Gálvez de Guatemala · 2025

---

## ¿Qué es SecureVision?

SecureVision es un sistema de vigilancia perimetral inteligente basado en una arquitectura IoT de tres capas. Combina visión computacional con sensores físicos para detectar, clasificar y registrar actividad sospechosa en el perímetro de una propiedad, generando evidencia fotográfica automática y notificaciones en tiempo real vía Telegram.

El sistema no solo detecta presencia — distingue entre quién pasa, quién ronda y quién intenta ingresar. Y cuando la cámara y los sensores confirman lo mismo al mismo tiempo, eleva la alerta como combinada de alta certeza.

---

## Arquitectura del Sistema

SecureVision implementa una arquitectura IoT de tres capas: **Edge → Fog → Cloud**.

```
┌─────────────────────────────────────────────────────┐
│                    CAPA EDGE                        │
│                                                     │
│   ESP32-CAM (AI-Thinker)      ESP32 DevKit          │
│   MJPEG stream + /capture     PIR · SW-420 · KY-037 │
│   Flash controlado por Fog    MQTT por WiFi          │
└──────────────────┬──────────────────┬───────────────┘
                   │ HTTP/MJPEG       │ MQTT
┌──────────────────▼──────────────────▼───────────────┐
│                    CAPA FOG (Laptop)                 │
│                                                     │
│   sistema_vigilancia.py                             │
│   ├── YOLOv8n ONNX — detección de personas          │
│   ├── Tracking por ID + zonas poligonales           │
│   ├── Mosquitto MQTT — fusión de eventos            │
│   ├── Niveles de sospecha con umbrales de tiempo    │
│   ├── Captura automática de evidencia               │
│   └── Alertas Telegram (visión / sensor / combinada)│
└─────────────────────────────────────────────────────┘
```

> **Principio fundamental:** todo el procesamiento de inteligencia ocurre en la laptop. La nube recibe solo eventos ya procesados. El sistema funciona aunque no haya internet.

---

## Escenarios de Detección

### Escenario 1 — Transeúnte normal
```
Persona entra y sale de zona de precaución
→ tiempo bajo umbral → sin alerta · sin registro
```

### Escenario 2 — Merodeador (zona de precaución)
```
Persona permanece en zona de precaución > 13s
→ captura de foto → alerta Telegram precaución
```

### Escenario 3 — Intrusión (zona crítica)
```
Persona entra a zona crítica por > 5s
→ foto de evidencia → ALERTA CRÍTICA en Telegram
```

### Escenario 4 — Sensor activado sin visión
```
PIR / vibración / sonido activado
→ alerta Telegram con sensor identificado
```

### Escenario 5 — Alerta combinada (máxima certeza)
```
Visión + sensor activos dentro de ventana de 60s
→ foto + contexto completo → "Alta certeza — múltiples fuentes confirmadas"
```

---

## Stack Técnico

| Capa | Tecnología |
|---|---|
| Detección | YOLOv8n exportado a ONNX — inferencia CPU pura |
| Visión | OpenCV 4 — stream MJPEG, zonas poligonales, tracking |
| Mensajería | Mosquitto 2.x + Paho MQTT |
| Notificaciones | Telegram Bot API |
| Firmware cámara | ESP32-CAM AI-Thinker — WiFiManager + mDNS |
| Firmware sensores | ESP32 DevKit — PIR HC-SR501, SW-420, KY-037 |
| Lenguaje principal | Python 3.14 |

### ¿Por qué YOLOv8n ONNX en CPU?

Benchmark real en Ryzen 3 5300U (sin GPU):

| Resolución | Latencia | FPS |
|---|---|---|
| imgsz=320 | 35ms | 28 FPS |
| **imgsz=416** | **35ms** | **28 FPS** ← configuración usada |
| imgsz=640 | 74ms | 13 FPS |

La variante nano con ONNX corre a 28 FPS sin GPU, suficiente para vigilancia. Todo el procesamiento ocurre localmente — sin latencia de red, sin dependencia de internet.

---

## Estructura del Proyecto

```
sistema_seguridad/
├── modulo_vision/
│   ├── sistema_vigilancia.py   # núcleo del sistema
│   ├── notifier.py             # alertas Telegram
│   ├── config.json             # configuración ESP32-CAM
│   ├── benchmark.py            # benchmark de inferencia CPU
│   ├── camara.py               # prototipo inicial de detección
│   ├── zona_restringida.py     # prototipo de zona única
│   └── export_yolo.py          # exportación .pt → .onnx
├── modulo_esp32/
│   ├── securevision_camera.ino # firmware ESP32-CAM
│   └── securevision_sensores.ino # firmware ESP32 + sensores
├── mosquitto.conf              # config broker MQTT
└── README.md
```

---

## Instalación y Uso

### Requisitos
- Python 3.10+
- Mosquitto 2.x (`sudo dnf install mosquitto`)
- ESP32-CAM AI-Thinker
- ESP32 DevKit + PIR HC-SR501, SW-420, KY-037

### Configuración

```bash
# 1. Crear entorno virtual e instalar dependencias
cd modulo_vision
python -m venv venv
source venv/bin/activate
pip install ultralytics opencv-python paho-mqtt requests python-dotenv

# 2. Crear archivo de credenciales
echo "TELEGRAM_BOT_TOKEN=tu_token" > .env
echo "TELEGRAM_CHAT_ID=tu_chat_id" >> .env

# 3. Ajustar IP del ESP32-CAM en config.json
#    (por defecto usa mDNS: securevision.local)
```

### Ejecución

```bash
# Terminal 1 — MQTT broker
mosquitto -c mosquitto.conf

# Terminal 2 — sistema de vigilancia
cd modulo_vision && source venv/bin/activate
python sistema_vigilancia.py
```

### Modo prueba (sin hardware)
Activa `"use_webcam": true` en `config.json` y simula sensores con:
```bash
mosquitto_pub -h localhost -t "securevision/pir" -m "1"
```

### Flashear ESP32
Abrir los `.ino` en Arduino IDE con las librerías:
- `esp32` board package
- `WiFiManager` by tzapu
- `PubSubClient` by Nick O'Leary

---

## Niveles de Alerta

| Nivel | Condición |
|---|---|
| Precaución | Persona en zona de precaución > 13s |
| Crítico | Persona en zona crítica > 5s |
| Sensor | PIR, vibración o sonido activado |
| Combinada | Visión + sensor dentro de 60s — alta certeza |
