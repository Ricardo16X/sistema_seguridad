#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiManager.h>
#include <ESPmDNS.h>
#include "esp_http_server.h"
#include "esp_task_wdt.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

#define WDT_TIMEOUT_S 30  // reinicia si el sistema se cuelga más de 30s

// ── Configuración ─────────────────────────────────────────────────
#define FLASH_PIN     4
#define SERVO_PIN     13  // SG90 — señal PWM

// ── Servo (LEDC canal 2 — canal 0 reservado por la cámara) ────────
#define SERVO_LEDC_CH   2
#define SERVO_FREQ      50
#define SERVO_RES       16
#define SERVO_US_MIN    1000   // 1ms → 0°
#define SERVO_US_MAX    2000   // 2ms → 180°
#define SERVO_PERIODO   20000  // 20ms (50Hz)

void initServo() {
  ledcAttachChannel(SERVO_PIN, SERVO_FREQ, SERVO_RES, SERVO_LEDC_CH);
  setServo(90);  // centrar al arrancar
  Serial.println("[SERVO] Inicializado en 90°");
}

void setServo(int angulo) {
  angulo = constrain(angulo, 0, 180);
  uint32_t us   = map(angulo, 0, 180, SERVO_US_MIN, SERVO_US_MAX);
  uint32_t duty = (uint32_t)((float)us / SERVO_PERIODO * 65535);
  ledcWrite(SERVO_PIN, duty);
}
#define DEVICE_NAME   "securevision"   // → securevision.local
#define AP_NAME       "SecureVision-Setup"
#define AP_PASSWORD   "securevision123"

// ── Pinout ESP32-CAM AI-Thinker ──────────────────────────────────
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ── LED debug ─────────────────────────────────────────────────────
void _blink(int veces, int ms_on, int ms_off) {
  for (int i = 0; i < veces; i++) {
    digitalWrite(FLASH_PIN, HIGH); delay(ms_on);
    digitalWrite(FLASH_PIN, LOW);  delay(ms_off);
  }
}

// ── Cámara ────────────────────────────────────────────────────────
void initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  // Alojar buffers DMA para UXGA desde el inicio usando PSRAM
  // Así el cambio a UXGA en /capture no desborda los buffers (FB-OVF)
  config.frame_size   = FRAMESIZE_UXGA;
  config.jpeg_quality = 10;
  config.fb_count     = 2;
  config.fb_location  = CAMERA_FB_IN_PSRAM;

  for (int intento = 1; intento <= 5; intento++) {
    if (esp_camera_init(&config) == ESP_OK) break;
    Serial.printf("[ERROR] Camera init falló (intento %d/5)\n", intento);
    _blink(3, 80, 80);
    delay(2000);
    if (intento == 5) {
      Serial.println("[ERROR] Cámara no responde — reiniciando");
      delay(500);
      ESP.restart();
    }
  }

  // Correr el stream en VGA — los buffers ya son suficientemente grandes
  sensor_t *s = esp_camera_sensor_get();
  s->set_framesize(s, FRAMESIZE_VGA);
  Serial.println("[OK] Cámara inicializada — buffers UXGA, stream VGA");
}

// Flag para coordinar stream y captura — evita race condition en buffers DMA
volatile bool g_capture_busy = false;

// ── Stream ────────────────────────────────────────────────────────
#define PART_BOUNDARY "123456789000000000000987654321"
static const char* STREAM_CONTENT_TYPE =
  "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* STREAM_PART =
  "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

esp_err_t streamHandler(httpd_req_t *req) {
  camera_fb_t *fb = NULL;
  esp_err_t    res = ESP_OK;
  char         part_buf[64];

  httpd_resp_set_type(req, STREAM_CONTENT_TYPE);

  while (true) {
    // Pausar stream mientras captureHandler cambia el frame size
    while (g_capture_busy) { vTaskDelay(pdMS_TO_TICKS(20)); }

    fb = esp_camera_fb_get();
    if (!fb) { res = ESP_FAIL; break; }

    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) {
      size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, fb->len);
      res = httpd_resp_send_chunk(req, part_buf, hlen);
    }
    if (res == ESP_OK)
      res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);

    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;
  }
  return res;
}

