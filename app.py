import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time

# ── Page setup ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL    = st.secrets["NIGHTSCOUT_URL"]
NS_SECRET = st.secrets["API_SECRET"]

# ── Fetch Nightscout data (cached) ───────────────────────────────────────────
@st.cache_data(ttl=600)
def fetch_nightscout_data():
    headers = {"API-SECRET": NS_SECRET}
    entries = requests.get(f"{NS_URL}/api/v1/entries.json?count=1000",   headers=headers).json()
    treatments = requests.get(f"{NS_URL}/api/v1/treatments.json?count=1000", headers=headers).json()
    devicestatus = requests.get(f"{NS_URL}/api/v1/devicestatus.json?count=10", headers=headers).json()
    return pd.DataFrame(entries), pd.DataFrame(treatments), pd.DataFrame(devicestatus)

st.write("Fetching data from Nightscout…")
entries_df, treatments_df, devicestatus_df = fetch_nightscout_data()
st.success("Data loaded.")

# ── Prepare BG entries ───────────────────────────────────────────────────────
entries_df['time'] = (
    pd.to_datetime(entries_df['dateString'], utc=True)   # tz-aware (UTC)
)
entries_df['mmol'] = entries_df['sgv'] / 18.0           # mg/dL → mmol/L
entries_df = entries_df.dropna(subset=['time'])

# ── Prepare treatments (boluses / SMBs) ──────────────────────────────────────
treatments_df['time'] = pd.to_datetime(treatments_df['created_at'], utc=True)
bolus_df = treatments_df[treatments_df['insulin'].notnull()]

# ── Date/time pickers (default = today) ──────────────────────────────────────
today = datetime.utcnow().date()
default_start = datetime.combine(today, time.min)
default_end   = datetime.combine(today, time.max)

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date", value=default_start.date())
    start_hour = st.time_input("Start time", value=time.min)
with col2:
    end_date   = st.date_input("End date",   value=default_end.date())
    end_hour   = st.time_input("End time",   value=time.max)

# Combine & localise to UTC
start_time = pd.Timestamp(datetime.combine(start_date, start_hour)).tz_localize('UTC')
end_time   = pd.Timestamp(datetime.combine(end_date,   end_hour)).tz_localize('UTC')

# ── Filter to selected window ────────────────────────────────────────────────
entries_window = entries_df[
    (entries_df['time'] >= start_time) & (entries_df['time'] <= end_time)
]
bolus_window = bolus_df[
    (bolus_df['time']  >= start_time) & (bolus_df['time']  <= end_time)
]

# ── Plot ─────────────────────────────────────────────────────────────────────
fig = go.Figure()

# BG trace
fig.add_trace(go.Scatter(
    x=entries_window['time'],
    y=entries_window['mmol'],
    mode='lines+markers',
    name='BG (mmol/L)',
    line=dict(color='green')
))

# Bolus bars
fig.add_trace(go.Bar(
    x=bolus_window['time'],
    y=bolus_window['insulin'],
    name='Bolus (U)',
    yaxis='y2',
    marker_color='rgba(0, 102, 204, 0.4)'
))

fig.update_layout(
    title='BG + Insulin Timeline',
    xaxis=dict(title='Time (UTC)'),
    yaxis=dict(title='BG (mmol/L)', range=[2, 15]),
    yaxis2=dict(
        title='Insulin (U)',
        overlaying='y',
        side='right',
        showgrid=False
    ),
    height=500,
    legend=dict(orientation='h'),
    bargap=0.15
)

st.plotly_chart(fig, use_container_width=True)
