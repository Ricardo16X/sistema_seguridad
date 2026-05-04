import os
import requests
from dotenv import load_dotenv

load_dotenv()

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

def _validar():
    if not _BOT_TOKEN or not _CHAT_ID:
        print("[NOTIFIER] ERROR: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False
    return True

def enviar_texto(mensaje):
    if not _validar():
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": _CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }, timeout=5)
        if not r.ok:
            print(f"[NOTIFIER] Telegram error: {r.text}")
    except Exception as e:
        print(f"[NOTIFIER] Error enviando texto: {e}")

def enviar_foto(foto_path, caption=""):
    if not _validar():
        return
    if not os.path.exists(foto_path):
        print(f"[NOTIFIER] Foto no encontrada: {foto_path}")
        enviar_texto(caption)
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendPhoto"
    try:
        with open(foto_path, "rb") as f:
            r = requests.post(url, data={
                "chat_id": _CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML"
            }, files={"photo": f}, timeout=10)
        if not r.ok:
            print(f"[NOTIFIER] Telegram error foto: {r.text}")
    except Exception as e:
        print(f"[NOTIFIER] Error enviando foto: {e}")