// ── Capture ───────────────────────────────────────────────────────
esp_err_t captureHandler(httpd_req_t *req) {
  g_capture_busy = true;  // pausar stream — evitar acceso concurrente a la cámara
  sensor_t *s = esp_camera_sensor_get();

  s->set_framesize(s, FRAMESIZE_UXGA);
  delay(500);

  // Descartar primer frame para limpiar el buffer
  camera_fb_t *fb_descarte = esp_camera_fb_get();
  if (fb_descarte) esp_camera_fb_return(fb_descarte);
  delay(100);

  // Solo captura — flash lo maneja Python
  camera_fb_t *fb = esp_camera_fb_get();
  s->set_framesize(s, FRAMESIZE_VGA);
  delay(200);  // dar tiempo al DMA para ajustarse de vuelta a VGA

  g_capture_busy = false;  // reanudar stream

  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition",
                     "inline; filename=evidencia.jpg");
  httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  Serial.println("[CAPTURE] Foto tomada en UXGA");
  return ESP_OK;
}

// ── Control ───────────────────────────────────────────────────────
esp_err_t controlHandler(httpd_req_t *req) {
  char buf[128];
  size_t buf_len = httpd_req_get_url_query_len(req) + 1;
  if (buf_len > sizeof(buf)) { httpd_resp_send_404(req); return ESP_FAIL; }

  httpd_req_get_url_query_str(req, buf, buf_len);
  char var[32], val[32];
  httpd_query_key_value(buf, "var", var, sizeof(var));
  httpd_query_key_value(buf, "val", val, sizeof(val));

  sensor_t *s   = esp_camera_sensor_get();
  int       ival = atoi(val);

  if      (!strcmp(var, "framesize"))   s->set_framesize(s, (framesize_t)ival);
  else if (!strcmp(var, "quality"))     s->set_quality(s, ival);
  else if (!strcmp(var, "brightness"))  s->set_brightness(s, ival);
  else if (!strcmp(var, "contrast"))    s->set_contrast(s, ival);
  else if (!strcmp(var, "saturation"))  s->set_saturation(s, ival);
  else if (!strcmp(var, "flash"))       digitalWrite(FLASH_PIN, ival ? HIGH : LOW);
  // ── Controles de exposición — usados por el bucle automático de Python
  else if (!strcmp(var, "ae_level"))    s->set_ae_level(s, ival);      // sesgo AEC (-2..+2)
  else if (!strcmp(var, "aec"))         s->set_exposure_ctrl(s, ival); // AEC on/off
  else if (!strcmp(var, "agc"))         s->set_gain_ctrl(s, ival);     // AGC on/off
  else if (!strcmp(var, "aec_value"))   s->set_aec_value(s, ival);     // exposición manual (0-1200)
  else if (!strcmp(var, "gainceiling")) s->set_gainceiling(s, (gainceiling_t)ival); // techo de ganancia
  else if (!strcmp(var, "servo"))       setServo(ival);
  else if (!strcmp(var, "reset")) {
    WiFiManager wm;
    wm.resetSettings();
    Serial.println("[WiFi] Credenciales borradas — reiniciando en modo AP");
    delay(500);
    ESP.restart();
  }
  else { httpd_resp_send_404(req); return ESP_FAIL; }

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_send(req, "OK", 2);
  Serial.printf("[CONTROL] %s = %s\n", var, val);
  return ESP_OK;
}

// ── Status ────────────────────────────────────────────────────────
esp_err_t statusHandler(httpd_req_t *req) {
  sensor_t *s = esp_camera_sensor_get();
  char json[300];
  snprintf(json, sizeof(json),
    "{\"framesize\":%d,\"quality\":%d,\"brightness\":%d,"
    "\"contrast\":%d,\"saturation\":%d,\"flash\":%d,"
    "\"ip\":\"%s\",\"mdns\":\"%s.local\"}",
    s->status.framesize, s->status.quality, s->status.brightness,
    s->status.contrast, s->status.saturation, digitalRead(FLASH_PIN),
    WiFi.localIP().toString().c_str(), DEVICE_NAME
  );
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_send(req, json, strlen(json));
  return ESP_OK;
}

