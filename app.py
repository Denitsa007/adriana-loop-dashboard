import streamlit as st
import requests, json, pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL    = st.secrets["NIGHTSCOUT_URL"]
NS_SECRET = st.secrets["API_SECRET"]

# ── Date-picker (defaults = today) ───────────────────────────────────────────
today = datetime.utcnow().date()
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date", value=today)
    start_time = st.time_input("Start time", value=time(0, 0))
with col2:
    end_date   = st.date_input("End date",   value=today)
    end_time   = st.time_input("End time",   value=time(23, 59))

start_dt = datetime.combine(start_date, start_time).astimezone().astimezone('UTC')
end_dt   = datetime.combine(end_date,   end_time).astimezone().astimezone('UTC')

# ── Cached fetch (single HTTP request) ───────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def fetch_window(start_ms: int, end_ms: int):
    """
    Fetch only data inside [start_ms, end_ms] (Unix ms UTC) using the Nightscout
    /api/v1/batch endpoint → one round-trip.
    """
    q = (
        f"{NS_URL}/api/v1/batch?"
        f"urls=/entries.json?find%5Bdate%5D%5B%24gte%5D={start_ms}"
        f"%26find%5Bdate%5D%5B%24lte%5D={end_ms}"
        f"&urls=/treatments.json?find%5Bcreated_at%5D%5B%24gte%5D={start_ms}"
        f"%26find%5Bcreated_at%5D%5B%24lte%5D={end_ms}"
    )
    headers = {"API-SECRET": NS_SECRET}
    raw = requests.get(q, headers=headers, timeout=10).json()

    entries_df = pd.DataFrame(raw[0])
    treat_df   = pd.DataFrame(raw[1])

    if not entries_df.empty:
        entries_df['time'] = pd.to_datetime(entries_df['dateString'], utc=True)
        entries_df['mmol'] = entries_df['sgv'] / 18.0
    if not treat_df.empty:
        treat_df['time'] = pd.to_datetime(treat_df['created_at'], utc=True)

    return entries_df, treat_df

start_ms = int(start_dt.timestamp() * 1000)
end_ms   = int(end_dt.timestamp()   * 1000)

with st.spinner("Fetching Nightscout data…"):
    entries_df, treatments_df = fetch_window(start_ms, end_ms)
st.success(f"Loaded {len(entries_df)} BG points & {len(treatments_df)} treatments.")

# ── Split bolus vs SMB ───────────────────────────────────────────────────────
bolus_df = treatments_df[treatments_df['insulin'].notnull()]
smb_df   = bolus_df[bolus_df['enteredBy'].str.contains('SMB', na=False)]
manual_df= bolus_df[~bolus_df['enteredBy'].str.contains('SMB', na=False)]

# ── Plot ─────────────────────────────────────────────────────────────────────
fig = go.Figure()

fig.add_trace(go.Scatter(
    x=entries_df['time'],
    y=entries_df['mmol'],
    mode='lines+markers',
    name='BG (mmol/L)',
    line=dict(color='green'),
    hovertemplate='BG: %{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

fig.add_trace(go.Bar(
    x=manual_df['time'], y=manual_df['insulin'],
    name='Bolus (U)', yaxis='y2',
    marker_color='rgba(0,102,204,.5)',
    hovertemplate='Bolus: %{y:.2f} U<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))
fig.add_trace(go.Bar(
    x=smb_df['time'], y=smb_df['insulin'],
    name='SMB (U)', yaxis='y2',
    marker_color='rgba(255,99,132,.5)',
    hovertemplate='SMB: %{y:.2f} U<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

fig.update_layout(
    xaxis_title='Time (UTC)',
    yaxis=dict(title='BG (mmol/L)', range=[2, 15]),
    yaxis2=dict(title='Insulin (U)', overlaying='y', side='right'),
    height=500, bargap=0.15, legend_orientation='h', hovermode='x unified'
)

st.plotly_chart(fig, use_container_width=True)
