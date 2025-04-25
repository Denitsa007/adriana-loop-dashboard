# ────────────────────────────────────────────────────────────────────
#  Adriana Loop Dashboard – 3-panel layout (BG | Bolus+Carbs | Basal)
# ────────────────────────────────────────────────────────────────────
import streamlit as st, requests, pandas as pd, time
from datetime import datetime, time as dtime, timezone
import plotly.graph_objects as go
from plotly.subplots import make_subplots
# ❶ Page / secrets ---------------------------------------------------
st.set_page_config(page_title="Adriana's Looping Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")
NS_URL, NS_SECRET = st.secrets["NIGHTSCOUT_URL"], st.secrets["API_SECRET"]
HEADERS, TIMEOUT, RETRY_PAUSE = {"API-SECRET": NS_SECRET}, 30, 3
# ❷ Date-time pickers ------------------------------------------------
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
# ❸ Nightscout fetch (cached 10 min) --------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns():
    def _get(uri):  # single GET with timeout + secret header
        return requests.get(uri, headers=HEADERS, timeout=TIMEOUT).json()
    qs_e = "api/v1/entries.json?count=8640"     # ≈3 days CGM
    qs_t = "api/v1/treatments.json?count=2000"  # ≈3 days Tx
    for attempt in (1, 2):
        try:
            return (
                pd.DataFrame(_get(f"{NS_URL}/{qs_e}")),
                pd.DataFrame(_get(f"{NS_URL}/{qs_t}")),
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
    st.error("Nightscout did not answer in 30 s (two tries). Click **Rerun**.")
    st.stop()
# ❹ Tidy data + window filter ---------------------------------------
entries_df['time'] = pd.to_datetime(entries_df['dateString'], utc=True)
entries_df['mmol'] = (entries_df['sgv'] / 18).round(1)          # 1 decimal
t_df['time']  = pd.to_datetime(t_df['created_at'], utc=True)
t_df['isSMB'] = t_df['enteredBy'].str.contains('SMB', na=False)
bolus_df   = t_df[t_df['insulin'].notnull()]
smb_df     = bolus_df[ bolus_df['isSMB']]
manual_df  = bolus_df[~bolus_df['isSMB']]
carb_df    = t_df[t_df['carbs'].fillna(0) > 0]
basal_df   = t_df[t_df['eventType'] == 'Temp Basal']
if not basal_df.empty:
    basal_df['rate'] = pd.to_numeric(basal_df['rate'], errors='coerce')
def wnd(df):
    return df[(df['time'] >= start_dt) & (df['time'] <= end_dt)]
entries_df, manual_df, smb_df = map(wnd, (entries_df, manual_df, smb_df))
carb_df, basal_df             = wnd(carb_df), wnd(basal_df)
have_carbs, have_basal = not carb_df.empty, not basal_df.empty
# ❺ Build 3-row subplot figure --------------------------------------
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.02,
    row_heights=[0.45, 0.30, 0.25],
    specs=[[{}], [{}], [{}]]
)
# Row 1 – BG
fig.add_trace(go.Scatter(
    x=entries_df['time'], y=entries_df['mmol'],
    mode='lines+markers', name='BG (mmol/L)',
    line=dict(color='green'),
    hovertemplate='%{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
), row=1, col=1)
# Row 2 – manual bolus + SMB + carbs
fig.add_trace(go.Bar(
    x=manual_df['time'], y=manual_df['insulin'],
    name='Manual Bolus (U)', marker_color='rgba(0,102,204,0.6)',
    hovertemplate='%{y} U<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
), row=2, col=1)
fig.add_trace(go.Bar(
    x=smb_df['time'], y=smb_df['insulin'],
    name='SMB (U)', marker_color='rgba(255,99,132,0.6)',
    hovertemplate='%{y} U (SMB)<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
), row=2, col=1)
if have_carbs:
    fig.add_trace(go.Bar(
        x=carb_df['time'], y=carb_df['carbs'],
        name='Carbs (g)', marker_color='rgba(255,165,0,0.55)',
        hovertemplate='%{y} g<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
    ), row=2, col=1)
# Row 3 – temp-basal rate
if have_basal:
    fig.add_trace(go.Scatter(
        x=basal_df['time'], y=basal_df['rate'],
        name='Temp Basal (U/h)', mode='lines',
        line_shape='hv', line=dict(color='purple'),
        hovertemplate='%{y} U/h<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
    ), row=3, col=1)
# ❻ Layout -----------------------------------------------------------
fig.update_yaxes(title_text="BG (mmol/L)", row=1, col=1, range=[2, 15])
fig.update_yaxes(title_text="Bolus / Carbs", row=2, col=1)
fig.update_yaxes(title_text="Basal U/h", row=3, col=1)
fig.update_layout(
    height=750, bargap=0.15, legend=dict(orientation='h'),
    title="BG, Insulin, Carbs & Basal – three-panel view"
)
st.plotly_chart(fig, use_container_width=True)
# ────────────────────────────────────────────────────────────────────