// ── Servidor HTTP ─────────────────────────────────────────────────
void startServer() {
  httpd_handle_t stream_httpd = NULL;
  httpd_config_t stream_cfg   = HTTPD_DEFAULT_CONFIG();
  stream_cfg.server_port      = 81;
  stream_cfg.ctrl_port        = 32769;

  if (httpd_start(&stream_httpd, &stream_cfg) == ESP_OK) {
    httpd_uri_t uri = { "/stream", HTTP_GET, streamHandler, NULL };
    httpd_register_uri_handler(stream_httpd, &uri);
  }

  httpd_handle_t control_httpd = NULL;
  httpd_config_t cfg           = HTTPD_DEFAULT_CONFIG();
  cfg.server_port              = 80;

  if (httpd_start(&control_httpd, &cfg) == ESP_OK) {
    httpd_uri_t capture = { "/capture", HTTP_GET, captureHandler, NULL };
    httpd_uri_t control = { "/control", HTTP_GET, controlHandler, NULL };
    httpd_uri_t status  = { "/status",  HTTP_GET, statusHandler,  NULL };
    httpd_register_uri_handler(control_httpd, &capture);
    httpd_register_uri_handler(control_httpd, &control);
    httpd_register_uri_handler(control_httpd, &status);
  }
}

// ── Setup ─────────────────────────────────────────────────────────
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);  // deshabilitar detector de brownout
  delay(1000);  // esperar a que el riel de alimentación se estabilice

  Serial.begin(115200);
  pinMode(FLASH_PIN, OUTPUT);
  digitalWrite(FLASH_PIN, LOW);

  _blink(5, 80, 80);   // boot iniciado
  initCamera();
  initServo();

  WiFiManager wm;
  wm.setAPCallback([](WiFiManager *wm) {
    Serial.println("[WiFi] Modo AP activo — conéctate a: " AP_NAME);
    Serial.println("[WiFi] Abre: 192.168.4.1");
    // Parpadeo lento continuo mientras espera configuración WiFi
    while (WiFi.status() != WL_CONNECTED) {
      digitalWrite(FLASH_PIN, HIGH); delay(400);
      digitalWrite(FLASH_PIN, LOW);  delay(400);
    }
  });

  if (!wm.autoConnect(AP_NAME, AP_PASSWORD)) {
    Serial.println("[ERROR] No se pudo conectar — reiniciando");
    delay(3000);
    ESP.restart();
  }

  _blink(3, 150, 150);  // WiFi conectado

  Serial.printf("[WiFi] Conectado — IP: %s\n", WiFi.localIP().toString().c_str());

  if (MDNS.begin(DEVICE_NAME)) {
    MDNS.addService("http", "tcp", 80);
    MDNS.addService("http", "tcp", 81);
    Serial.printf("[mDNS] Disponible en: http://%s.local\n", DEVICE_NAME);
  }

  startServer();

  // Watchdog por hardware — reinicia el ESP32 si el loop se cuelga > 30s
  const esp_task_wdt_config_t wdt_cfg = {
    .timeout_ms    = WDT_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic  = true
  };
  esp_task_wdt_reconfigure(&wdt_cfg);  // Arduino 3.x ya inicializa el WDT
  esp_task_wdt_add(NULL);

  Serial.println("──────────────────────────────────────────");
  Serial.printf("Stream:   http://%s.local:81/stream\n",  DEVICE_NAME);
  Serial.printf("Capture:  http://%s.local/capture\n",    DEVICE_NAME);
  Serial.printf("Control:  http://%s.local/control?var=brightness&val=1\n", DEVICE_NAME);
  Serial.printf("Status:   http://%s.local/status\n",     DEVICE_NAME);
  Serial.printf("Reset:    http://%s.local/control?var=reset&val=1\n", DEVICE_NAME);
  Serial.println("──────────────────────────────────────────");
}

// ── Loop — watchdog WiFi + hardware WDT ──────────────────────────
void loop() {
  esp_task_wdt_reset();  // alimentar el watchdog de hardware

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Conexión perdida — reiniciando");
    delay(1000);
    ESP.restart();
  }

  // Heartbeat — 1 pulso corto cada 4s: sistema operativo
  _blink(1, 80, 0);
  delay(4000);
}
