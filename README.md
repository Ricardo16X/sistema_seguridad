# SecureVision
### Sistema Inteligente de Vigilancia Perimetral

> Proyecto Final — Arquitectura de Computadoras I  
> Universidad Mariano Gálvez de Guatemala · 2025

---

## ¿Qué es SecureVision?

SecureVision es un sistema de vigilancia perimetral inteligente basado en una arquitectura IoT de tres capas (**Edge → Fog → Cloud**). Combina visión computacional con sensores físicos para detectar, clasificar y registrar actividad sospechosa en el perímetro de una propiedad, generando evidencia fotográfica automática y notificaciones en tiempo real vía Telegram.

El sistema no solo detecta presencia — distingue entre quién pasa, quién ronda y quién intenta ingresar. Aplica una tabla lógica de eventos para filtrar falsas alarmas y clasifica cada alerta en niveles de sospecha antes de notificar. Cuenta además con un dashboard web de monitoreo en tiempo real y control remoto de parámetros del sistema.

---

## Arquitectura del Sistema

SecureVision implementa una arquitectura IoT de tres capas completa:

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CAPA EDGE                                  │
│                                                                     │
│   ESP32-CAM (AI-Thinker)            ESP32 DevKit                   │
│   MJPEG stream + /capture           PIR HC-SR501                   │
│   Flash controlado por Fog          Vibración SW-420               │
│   Servo pan SG90 (seguimiento)      Proximidad HC-SR04             │
│   WiFiManager + mDNS                MQTT por WiFi                  │
└──────────────────┬──────────────────────────┬───────────────────────┘
                   │ HTTP / MJPEG             │ MQTT (Mosquitto)
