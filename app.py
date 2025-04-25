# ─────────────────────────────────────────────────────────────────────
#  Adriana Loop Dashboard – BG + bolus / SMB + carbs + temp-basal
# ─────────────────────────────────────────────────────────────────────
import streamlit as st, requests, pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time, timezone

# ── Page + Nightscout creds ──────────────────────────────────────────
st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL    = st.secrets["NIGHTSCOUT_URL"]
NS_SECRET = st.secrets["API_SECRET"]

# ── Date-time pickers ────────────────────────────────────────────────
today = datetime.now(timezone.utc).date()
c1, c2 = st.columns(2)
with c1:
    start_date = st.date_input("Start date", value=today)
    start_time = st.time_input("Start time", value=time(0, 0))
with c2:
    end_date   = st.date_input("End date",   value=today)
    end_time   = st.time_input("End time",   value=time(23, 59))

start_dt = datetime.combine(start_date, start_time, tzinfo=timezone.utc)
end_dt   = datetime.combine(end_date,   end_time,   tzinfo=timezone.utc)

# ── Nightscout fetch (cached 10 min) ─────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns():
    hdr = {"API-SECRET": NS_SECRET}
    entries = requests.get(f"{NS_URL}/api/v1/entries.json?count=8640",
                           headers=hdr, timeout=10).json()
    treatments = requests.get(f"{NS_URL}/api/v1/treatments.json?count=2000",
                              headers=hdr, timeout=10).json()
    return pd.DataFrame(entries), pd.DataFrame(treatments)

st.write("Fetching data from Nightscout…")
entries_df, t_df = fetch_ns()
st.success("Data loaded")

# ── Pre-processing ──────────────────────────────────────────────────
entries_df['time'] = pd.to_datetime(entries_df['dateString'], utc=True)
entries_df['mmol'] = (entries_df['sgv'] / 18).round(1)

t_df['time'] = pd.to_datetime(t_df['created_at'], utc=True)

#  SMB vs Manual bolus
t_df['isSMB'] = False
if 'isSMB' in t_df.columns:
    t_df.loc[t_df['isSMB'] == True, 'isSMB'] = True
else:
    t_df.loc[t_df['enteredBy'].str.contains('SMB', na=False), 'isSMB'] = True

bolus_df   = t_df[t_df['insulin'].notnull()]
smb_df     = bolus_df[ bolus_df['isSMB']]
manual_df  = bolus_df[~bolus_df['isSMB']]

carb_df   = t_df[t_df['carbs'].fillna(0) > 0]             # carbs
basal_df  = t_df[t_df['eventType'] == 'Temp Basal']       # temp-basal
if 'rate' in basal_df.columns:
    basal_df['rate'] = basal_df['rate'].astype(float)

# ── Window filter helper ────────────────────────────────────────────
def wnd(df): return df[(df['time'] >= start_dt) & (df['time'] <= end_dt)]

entries_df = wnd(entries_df)
manual_df  = wnd(manual_df)
smb_df     = wnd(smb_df)
carb_df    = wnd(carb_df)
basal_df   = wnd(basal_df)

# ── Plotly figure ───────────────────────────────────────────────────
fig = go.Figure()

# BG
fig.add_trace(go.Scatter(
    x=entries_df['time'], y=entries_df['mmol'],
    mode='lines+markers', name='BG (mmol/L)', line=dict(color='green'),
    hovertemplate='%{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# Manual bolus
fig.add_trace(go.Bar(
    x=manual_df['time'], y=manual_df['insulin'],
    name='Manual Bolus (U)', yaxis='y2',
    marker_color='rgba(0,102,204,0.6)',
    hovertemplate='%{y} U<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# SMB
fig.add_trace(go.Bar(
    x=smb_df['time'], y=smb_df['insulin'],
    name='SMB (U)', yaxis='y2',
    marker_color='rgba(255,99,132,0.6)',
    hovertemplate='%{y} U (SMB)<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# Carbs
fig.add_trace(go.Bar(
    x=carb_df['time'], y=carb_df['carbs'],
    name='Carbs (g)', yaxis='y3',
    marker_color='rgba(255,165,0,0.55)',
    hovertemplate='%{y} g carbs<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# Temp-basal
if not basal_df.empty:
    fig.add_trace(go.Scatter(
        x=basal_df['time'], y=basal_df['rate'],
        name='Temp Basal (U/h)', yaxis='y4',
        mode='lines', line_shape='hv', line=dict(color='purple'),
        hovertemplate='%{y} U/h<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
    ))

# ── Layout with 4 y-axes (anchor='free' fixes the error) ─────────────
fig.update_layout(
    title='BG + Insulin + Carbs + Basal',
    xaxis=dict(title='Time'),
    yaxis=dict(title='BG (mmol/L)', range=[2, 15]),
    yaxis2=dict(title='Bolus U',
                overlaying='y', side='right', showgrid=False),
    yaxis3=dict(title='Carbs g',
                overlaying='y', side='left',
                anchor='free', position=0.05,
                showgrid=False,
                tickfont=dict(color='darkorange'),
                titlefont=dict(color='darkorange')),
    yaxis4=dict(title='Basal U/h',
                overlaying='y', side='right',
                anchor='free', position=0.95,
                showgrid=False,
                tickfont=dict(color='purple'),
                titlefont=dict(color='purple')),
    bargap=0.15, height=600,
    legend=dict(orientation='h')
)

st.plotly_chart(fig, use_container_width=True)
# ─────────────────────────────────────────────────────────────────────
