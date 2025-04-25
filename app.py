# ─────────────────────────────────────────────────────────────────────
#  Adriana Loop Dashboard  |  BG + Bolus/SMB + Carbs + Temp-Basal
# ─────────────────────────────────────────────────────────────────────
import streamlit as st, requests, pandas as pd, time
from datetime import datetime, time as dtime, timezone
import plotly.graph_objects as go

# ── page / secrets ──────────────────────────────────────────────────
st.set_page_config(page_title="Adriana's Looping Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL, NS_SECRET = st.secrets["NIGHTSCOUT_URL"], st.secrets["API_SECRET"]
HEADERS, TIMEOUT, RETRY_PAUSE = {"API-SECRET": NS_SECRET}, 30, 3

# ── date-time pickers ───────────────────────────────────────────────
today = datetime.now(timezone.utc).date()
c1, c2 = st.columns(2)
with c1:
    sd = st.date_input("Start date", today)
    stime = st.time_input("Start time", dtime(0, 0))
with c2:
    ed = st.date_input("End date",   today)
    etime = st.time_input("End time", dtime(23, 59))

start_dt = datetime.combine(sd, stime, tzinfo=timezone.utc)
end_dt   = datetime.combine(ed, etime, tzinfo=timezone.utc)

# ── Nightscout fetch (cached 10 min) ────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns():
    def _get(url):
        return requests.get(url, headers=HEADERS, timeout=TIMEOUT).json()

    qs_entries = "api/v1/entries.json?count=8640"      # 3 days @5-min CGM
    qs_treat   = "api/v1/treatments.json?count=2000"   # ≈3 days treatments

    for attempt in (1, 2):
        try:
            return (
                pd.DataFrame(_get(f"{NS_URL}/{qs_entries}")),
                pd.DataFrame(_get(f"{NS_URL}/{qs_treat}")),
            )
        except requests.exceptions.ReadTimeout:
            if attempt == 1:
                time.sleep(RETRY_PAUSE)
            else:
                raise

st.write("Fetching data from Nightscout…")
try:
    entries_df, t_df = fetch_ns()
    st.success("Data loaded")
except requests.exceptions.ReadTimeout:
    st.error("Nightscout didn’t answer within 30 s (two tries). "
             "Click **Rerun** or try later.")
    st.stop()

# ── tidy data & window filter ───────────────────────────────────────
entries_df['time'] = pd.to_datetime(entries_df['dateString'], utc=True)
entries_df['mmol'] = (entries_df['sgv'] / 18).round(1)

t_df['time']  = pd.to_datetime(t_df['created_at'], utc=True)
t_df['isSMB'] = t_df['enteredBy'].str.contains('SMB', na=False)

bolus_df   = t_df[t_df['insulin'].notnull()]
smb_df     = bolus_df[ bolus_df['isSMB']]
manual_df  = bolus_df[~bolus_df['isSMB']]
carb_df    = t_df[t_df['carbs'].fillna(0) > 0]

basal_df   = t_df[t_df['eventType'] == 'Temp Basal']
if not basal_df.empty:
    basal_df['rate'] = pd.to_numeric(basal_df['rate'], errors='coerce')

def wnd(df): return df[(df['time'] >= start_dt) & (df['time'] <= end_dt)]
entries_df, manual_df, smb_df = map(wnd, (entries_df, manual_df, smb_df))
carb_df, basal_df             = wnd(carb_df), wnd(basal_df)

# ── build plot ──────────────────────────────────────────────────────
fig = go.Figure()

# BG line (1-decimal hover)
fig.add_trace(go.Scatter(
    x=entries_df['time'],  y=entries_df['mmol'],
    mode='lines+markers',  name='BG (mmol/L)', line=dict(color='green'),
    hovertemplate='%{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# bolus + SMB
fig.add_trace(go.Bar(
    x=manual_df['time'], y=manual_df['insulin'],
    name='Manual Bolus (U)', yaxis='y2',
    marker_color='rgba(0,102,204,0.6)',
    hovertemplate='%{y} U<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))
fig.add_trace(go.Bar(
    x=smb_df['time'], y=smb_df['insulin'],
    name='SMB (U)', yaxis='y2',
    marker_color='rgba(255,99,132,0.6)',
    hovertemplate='%{y} U (SMB)<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
))

# carbs (if any)
use_carbs = not carb_df.empty
if use_carbs:
    fig.add_trace(go.Bar(
        x=carb_df['time'], y=carb_df['carbs'],
        name='Carbs (g)', yaxis='y3',
        marker_color='rgba(255,165,0,0.55)',
        hovertemplate='%{y} g<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
    ))

# temp-basal (if any)
use_basal = not basal_df.empty
if use_basal:
    fig.add_trace(go.Scatter(
        x=basal_df['time'], y=basal_df['rate'],
        name='Temp Basal (U/h)', yaxis='y4',
        mode='lines', line_shape='hv', line=dict(color='purple'),
        hovertemplate='%{y} U/h<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
    ))

# ── layout – add only the axes we really need ──────────────────────
layout = dict(
    title='BG + Insulin + Carbs + Basal',
    xaxis=dict(title='Time'),
    yaxis=dict(title='BG (mmol/L)', range=[2, 15]),
    yaxis2=dict(title='Bolus U', overlaying='y', side='right', showgrid=False),
    bargap=0.15, height=600, legend=dict(orientation='h')
)

if use_carbs:
    layout['yaxis3'] = dict(
        title='Carbs g', overlaying='y',
        anchor='free', side='left', position=0.05,
        tickfont=dict(color='darkorange'),
        titlefont=dict(color='darkorange'),
        showgrid=False
    )
if use_basal:
    layout['yaxis4'] = dict(
        title='Basal U/h', overlaying='y',
        anchor='free', side='right', position=0.95,
        tickfont=dict(color='purple'),
        titlefont=dict(color='purple'),
        showgrid=False
    )

fig.update_layout(layout)
st.plotly_chart(fig, use_container_width=True)
# ─────────────────────────────────────────────────────────────────────
