"""
SecureVision — Dashboard de Monitoreo
Streamlit · multi-tenant por URL (?cliente=UUID)
"""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import time

# ── Configuración de página ───────────────────────────────────────
st.set_page_config(
    page_title="SecureVision",
    page_icon="🔒",
    layout="wide",
)

API_URL = "https://securevision-cloud-production.up.railway.app"

NIVEL_COLOR = {
    "CRITICO": "🔴",
    "ALTO":    "🟠",
    "MEDIO":   "🟡",
    "BAJO":    "🟢",
}
ZONA_COLOR = {
    "CRITICO":    "#dc2626",
    "PRECAUCION": "#f97316",
    "SEGURO":     "#16a34a",
}

# ── Cliente desde URL ─────────────────────────────────────────────
cliente_id = st.query_params.get("cliente", "00f0374a-ff6f-4117-8da4-9c3fd6df2cac")

# ── Fetch de datos ────────────────────────────────────────────────
@st.cache_data(ttl=5)
def get_stats(cid):
    try:
        r = requests.get(f"{API_URL}/stats/{cid}", timeout=6)
        return r.json() if r.ok else {}
    except Exception:
        return {}

@st.cache_data(ttl=5)
def get_eventos(cid, limit=20):
    try:
        r = requests.get(f"{API_URL}/eventos/{cid}?limit={limit}", timeout=6)
        return r.json() if r.ok else []
    except Exception:
        return []

stats   = get_stats(cliente_id)
eventos = get_eventos(cliente_id)

online = len(eventos) > 0 and (
    datetime.now(timezone.utc) -
    datetime.fromisoformat(eventos[0]["created_at"].replace("Z", "+00:00"))
).total_seconds() < 3600   # online si hay evento en la última hora

# ── Header ────────────────────────────────────────────────────────
col_title, col_status = st.columns([4, 1])
with col_title:
    st.title("🔒 SecureVision — Dashboard")
    st.caption(f"Cliente: `{cliente_id}`")
with col_status:
    st.metric(
        label="Estado del sistema",
        value="🟢 En línea" if online else "🔴 Sin actividad",
    )

st.divider()

# ── KPI cards ─────────────────────────────────────────────────────
k1, k2, k3, k4 = st.columns(4)

total    = stats.get("total", 0)
criticos = stats.get("por_nivel", {}).get("CRITICO", 0)
ultimo   = eventos[0]["created_at"][:19].replace("T", " ") if eventos else "—"
tipos    = stats.get("por_tipo", {})

k1.metric("Total eventos",  total)
k2.metric("🔴 Críticos",    criticos)
k3.metric("⏱️ Último evento", ultimo)
k4.metric("📡 Combinados",  tipos.get("combinado", 0))

st.divider()

# ── Gráfico + Tabla ───────────────────────────────────────────────
col_chart, col_table = st.columns([1, 2])

with col_chart:
    st.subheader("Alertas por zona")
    por_zona = stats.get("por_zona", {})
    if por_zona:
        df_zona = pd.DataFrame(
            {"Zona": list(por_zona.keys()), "Alertas": list(por_zona.values())}
        ).set_index("Zona")
        st.bar_chart(df_zona, color="#ef4444")
    else:
        st.info("Sin datos aún")

    st.subheader("Por nivel")
    por_nivel = stats.get("por_nivel", {})
    if por_nivel:
        df_nivel = pd.DataFrame(
            {"Nivel": list(por_nivel.keys()), "Total": list(por_nivel.values())}
        ).set_index("Nivel")
        st.bar_chart(df_nivel, color="#f97316")
    else:
        st.info("Sin datos aún")

with col_table:
    st.subheader("Eventos recientes")
    if eventos:
        rows = []
        for e in eventos:
            ts = e["created_at"][:19].replace("T", " ")
            nivel = e.get("nivel") or "—"
            zona  = e.get("zona")  or "—"
            tipo  = e.get("tipo")  or "—"
            icono = NIVEL_COLOR.get(nivel, "⚪")
            rows.append({
                "Hora":    ts,
                "Nivel":   f"{icono} {nivel}",
                "Zona":    zona,
                "Tipo":    tipo,
                "Mensaje": (e.get("mensaje") or "")[:60],
            })
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            height=460,
        )
    else:
        st.info("Esperando eventos del sistema...")

# ── Auto-refresh cada 5 segundos ─────────────────────────────────
st.caption(f"Actualizado: {datetime.now().strftime('%H:%M:%S')} · refresca cada 5s")
time.sleep(5)
st.rerun()
