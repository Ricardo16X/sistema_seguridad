"""
SecureVision — Módulo de notificaciones Telegram

Diseño síncrono: pensado para ser llamado desde el hilo de Comunicaciones,
que ya es independiente del loop de video, por lo que bloquear aquí es seguro.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

def _configurado():
    if not _BOT_TOKEN or not _CHAT_ID:
        print("[NOTIFIER] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False
    return True

def enviar_texto(mensaje, silencioso=False):
    """Envía un mensaje de texto a Telegram."""
    if not _configurado():
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":              _CHAT_ID,
                "text":                 mensaje,
                "parse_mode":           "HTML",
                "disable_notification": silencioso,
            },
            timeout=10,
        )
        if not r.ok:
            print(f"[NOTIFIER] Error texto: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"[NOTIFIER] Error enviando texto: {e}")

def enviar_foto(foto_path, caption="", silencioso=False):
    """Envía una foto a Telegram. Si no existe, envía solo el caption como texto."""
    if not _configurado():
        return
    if not foto_path or not os.path.exists(foto_path):
        if caption:
            enviar_texto(caption, silencioso=silencioso)
        return
    try:
        with open(foto_path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{_BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id":              _CHAT_ID,
                    "caption":              caption,
                    "parse_mode":           "HTML",
                    "disable_notification": str(silencioso).lower(),
                },
                files={"photo": f},
                timeout=15,
            )
        if not r.ok:
            print(f"[NOTIFIER] Error foto: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"[NOTIFIER] Error enviando foto: {e}")
