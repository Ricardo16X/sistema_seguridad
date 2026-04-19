# SecureVision 🔒
### Sistema Inteligente de Vigilancia Perimetral

> Proyecto Final — Arquitectura de Computadoras I  
> Universidad Mariano Gálvez de Guatemala · 2025

---

## ¿Qué es SecureVision?

SecureVision es un sistema de vigilancia perimetral inteligente basado en una arquitectura IoT de tres capas. Combina visión computacional con sensores físicos para detectar, clasificar y registrar actividad sospechosa en el perímetro de una propiedad, generando evidencia fotográfica automática y notificaciones en tiempo real.

El sistema no solo detecta presencia — distingue entre quién pasa, quién ronda y quién intenta ingresar.

---

## Arquitectura del Sistema

SecureVision implementa una arquitectura IoT de tres capas: **Edge → Fog → Cloud**.

```
┌─────────────────────────────────────────────────────┐
│                    CAPA EDGE                        │
│                                                     │
│   ESP32-CAM + Pan/Tilt        ESP32 Secundario      │
│   (video en stream)           (PIR · SW-420 · Son)  │
└──────────────────┬──────────────────┬───────────────┘
                   │ WiFi/MJPEG       │ MQTT
┌──────────────────▼──────────────────▼───────────────┐
│                    CAPA FOG                         │
│                   (Laptop)                          │
│                                                     │
│   Detección humana con YOLOv8n (ONNX)              │
│   Zonas poligonales de precaución y crítico         │
│   Tracking por ID + niveles de sospecha             │
│   Fusión de eventos: visión + sensores              │
│   Captura automática de evidencia fotográfica       │
└──────────────────────────────┬──────────────────────┘
                               │ HTTP POST (eventos procesados)
┌──────────────────────────────▼──────────────────────┐
│                   CAPA CLOUD                        │
│                                                     │
│   Backend API (FastAPI)                             │
│   Base de datos de eventos (Supabase)               │
│   Dashboard web en tiempo real                      │
│   Notificaciones push (Telegram Bot)                │
│   Acceso por token QR por dispositivo               │
└─────────────────────────────────────────────────────┘
```

**Principio fundamental:** Todo el procesamiento de inteligencia ocurre en la laptop (Fog). A la nube únicamente llegan eventos ya procesados — alertas, snapshots y estado de sensores. Nunca video crudo.

---

## Escenarios de Detección

### Escenario 1 — Transeúnte normal
Una persona camina frente a la propiedad sin detenerse.

```
Cámara detecta persona → entra y sale de zona de precaución
→ tiempo de permanencia bajo umbral
→ sin alerta · sin registro
```

### Escenario 2 — Comportamiento sospechoso (merodeador)
Una persona se detiene y permanece en las inmediaciones por un tiempo prolongado.

```
Cámara detecta persona → permanece en zona de precaución
→ tracker acumula tiempo de permanencia
→ tiempo supera umbral configurado
→ ALERTA PRECAUCIÓN: notificación Telegram + registro en dashboard
```

### Escenario 3 — Intrusión (zona crítica)
Una persona intenta acceder a la propiedad o ingresa a la zona restringida.

```
Cámara detecta persona → ingresa a zona crítica
→ captura automática de foto de evidencia
→ ALERTA CRÍTICA: Telegram con foto adjunta + dashboard en tiempo real
```

### Escenario 4 — Activación múltiple de sensores
Se detecta actividad simultánea en más de un sensor (vibración en puerta + movimiento PIR).

```
SW-420 detecta golpe en puerta/ventana
PIR confirma presencia en el área
→ fusión de eventos en capa Fog
→ ALERTA CRÍTICA: correlación multisensor → mayor certeza, menor falso positivo
```

### Escenario 5 — Actividad nocturna
Detección fuera del horario configurado como activo.

```
Cualquier detección fuera del horario permitido
→ nivel de alerta escala automáticamente
→ notificación inmediata independientemente del tiempo de permanencia
```

---

## Niveles de Alerta

| Nivel | Color | Condición |
|---|---|---|
| **Precaución** | Amarillo | Persona en zona de precaución por más del umbral configurado |
| **Crítico** | Rojo | Persona en zona crítica, activación múltiple de sensores, o detección nocturna |

---

*La documentación técnica detallada (stack, instalación, API) se irá completando conforme avance el desarrollo.*
