#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>

// ── Configuración ─────────────────────────────────────────────────
#define AP_NAME       "SecureVision-Sensores"
#define AP_PASSWORD   "securevision123"
#define MQTT_BROKER   "192.168.0.32"   // IP de la laptop (Fog)
#define MQTT_PORT     1883
#define DEVICE_ID     "esp32-sensores"

// ── Pines ─────────────────────────────────────────────────────────
#define PIN_PIR       13   // PIR HC-SR501  — salida digital
#define PIN_VIBRACION 14   // SW-420        — salida digital
#define PIN_TRIG      26   // HC-SR04       — disparo ultrasónico
#define PIN_ECHO      25   // HC-SR04       — eco (5V → 3.3V con divisor)

// ── Proximidad ────────────────────────────────────────────────────
#define DISTANCIA_UMBRAL_CM  100   // alerta si objeto a menos de esta distancia

// ── Topics MQTT ───────────────────────────────────────────────────
#define TOPIC_PIR         "securevision/pir"
#define TOPIC_VIBRACION   "securevision/vibracion"
#define TOPIC_PROXIMIDAD  "securevision/proximidad"

// ── Estado interno ────────────────────────────────────────────────
struct Sensor {
  uint8_t     pin;
  const char* topic;
  unsigned long cooldown_ms;
  unsigned long ultimo_envio;
  bool          estado_anterior;
};

Sensor sensores[] = {
  { PIN_PIR,       TOPIC_PIR,       8000, 0, false },
  { PIN_VIBRACION, TOPIC_VIBRACION, 3000, 0, false },
};
const int N_SENSORES = sizeof(sensores) / sizeof(sensores[0]);

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ── Proximidad (HC-SR04) ──────────────────────────────────────────
unsigned long _ultimo_proximidad = 0;
#define COOLDOWN_PROXIMIDAD 5000  // ms entre alertas de proximidad

float medir_distancia_cm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  long duracion = pulseIn(PIN_ECHO, HIGH, 30000);  // timeout 30ms (~5m)
  if (duracion == 0) return -1;                     // sin eco — fuera de rango
  return duracion * 0.034f / 2.0f;
}

// ── WiFi ──────────────────────────────────────────────────────────
void conectar_wifi() {
  WiFiManager wm;
  wm.setAPCallback([](WiFiManager *wm) {
    Serial.println("[WiFi] Modo AP activo — conéctate a: " AP_NAME);
    Serial.println("[WiFi] Abre: 192.168.4.1");
  });
  if (!wm.autoConnect(AP_NAME, AP_PASSWORD)) {
    Serial.println("[ERROR] No se pudo conectar — reiniciando");
    delay(3000);
    ESP.restart();
  }
  Serial.printf("[WiFi] Conectado — IP: %s\n", WiFi.localIP().toString().c_str());
}

// ── MQTT ──────────────────────────────────────────────────────────
void conectar_mqtt() {
  while (!mqtt.connected()) {
    Serial.printf("[MQTT] Conectando a %s...", MQTT_BROKER);
    if (mqtt.connect(DEVICE_ID)) {
      Serial.println(" OK");
    } else {
      Serial.printf(" fallo rc=%d — reintento en 3s\n", mqtt.state());
      delay(3000);
    }
  }
}

void publicar(const char* topic, const char* payload) {
  mqtt.publish(topic, payload);
  Serial.printf("[MQTT] %s → %s\n", topic, payload);
}

// ── Setup ─────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  pinMode(PIN_PIR,       INPUT);
  pinMode(PIN_VIBRACION, INPUT);
  pinMode(PIN_TRIG,      OUTPUT);
  pinMode(PIN_ECHO,      INPUT_PULLDOWN);

  conectar_wifi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
}

// ── Loop ──────────────────────────────────────────────────────────
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Conexión perdida — reconectando");
    WiFi.reconnect();
    unsigned long t = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t < 10000) delay(500);
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[WiFi] Sin red — reiniciando");
      ESP.restart();
    }
  }
  if (!mqtt.connected()) conectar_mqtt();
  mqtt.loop();

  unsigned long ahora = millis();

  // ── PIR y vibración (flanco digital) ──────────────────────────
  for (int i = 0; i < N_SENSORES; i++) {
    Sensor& s      = sensores[i];
    bool    activo = digitalRead(s.pin) == HIGH;
    bool    flanco = activo && !s.estado_anterior;
    bool    listo  = (ahora - s.ultimo_envio) >= s.cooldown_ms;

    if (flanco && listo) {
      publicar(s.topic, "1");
      s.ultimo_envio = ahora;
    }
    s.estado_anterior = activo;
  }

  // ── Proximidad (HC-SR04) ───────────────────────────────────────
  if (ahora - _ultimo_proximidad >= COOLDOWN_PROXIMIDAD) {
    float dist = medir_distancia_cm();
    if (dist > 0 && dist < DISTANCIA_UMBRAL_CM) {
      Serial.printf("[PROX] %.1f cm — umbral %d cm\n", dist, DISTANCIA_UMBRAL_CM);
      char payload[10];
      snprintf(payload, sizeof(payload), "%.1f", dist);
      publicar(TOPIC_PROXIMIDAD, payload);
      _ultimo_proximidad = ahora;
    }
  }

  delay(50);  // muestrea a 20 Hz
}
