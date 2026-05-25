"""
SecureVision — Dashboard de Monitoreo
Streamlit · multi-tenant por URL (?cliente=UUID)
"""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import time
from PIL import Image, ImageDraw, ImageFont
import io

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

def preview_zonas(y_seg, y_prec, w=320, h=240):
    """Genera imagen de preview de zonas escalada desde 640x480."""
    scale = h / 480
    y1 = int(y_seg  * scale)
    y2 = int(y_prec * scale)
    y1 = max(1, min(y1, h - 2))
    y2 = max(y1 + 1, min(y2, h - 1))

    img  = Image.new("RGB", (w, h), (25, 25, 25))
    draw = ImageDraw.Draw(img, "RGBA")

    draw.rectangle([0, 0,  w, y1], fill=(34,  140,  34, 80))
    draw.rectangle([0, y1, w, y2], fill=(255, 140,   0, 80))
    draw.rectangle([0, y2, w, h],  fill=(200,  30,  30, 80))

    draw.line([(0, y1), (w, y1)], fill=(34,  180,  34), width=2)
    draw.line([(0, y2), (w, y2)], fill=(200,  30,  30), width=2)

    font = ImageFont.load_default()
    labels = [
        ("SEGURO",     y1 // 2,           (100, 220, 100)),
        ("PRECAUCION", y1 + (y2 - y1)//2, (255, 180,  80)),
        ("CRITICO",    y2 + (h  - y2)//2, (255,  80,  80)),
    ]
    for texto, cy, color in labels:
        bbox = draw.textbbox((0, 0), texto, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((w // 2 - tw // 2, cy - 6), texto, fill=color, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def enviar_comando(tipo, payload):
    try:
        r = requests.post(f"{API_URL}/comando", json={
            "cliente_id": cliente_id,
            "tipo":       tipo,
            "payload":    payload,
        }, timeout=6)
        return r.ok
    except Exception:
        return False

stats   = get_stats(cliente_id)
eventos = get_eventos(cliente_id)

online = len(eventos) > 0 and (
    datetime.now(timezone.utc) -
    datetime.fromisoformat(eventos[0]["created_at"].replace("Z", "+00:00"))
).total_seconds() < 3600

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

# ── Tabs ──────────────────────────────────────────────────────────
tab_monitor, tab_config = st.tabs(["📊 Monitoreo", "⚙️ Configuración"])

# ── TAB MONITOREO ─────────────────────────────────────────────────
with tab_monitor:
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
                ts    = e["created_at"][:19].replace("T", " ")
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

# ── TAB CONFIGURACIÓN ─────────────────────────────────────────────
with tab_config:
    st.subheader("🎛️ Control de cámara")
    st.caption("Los cambios se aplican al sistema en ~3 segundos.")

    col_brillo, col_zonas = st.columns(2)

    with col_brillo:
        st.markdown("**Brillo / Exposición**")
        ae = st.slider(
            "Nivel de exposición",
            min_value=-2, max_value=2, value=0, step=1,
            help="-2 = más oscuro · 0 = automático · +2 = más brillante",
        )
        if st.button("Aplicar brillo", use_container_width=True, type="primary"):
            if enviar_comando("brillo", {"ae_level": ae}):
                st.success(f"Comando enviado — ae_level={ae}")
            else:
                st.error("Error al enviar comando")

    with col_zonas:
        st.markdown("**Límites de zonas** *(píxeles Y en frame 480px)*")
        y_seg = st.slider(
            "Límite SEGURO / PRECAUCION",
            min_value=50, max_value=350, value=155, step=5,
            help="Borde inferior de la zona SEGURO",
        )
        y_prec = st.slider(
            "Límite PRECAUCION / CRITICO",
            min_value=100, max_value=440, value=320, step=5,
            help="Borde inferior de la zona PRECAUCION",
        )
        st.image(preview_zonas(y_seg, y_prec), caption="Vista previa — se actualiza al mover los sliders", use_container_width=True)
        if y_prec <= y_seg + 50:
            st.warning("CRITICO debe tener al menos 50px de altura")
        else:
            if st.button("Aplicar zonas", use_container_width=True, type="primary"):
                if enviar_comando("zonas", {"y_seguro": y_seg, "y_precaucion": y_prec}):
                    st.success(f"Zonas actualizadas — SEGURO 0-{y_seg} | PRECAUCION {y_seg}-{y_prec} | CRITICO {y_prec}-480")
                else:
                    st.error("Error al enviar comando")

    st.divider()
    st.caption("Los límites se aplican en tiempo real sin reiniciar el sistema.")

# ── Auto-refresh cada 5 segundos ─────────────────────────────────
st.caption(f"Actualizado: {datetime.now().strftime('%H:%M:%S')} · refresca cada 5s")
time.sleep(5)
st.rerun()
