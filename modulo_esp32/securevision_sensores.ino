/**
 * SecureVision — Módulo de Sensores (ESP32-DevKit)
 *
 * Sensores: PIR HC-SR501, Vibración SW-420, Ultrasonido HC-SR04
 * Protocolo: MQTT sobre WiFi
 *
 * Diseño NO BLOQUEANTE: sin delay() en el loop principal.
 * El HC-SR04 usa interrupción en el pin ECHO — nunca congela el CPU.
 */

#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>

// ── Configuración ─────────────────────────────────────────────────
#define AP_NAME     "SecureVision-Sensores"
#define AP_PASSWORD "securevision123"
#define MQTT_BROKER "192.168.0.32"
#define MQTT_PORT   1883
#define DEVICE_ID   "esp32-sensores"

// ── Pines ─────────────────────────────────────────────────────────
#define PIN_PIR       18
#define PIN_VIBRACION 19
#define PIN_TRIG      22
#define PIN_ECHO      23

// ── Topics MQTT ───────────────────────────────────────────────────
#define TOPIC_PIR         "securevision/pir"
#define TOPIC_VIBRACION   "securevision/vibracion"
#define TOPIC_PROXIMIDAD  "securevision/proximidad"

// ── Umbrales ──────────────────────────────────────────────────────
#define DISTANCIA_UMBRAL_CM  100   // alerta si objeto < 1 metro

// ─────────────────────────────────────────────────────────────────
// SENSORES DIGITALES (PIR + Vibración) — estructura con cooldown
// ─────────────────────────────────────────────────────────────────
struct Sensor {
  uint8_t      pin;
  const char*  topic;
  unsigned long cooldown_ms;
  unsigned long ultimo_envio;
  bool          estado_anterior;
};

Sensor sensores[] = {
  { PIN_PIR,       TOPIC_PIR,       8000, 0, false },
  { PIN_VIBRACION, TOPIC_VIBRACION, 3000, 0, false },
};
const int N_SENSORES = sizeof(sensores) / sizeof(sensores[0]);

// ─────────────────────────────────────────────────────────────────
// HC-SR04 — lógica NO BLOQUEANTE con interrupción en ECHO
// ─────────────────────────────────────────────────────────────────
volatile unsigned long _echo_inicio   = 0;
volatile unsigned long _echo_fin      = 0;
volatile bool          _echo_listo    = false;

// ISR: captura tiempos de subida y bajada del pulso ECHO
void IRAM_ATTR isr_echo() {
  if (digitalRead(PIN_ECHO) == HIGH) {
    _echo_inicio = micros();
    _echo_listo  = false;
  } else {
    _echo_fin   = micros();
    _echo_listo = true;
  }
}

// Máquina de estados del sonar
enum EstadoSonar { SONAR_ESPERA, SONAR_LEER };
EstadoSonar   _estado_sonar      = SONAR_ESPERA;
unsigned long _t_sonar           = 0;
unsigned long _ultimo_proximidad = 0;

#define COOLDOWN_PROXIMIDAD_MS 5000   // ms entre alertas de proximidad
#define SONAR_TIMEOUT_MS       30     // ms máximo esperando eco

void actualizar_sonar(unsigned long ahora) {
  switch (_estado_sonar) {

    case SONAR_ESPERA:
      if (ahora - _ultimo_proximidad >= COOLDOWN_PROXIMIDAD_MS) {
        // Disparar pulso TRIG (12µs — negligible)
        _echo_listo = false;
        digitalWrite(PIN_TRIG, LOW);
        delayMicroseconds(2);
        digitalWrite(PIN_TRIG, HIGH);
        delayMicroseconds(10);
        digitalWrite(PIN_TRIG, LOW);
        _t_sonar      = ahora;
        _estado_sonar = SONAR_LEER;
      }
      break;

    case SONAR_LEER:
      if (_echo_listo) {
        // Eco recibido — calcular distancia
        unsigned long dur = _echo_fin - _echo_inicio;
        float dist = dur * 0.0343f / 2.0f;   // cm
        if (dist > 0.0f && dist < DISTANCIA_UMBRAL_CM) {
          char payload[10];
          snprintf(payload, sizeof(payload), "%.1f", dist);
          publicar(TOPIC_PROXIMIDAD, payload);
          Serial.printf("[PROX] %.1f cm\n", dist);
        }
        _ultimo_proximidad = ahora;
        _estado_sonar      = SONAR_ESPERA;

      } else if (ahora - _t_sonar >= SONAR_TIMEOUT_MS) {
        // Sin eco en 30ms → fuera de rango, evitar re-disparo inmediato
        _ultimo_proximidad = ahora;
        _estado_sonar      = SONAR_ESPERA;
      }
      break;
  }
}

