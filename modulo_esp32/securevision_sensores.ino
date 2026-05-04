#include <WiFi.h>
#include <PubSubClient.h>

// ── Configuración ─────────────────────────────────────────────────
#define WIFI_SSID     "TU_RED_WIFI"
#define WIFI_PASSWORD "TU_PASSWORD"
#define MQTT_BROKER   "192.168.0.32"   // IP de la laptop (Fog)
#define MQTT_PORT     1883
#define DEVICE_ID     "esp32-sensores"

// ── Pines ─────────────────────────────────────────────────────────
#define PIN_PIR       13   // PIR HC-SR501  — salida digital
#define PIN_VIBRACION 14   // SW-420        — salida digital
#define PIN_SONIDO    27   // KY-037 (DO)   — salida digital

// ── Topics MQTT ───────────────────────────────────────────────────
#define TOPIC_PIR       "securevision/pir"
#define TOPIC_VIBRACION "securevision/vibracion"
#define TOPIC_SONIDO    "securevision/sonido"

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
  { PIN_SONIDO,    TOPIC_SONIDO,    3000, 0, false },
};
const int N_SENSORES = sizeof(sensores) / sizeof(sensores[0]);

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ── WiFi ──────────────────────────────────────────────────────────
void conectar_wifi() {
  Serial.printf("[WiFi] Conectando a %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Conectado — IP: %s\n", WiFi.localIP().toString().c_str());
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
  pinMode(PIN_SONIDO,    INPUT);

  conectar_wifi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
}

// ── Loop ──────────────────────────────────────────────────────────
void loop() {
  if (WiFi.status() != WL_CONNECTED) conectar_wifi();
  if (!mqtt.connected())             conectar_mqtt();
  mqtt.loop();

  unsigned long ahora = millis();

  for (int i = 0; i < N_SENSORES; i++) {
    Sensor& s       = sensores[i];
    bool    activo  = digitalRead(s.pin) == HIGH;
    bool    flanco  = activo && !s.estado_anterior;
    bool    listo   = (ahora - s.ultimo_envio) >= s.cooldown_ms;

    if (flanco && listo) {
      publicar(s.topic, "1");
      s.ultimo_envio = ahora;
    }

    s.estado_anterior = activo;
  }

  delay(50);  // muestrea a 20 Hz
}
