"""
SecureVision — Cloud API
FastAPI + Supabase · desplegado en Railway
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any
from collections import Counter
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_API_KEY")

_SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

app = FastAPI(title="SecureVision Cloud API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Modelos ───────────────────────────────────────────────────────
class EventoIn(BaseModel):
    cliente_id: str
    tipo:       str                  # 'vision' | 'sensor' | 'combinado'
    zona:       Optional[str] = None # 'SEGURO' | 'PRECAUCION' | 'CRITICO'
    nivel:      Optional[str] = None # 'BAJO' | 'MEDIO' | 'ALTO' | 'CRITICO'
    mensaje:    Optional[str] = None
    foto_url:   Optional[str] = None


# ── Helpers Supabase ──────────────────────────────────────────────
def _sb_get(path: str, params: dict) -> list:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{path}",
        params=params,
        headers=_SB_HEADERS,
        timeout=8,
    )
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"Supabase: {r.text[:120]}")
    return r.json()

def _sb_post(path: str, payload: dict):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{path}",
        json=payload,
        headers={**_SB_HEADERS, "Prefer": "return=minimal"},
        timeout=8,
    )
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"Supabase: {r.text[:120]}")


# ── Endpoints ─────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/evento", status_code=201)
def crear_evento(ev: EventoIn):
    """Recibe un evento del fog layer y lo persiste en Supabase."""
    _sb_post("eventos", ev.model_dump(exclude_none=True))
    return {"ok": True}


@app.get("/eventos/{cliente_id}")
def listar_eventos(
    cliente_id: str,
    limit: int = Query(default=50, le=200),
    tipo:  Optional[str] = None,
    nivel: Optional[str] = None,
):
    """Devuelve los últimos N eventos de un cliente, con filtros opcionales."""
    params = {
        "cliente_id": f"eq.{cliente_id}",
        "order":      "created_at.desc",
        "limit":      limit,
    }
    if tipo:
        params["tipo"]  = f"eq.{tipo}"
    if nivel:
        params["nivel"] = f"eq.{nivel}"

    return _sb_get("eventos", params)


@app.get("/stats/{cliente_id}")
def estadisticas(cliente_id: str):
    """Totales agrupados por zona y nivel para el dashboard."""
    rows = _sb_get("eventos", {
        "cliente_id": f"eq.{cliente_id}",
        "select":     "tipo,zona,nivel",
        "limit":      1000,
    })
    return {
        "total":     len(rows),
        "por_tipo":  dict(Counter(r["tipo"]  for r in rows if r.get("tipo"))),
        "por_zona":  dict(Counter(r["zona"]  for r in rows if r.get("zona"))),
        "por_nivel": dict(Counter(r["nivel"] for r in rows if r.get("nivel"))),
    }


# ── Comandos remotos ──────────────────────────────────────────────
class ComandoIn(BaseModel):
    cliente_id: str
    tipo:       str          # 'brillo' | 'zonas'
    payload:    dict[str, Any]

@app.post("/comando", status_code=201)
def enviar_comando(cmd: ComandoIn):
    """Dashboard → fog layer: ajustar brillo o límites de zonas."""
    _sb_post("comandos", cmd.model_dump())
    return {"ok": True}

@app.get("/comandos/{cliente_id}/pendientes")
def obtener_pendientes(cliente_id: str):
    """Fog layer polling: devuelve comandos no ejecutados y los marca."""
    rows = _sb_get("comandos", {
        "cliente_id": f"eq.{cliente_id}",
        "ejecutado":  "eq.false",
        "order":      "created_at.asc",
        "limit":      10,
    })
    if rows:
        ids = ",".join(r["id"] for r in rows)
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/comandos",
            json={"ejecutado": True},
            params={"id": f"in.({ids})"},
            headers=_SB_HEADERS,
            timeout=5,
        )
    return rows
