# ─────────────────────────────────────────────────────────────────────────────
#  Adriana Loop Dashboard – MVP
#  Full Streamlit app.py
# ─────────────────────────────────────────────────────────────────────────────
import streamlit as st
import requests, pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time, timezone

# ---------- Streamlit page config ----------
st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

# ---------- Nightscout credentials ----------
NS_URL    = st.secrets["NIGHTSCOUT_URL"]
NS_SECRET = st.secrets["API_SECRET"]

# ---------- Date / time selectors (default = today) ----------
today_utc = datetime.now(timezone.utc).date()
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date",  value=today_utc)
    start_time = st.time_input("Start time",  value=time(0, 0))
with col2:
    end_date   = st.date_input("End date",    value=today_utc)
    end_time   = st.time_input("End time",    value=time(23, 59))

start_dt = datetime.combine(start_date, start_time, tzinfo=timezone.utc)
end_dt   = datetime.combine(end_date,   end_time,   tzinfo=timezone.utc)

# ---------- Data fetch (cached) ----------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_nightscout():
    headers = {"API-SECRET": NS_SECRET}
    # fetch last ~3 days (enough for most windows) to keep payload small
    entries = requests.get(
        f"{NS_URL}/api/v1/entries.json?count=8640",  # 5-min samples × 3 days
        headers=headers).json()

    treatments = requests.get(
        f"{NS_URL}/api/v1/treatments.json?count=2000",
        headers=headers).json()

    return pd.DataFrame(entries), pd.DataFrame(treatments)

st.write("Fetching data from Nightscout…")
entries_df, treatments_df = fetch_nightscout()
st.success("Data loaded.")

# ---------- Pre-process ----------
entries_df['time'] = pd.to_datetime(entries_df['dateString'], utc=True)
entries_df['mmol'] = (entries_df['sgv'] / 18).round(1)          # 1 decimal

treatments_df['time'] = pd.to_datetime(treatments_df['created_at'], utc=True)
bolus_df    = treatments_df[treatments_df['insulin'].notnull()]
smb_df      = bolus_df[ bolus_df['enteredBy'].str.contains('SMB', na=False)]
manual_df   = bolus_df[~bolus_df['enteredBy'].str.contains('SMB', na=False)]

# ---------- Window filter ----------
mask = (entries_df['time'] >= start_dt) & (entries_df['time'] <= end_dt)
entries_df  = entries_df.loc[mask]
manual_df   = manual_df.loc[(manual_df['time'] >= start_dt) & (manual_df['time'] <= end_dt)]
smb_df      = smb_df.loc[(smb_df['time']    >= start_dt) & (smb_df['time']    <= end_dt)]

# ---------- Plot ----------
fig = go.Figure()

# BG line
fig.add_trace(go.Scatter(
    x=entries_df['time'], y=entries_df['mmol'],
    mode='lines+markers', name='BG (mmol/L)',
    line=dict(color='green'),
    hovertemplate='%{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# Manual bolus bars
fig.add_trace(go.Bar(
    x=manual_df['time'], y=manual_df['insulin'],
    name='Manual Bolus (U)', yaxis='y2',
    marker_color='rgba(0,102,204,0.6)',
    hovertemplate='%{y} U<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# SMB bars
fig.add_trace(go.Bar(
    x=smb_df['time'], y=smb_df['insulin'],
    name='SMB (U)', yaxis='y2',
    marker_color='rgba(255,99,132,0.6)',
    hovertemplate='%{y} U (SMB)<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

fig.update_layout(
    title='BG + Insulin Timeline',
    xaxis=dict(title='Time'),
    yaxis =dict(title='BG (mmol/L)', range=[2, 15]),
    yaxis2=dict(
        title='Insulin (U)', overlaying='y', side='right',
        showgrid=False),
    bargap=0.15, height=550,
    legend=dict(orientation='h')
)

st.plotly_chart(fig, use_container_width=True)
# ─────────────────────────────────────────────────────────────────────────────
