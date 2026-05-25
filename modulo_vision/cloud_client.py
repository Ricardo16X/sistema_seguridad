"""
SecureVision — Cliente Cloud

Postea eventos al Cloud API (FastAPI en Railway).
Si CLOUD_API_URL no está definido, cae back directo a Supabase REST.
Fire-and-forget: los fallos se loguean sin interrumpir el sistema.
"""
import requests
import os
from dotenv import load_dotenv

load_dotenv()

_API_URL     = os.getenv("CLOUD_API_URL", "").rstrip("/")   # Railway URL
_SB_URL      = os.getenv("SUPABASE_URL", "")
_SB_KEY      = os.getenv("SUPABASE_API_KEY", "")

def registrar_evento(cliente_id, tipo, zona=None, nivel=None, mensaje=None, foto_url=None):
    """
    Envía un evento al cloud. Intenta FastAPI primero; si no hay URL, va directo a Supabase.
    tipo  : 'vision' | 'sensor' | 'combinado'
    zona  : 'SEGURO' | 'PRECAUCION' | 'CRITICO'
    nivel : 'BAJO' | 'MEDIO' | 'ALTO' | 'CRITICO'
    """
    payload = {k: v for k, v in {
        "cliente_id": cliente_id,
        "tipo":       tipo,
        "zona":       zona,
        "nivel":      nivel,
        "mensaje":    mensaje,
        "foto_url":   foto_url,
    }.items() if v is not None}

    try:
        if _API_URL:
            r = requests.post(f"{_API_URL}/evento", json=payload, timeout=5)
        elif _SB_URL and _SB_KEY:
            r = requests.post(
                f"{_SB_URL}/rest/v1/eventos",
                json=payload,
                headers={
                    "apikey":        _SB_KEY,
                    "Authorization": f"Bearer {_SB_KEY}",
                    "Content-Type":  "application/json",
                    "Prefer":        "return=minimal",
                },
                timeout=5,
            )
        else:
            return

        if not r.ok:
            print(f"[CLOUD] Error {r.status_code}: {r.text[:80]}")
        else:
            print(f"[CLOUD] Evento registrado: {tipo} {zona or ''} {nivel or ''}")

    except Exception as e:
        print(f"[CLOUD] Sin conexión: {e}")