┌──────────────────▼──────────────────────────▼───────────────────────┐
│                     CAPA FOG (Laptop / PC)                          │
│                                                                     │
│   sistema_vigilancia.py (5 threads concurrentes)                   │
│   ├── YOLOv8n ONNX — detección de personas en CPU                  │
│   ├── Tracking por ID + zonas poligonales (SEGURO / PRECAUCIÓN /   │
│   │   CRÍTICO) + niveles de sospecha por tiempo de permanencia     │
│   ├── Mosquitto MQTT — fusión de sensores PIR / Vib / Proximidad   │
│   ├── Tabla lógica de eventos — filtro de falsas alarmas           │
│   ├── Captura automática de evidencia fotográfica en alta res.     │
│   ├── Alertas Telegram (visión / sensor / combinada)               │
│   ├── Servo worker — seguimiento automático de personas            │
│   └── Comandos cloud — ajuste remoto de parámetros en caliente     │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ HTTPS (REST)
┌────────────────────────────────▼────────────────────────────────────┐
│                        CAPA CLOUD                                   │
│                                                                     │
│   modulo_cloud / FastAPI  →  Supabase (PostgreSQL)                 │
│   ├── POST /evento        — log de eventos procesados              │
│   ├── GET  /eventos/{id}  — historial con filtros                  │
│   ├── GET  /stats/{id}    — estadísticas para dashboard            │
│   ├── POST /comando       — comandos desde dashboard               │
│   └── GET  /comandos/{id}/pendientes — polling del fog layer       │
│                                                                     │
│   modulo_dashboard / Streamlit                                     │
│   ├── Tabla de eventos en tiempo real (refresco 5 s)               │
│   ├── Visualización de estadísticas por zona y nivel               │
│   └── Control remoto: exposición, umbrales de zona, brillo flash   │
└─────────────────────────────────────────────────────────────────────┘
```

> **Principio fundamental:** todo el procesamiento de inteligencia ocurre en el fog layer (laptop). La nube recibe únicamente eventos ya clasificados. El sistema funciona aunque no haya internet; la nube y el dashboard son complementarios.

---

## Escenarios de Detección

### Escenario 1 — Transeúnte normal
```
Persona entra y sale de zona de precaución
→ tiempo bajo umbral → sin alerta · sin registro
```

### Escenario 2 — Merodeador (zona de precaución)
```
Persona permanece en zona de precaución > 15 s
→ captura de foto → alerta Telegram precaución
```

### Escenario 3 — Intrusión (zona crítica)
```
Persona entra a zona crítica por > 5 s
→ foto de evidencia en alta resolución → ALERTA CRÍTICA en Telegram
```

### Escenario 4 — Sensor activado sin visión
```
PIR / vibración / proximidad activado
→ tabla lógica evalúa combinación → alerta según nivel
```

### Escenario 5 — Alerta combinada (máxima certeza)
```
Visión + sensor activos dentro de ventana de 60 s
→ foto + contexto completo → "Alta certeza — múltiples fuentes confirmadas"
```

### Escenario 6 — Reincidencia de sensores
```
PIR + Vibración se activan más de una vez en 2 minutos
→ escala automáticamente de nivel MEDIO a ALTO
```

---

## Tabla Lógica de Eventos

El sistema evalúa la combinación de sensores activos antes de enviar cualquier alerta, eliminando falsas alarmas por eventos aislados.

| PIR | Vibración | Proximidad | Nivel | Acción |
|:---:|:---------:|:----------:|-------|--------|
| ✅ | ❌ | ❌ | BAJO | Solo log — sin Telegram |
| ❌ | ✅ | ❌ | MEDIO | Telegram silencioso |
| ❌ | ❌ | ✅ | — | Filtrado — falso positivo |
| ✅ | ✅ | ❌ | MEDIO / ALTO | ALTO si vibración fue primero o hay reincidencia |
| ✅ | ❌ | ✅ | CRÍTICO | Telegram urgente + foto |
| ❌ | ✅ | ✅ | CRÍTICO | Telegram urgente + foto |
| ✅ | ✅ | ✅ | CRÍTICO | Telegram urgente + foto |

**Reglas adicionales:**
- Los sensores que disparan dentro de una ventana de 500 ms se agrupan en un solo mensaje
- Alertas ALTO y CRÍTICO ignoran el cooldown — siempre se envían
- La foto solo se adjunta si la detección visual ocurrió en los últimos 15 segundos

---

## Stack Técnico

| Capa | Tecnología | Propósito |
|------|-----------|-----------|
| Detección | YOLOv8n exportado a ONNX | Inferencia CPU pura — clase 0 (persona) |
| Visión | OpenCV 4.13 | Stream MJPEG, zonas poligonales, tracking |
| Mensajería | Mosquitto 2.x + Paho MQTT | Fusión de sensores desde dispositivos embebidos |
| Notificaciones | Telegram Bot API | Alertas con foto y formato HTML |
| Cloud API | FastAPI + Supabase REST | Persistencia de eventos y comandos remotos |
| Dashboard | Streamlit | Monitoreo web en tiempo real y control remoto |
| Firmware cámara | ESP32-CAM AI-Thinker | MJPEG stream, servo SG90, flash, WiFiManager + mDNS |
| Firmware sensores | ESP32 DevKit | PIR HC-SR501, SW-420, HC-SR04 — MQTT sobre WiFi |
| Lenguaje principal | Python 3.10+ | Sistema fog, cloud API y dashboard |

### ¿Por qué YOLOv8n ONNX en CPU?

Benchmark real en Ryzen 3 5300U (sin GPU):

| Resolución | Latencia | FPS |
|---|---|---|
| imgsz=320 | 35 ms | 28 FPS |
| **imgsz=416** | **35 ms** | **28 FPS** ← configuración usada |
| imgsz=640 | 74 ms | 13 FPS |

La variante nano con ONNX corre a 28 FPS sin GPU, suficiente para vigilancia en tiempo real. Todo el procesamiento ocurre localmente — sin latencia de red, sin dependencia de internet.

---

## Estructura del Proyecto

```
sistema_seguridad/
├── modulo_vision/
│   ├── sistema_vigilancia.py      # núcleo del sistema (5 threads)
│   ├── notifier.py                # alertas Telegram con nivel de sospecha
│   ├── cloud_client.py            # cliente REST fire-and-forget hacia cloud API
│   ├── benchmark.py               # benchmark de inferencia CPU (5 configs)
│   ├── config.json                # configuración ESP32, exposición, zonas
│   ├── export_yolo.py             # exportación .pt → .onnx
│   ├── camara.py                  # prototipo inicial de detección
│   ├── zona_restringida.py        # prototipo de zona única
│   ├── yolov8n.pt                 # modelo PyTorch (6.5 MB)
│   ├── yolov8n.onnx               # modelo ONNX activo (13 MB)
│   └── evidencia/                 # fotos capturadas automáticamente
├── modulo_dashboard/
│   ├── app.py                     # dashboard Streamlit multi-tenant
│   └── requirements.txt
├── modulo_cloud/
│   ├── main.py                    # API FastAPI → Supabase
│   └── requirements.txt
├── modulo_esp32/
│   ├── securevision_camera.ino    # firmware ESP32-CAM (stream + servo + flash)
│   └── securevision_sensores.ino  # firmware ESP32 + sensores físicos
├── mosquitto.conf                 # config broker MQTT local
└── README.md
```

---

## Instalación y Uso

### Requisitos del sistema
- Python 3.10+
- Mosquitto 2.x: `sudo dnf install mosquitto` (Fedora) / `sudo apt install mosquitto` (Ubuntu)
- Hardware edge (opcional para pruebas):
  - ESP32-CAM AI-Thinker
  - ESP32 DevKit + PIR HC-SR501 + SW-420 + HC-SR04

---

### Capa Fog — Sistema de Vigilancia

```bash
cd sistema_seguridad/modulo_vision

# Crear entorno virtual e instalar dependencias
python -m venv venv
source venv/bin/activate
pip install ultralytics opencv-python paho-mqtt requests python-dotenv

