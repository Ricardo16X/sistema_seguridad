#!/bin/bash
# SecureVision — Arranque completo del sistema

VERDE='\033[0;32m'
ROJO='\033[0;31m'
AMARILLO='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VISION_DIR="$SCRIPT_DIR/modulo_vision"
PYTHON="/mnt/proyectos/Detector YOLO/venv/bin/python3"

ok()   { echo -e "${VERDE}  ✓ $1${RESET}"; }
fail() { echo -e "${ROJO}  ✗ $1${RESET}"; exit 1; }
info() { echo -e "${AMARILLO}  → $1${RESET}"; }

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║        SecureVision  v1.0            ║"
echo "  ║   Sistema de Vigilancia Inteligente  ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${RESET}"

# ── 1. Mosquitto ──────────────────────────────────────────────────
echo -e "${CYAN}[1/3] MQTT Broker${RESET}"
if systemctl is-active --quiet mosquitto 2>/dev/null; then
    ok "Mosquitto ya está corriendo"
else
    info "Iniciando Mosquitto..."
    sudo systemctl start mosquitto 2>/dev/null || mosquitto -d -c /etc/mosquitto/mosquitto.conf 2>/dev/null
    sleep 1
    if systemctl is-active --quiet mosquitto 2>/dev/null || pgrep -x mosquitto > /dev/null; then
        ok "Mosquitto iniciado"
    else
        fail "No se pudo iniciar Mosquitto — verificá la instalación"
    fi
fi

# ── 2. ESP32-CAM ──────────────────────────────────────────────────
echo -e "\n${CYAN}[2/3] ESP32-CAM${RESET}"
CAM_IP=$(grep -o '"esp32cam_ip": *"[^"]*"' "$VISION_DIR/config.json" | grep -o '"[^"]*"$' | tr -d '"')
info "Verificando $CAM_IP..."
if curl -s --max-time 3 "http://$CAM_IP/status" > /dev/null 2>&1; then
    ok "ESP32-CAM responde en $CAM_IP"
else
    echo -e "${AMARILLO}  ⚠ ESP32-CAM no responde — el sistema intentará conectar igual${RESET}"
fi

# ── 3. Cloud API ──────────────────────────────────────────────────
echo -e "\n${CYAN}[3/3] Cloud API${RESET}"
if curl -s --max-time 4 "https://securevision-cloud-production.up.railway.app/health" | grep -q "ok" 2>/dev/null; then
    ok "FastAPI en Railway responde"
else
    echo -e "${AMARILLO}  ⚠ Cloud API no responde — eventos se guardarán solo en Supabase${RESET}"
fi

# ── Lanzar sistema ────────────────────────────────────────────────
echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${VERDE}  Dashboard: https://securevision.streamlit.app"
echo -e "  ?cliente=00f0374a-ff6f-4117-8da4-9c3fd6df2cac${RESET}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"

cd "$VISION_DIR"
exec "$PYTHON" -u sistema_vigilancia.py