// ─────────────────────────────────────────────────────────────────
// WiFi — reconexión no bloqueante
// ─────────────────────────────────────────────────────────────────
unsigned long _t_wifi_perdida  = 0;
unsigned long _t_wifi_intento  = 0;
#define WIFI_RETRY_MS    10000    // cada 10s intenta reconectar
#define WIFI_RESTART_MS  300000  // reinicia si lleva 5 min sin red

void conectar_wifi_inicial() {
  WiFiManager wm;
  wm.setAPCallback([](WiFiManager* wm) {
    Serial.println("[WiFi] Modo AP — conéctate a: " AP_NAME);
    Serial.println("[WiFi] Abre: 192.168.4.1");
  });
  if (!wm.autoConnect(AP_NAME, AP_PASSWORD)) {
    Serial.println("[WiFi] Sin conexión inicial — reiniciando");
    delay(2000);
    ESP.restart();
  }
  Serial.printf("[WiFi] Conectado — IP: %s\n", WiFi.localIP().toString().c_str());
}

// Devuelve true si WiFi está listo para usar
bool verificar_wifi(unsigned long ahora) {
  if (WiFi.status() == WL_CONNECTED) {
    _t_wifi_perdida = 0;
    return true;
  }
  // Primera vez que se pierde
  if (_t_wifi_perdida == 0) {
    _t_wifi_perdida = ahora;
    Serial.println("[WiFi] Conexión perdida — reconectando...");
  }
  // Reinicio de emergencia tras 5 minutos sin red
  if (ahora - _t_wifi_perdida >= WIFI_RESTART_MS) {
    Serial.println("[WiFi] 5 min sin red — reiniciando");
    ESP.restart();
  }
  // Intento silencioso cada 10s
  if (ahora - _t_wifi_intento >= WIFI_RETRY_MS) {
    WiFi.reconnect();
    _t_wifi_intento = ahora;
  }
  return false;
}

// ─────────────────────────────────────────────────────────────────
// MQTT — reconexión no bloqueante
// ─────────────────────────────────────────────────────────────────
WiFiClient   _wifiClient;
PubSubClient mqtt(_wifiClient);

unsigned long _t_mqtt_intento = 0;
#define MQTT_RETRY_MS 5000

void publicar(const char* topic, const char* payload) {
  if (!mqtt.connected()) return;
  mqtt.publish(topic, payload);
  Serial.printf("[MQTT] %s → %s\n", topic, payload);
}

// Devuelve true si MQTT está listo para usar
bool verificar_mqtt(unsigned long ahora) {
  if (mqtt.connected()) return true;
  if (ahora - _t_mqtt_intento >= MQTT_RETRY_MS) {
    Serial.printf("[MQTT] Reconectando a %s...", MQTT_BROKER);
    if (mqtt.connect(DEVICE_ID)) {
      Serial.println(" OK");
    } else {
      Serial.printf(" fallo rc=%d\n", mqtt.state());
    }
    _t_mqtt_intento = ahora;
  }
  return false;
}

// ─────────────────────────────────────────────────────────────────
// Setup
// ─────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  pinMode(PIN_PIR,       INPUT);
  pinMode(PIN_VIBRACION, INPUT);
  pinMode(PIN_TRIG,      OUTPUT);
  pinMode(PIN_ECHO,      INPUT_PULLDOWN);

  // ISR para el ECHO del sonar
  attachInterrupt(digitalPinToInterrupt(PIN_ECHO), isr_echo, CHANGE);

  conectar_wifi_inicial();

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setKeepAlive(30);

  Serial.println("[OK] Sensores listos.");
}

// ─────────────────────────────────────────────────────────────────
// Loop — completamente no bloqueante
// ─────────────────────────────────────────────────────────────────
void loop() {
  unsigned long ahora = millis();

  // Sin WiFi → esperar sin procesar
  if (!verificar_wifi(ahora)) return;

  // Sin MQTT → intentar reconectar, seguir procesando sensores
  bool mqtt_ok = verificar_mqtt(ahora);
  if (mqtt_ok) mqtt.loop();

  // ── PIR y Vibración — flanco de subida con cooldown ──────────
  for (int i = 0; i < N_SENSORES; i++) {
    Sensor& s     = sensores[i];
    bool activo   = digitalRead(s.pin) == HIGH;
    bool flanco   = activo && !s.estado_anterior;
    bool listo    = (ahora - s.ultimo_envio) >= s.cooldown_ms;

    if (flanco && listo && mqtt_ok) {
      publicar(s.topic, "1");
      s.ultimo_envio = ahora;
    }
    s.estado_anterior = activo;
  }

  // ── HC-SR04 — máquina de estados no bloqueante ────────────────
  if (mqtt_ok) {
    actualizar_sonar(ahora);
  }
}