# Crear archivo de credenciales
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
CLOUD_API_URL=https://securevision-cloud-production.up.railway.app
EOF

# (Opcional) Verificar rendimiento de inferencia
python benchmark.py

# Ajustar IP del ESP32-CAM en config.json si no usa mDNS
# Por defecto: "esp32cam_ip": "securevision.local"
```

**Ejecutar el sistema:**

```bash
# Terminal 1 — broker MQTT
mosquitto -c mosquitto.conf

# Terminal 2 — sistema de vigilancia principal
cd modulo_vision && source venv/bin/activate
python sistema_vigilancia.py
```

---

### Capa Cloud — API y Dashboard (opcional)

La cloud API ya está desplegada en Railway (`https://securevision-cloud-production.up.railway.app`). Para ejecutarla localmente:

```bash
cd sistema_seguridad/modulo_cloud
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configurar variables de entorno
export SUPABASE_URL=tu_supabase_url
export SUPABASE_API_KEY=tu_supabase_key

uvicorn main:app --host 0.0.0.0 --port 8000
```

**Dashboard de monitoreo:**

```bash
cd sistema_seguridad/modulo_dashboard
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

streamlit run app.py --server.port=8501
```

El dashboard es multi-tenant: usa el parámetro `?cliente_id=<uuid>` en la URL para filtrar eventos por instalación.

---

### Modo prueba (sin hardware)

1. Activa `"use_webcam": true` en `config.json` para usar la webcam del equipo
2. Simula activaciones de sensores desde otra terminal:

```bash
mosquitto_pub -h localhost -t "securevision/pir"         -m "1"
mosquitto_pub -h localhost -t "securevision/vibracion"   -m "1"
mosquitto_pub -h localhost -t "securevision/proximidad"  -m "75.0"
```

---

### Flashear ESP32

Abrir los `.ino` en Arduino IDE con las siguientes librerías instaladas:
- `esp32` board package (Espressif)
- `WiFiManager` by tzapu
- `PubSubClient` by Nick O'Leary

**Primera ejecución — provisioning WiFi:**

Cada ESP32 crea un AP de configuración en el primer arranque:

| Dispositivo | SSID del AP | Contraseña |
|-------------|-------------|------------|
| ESP32-CAM   | `SecureVision-Setup` | `securevision123` |
| ESP32 + sensores | `SecureVision-Sensores` | `securevision123` |

Conectarse al AP, abrir `192.168.4.1`, ingresar credenciales WiFi. Desde ese momento arranca automáticamente y se publica en la red local vía mDNS (`securevision.local`).

---

## Niveles de Alerta

| Nivel | Condición | Telegram | Notificación |
|---|---|---|---|
| BAJO | Solo PIR | ❌ Solo log | — |
| MEDIO | Vibración sola · PIR + Vib ambiguo | ✅ Texto | Silenciosa |
| ALTO | PIR + Vib con Vib primero · reincidente | ✅ Texto | Normal |
| CRÍTICO | Cualquier combinación con Proximidad | ✅ Foto | Normal |
| Combinada | Visión + sensor dentro de 60 s | ✅ Foto | Normal |

---

## Control Remoto (Dashboard → Sistema)

El dashboard permite ajustar parámetros del sistema en caliente sin reiniciarlo:

- **Exposición de cámara** — ajuste de brillo del stream ESP32-CAM
- **Umbrales de zona** — tiempo mínimo en zona precaución y crítica para generar alerta
- **Brillo del flash** — intensidad del LED al capturar evidencia

Los comandos viajan por el ciclo: Dashboard → Supabase → Cloud API → Fog polling (cada 10 s) → aplicación inmediata sin reinicio.

---

## Flujo de Evidencia

Cada alerta PRECAUCIÓN, CRÍTICO o Combinada genera automáticamente:

1. Solicitud de foto en alta resolución al ESP32-CAM (`/capture`)
2. Guardado local en `evidencia/ID{track_id}_{zona}_{timestamp}.jpg`
3. Adjunto al mensaje Telegram correspondiente
4. Registro del evento en la cloud API con metadatos completos

---

## Notas de Implementación

- **Arquitectura multi-hilo:** 5 threads independientes (captura, inferencia, comunicaciones, servo, comandos) con colas thread-safe — el procesamiento de video nunca es bloqueado por I/O de red.
- **Lock-file:** previene múltiples instancias simultáneas del sistema.
- **Reconexión automática:** reintento con backoff exponencial si el stream del ESP32-CAM se interrumpe.
- **Watchdog hardware:** el ESP32-CAM incluye un watchdog por software de 8 segundos para auto-recuperarse de cuelgues.
- **Modelo:** YOLOv8n filtra a clase 0 (persona) con umbral de confianza 0.60 — reducción de falsos positivos sin sacrificar velocidad.
