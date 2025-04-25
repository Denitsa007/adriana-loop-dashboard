import os, time, pytz, requests, streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, time as dtime, timedelta

# ──────────────────────────────  SETTINGS  ──────────────────────────────
NS_URL     = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET  = st.secrets["API_SECRET"]
DAYS_BACK  = 30                 # how many days of data to download
READ_TO    = 30                 # seconds for one HTTP read
MAX_RETRY  = 2                  # Nightscout retries on timeout
LOCAL_TZ   = pytz.timezone(time.tzname[0])

# ────────────────────────  NIGHTSCOUT DOWNLOAD  ────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns(max_retry=MAX_RETRY, read_to=READ_TO):
    hdr = {"API-SECRET": NS_SECRET}
    since = int((datetime.utcnow() - timedelta(days=DAYS_BACK)).timestamp()*1000)

    urls = dict(
        entries = f"{NS_URL}/api/v1/entries.json"
                  f"?find[date][$gte]={since}&count=8640",
        treats  = f"{NS_URL}/api/v1/treatments.json"
                  f"?find[created_at][$gte]={since}&count=4000",
        profile = f"{NS_URL}/api/v1/profile.json"
    )

    for attempt in range(max_retry + 1):
        try:
            to = (10, read_to)
            entries  = requests.get(urls['entries'], headers=hdr, timeout=to).json()
            treats   = requests.get(urls['treats'],  headers=hdr, timeout=to).json()
            profile  = requests.get(urls['profile'], headers=hdr, timeout=to).json()
            break
        except requests.exceptions.ReadTimeout:
            if attempt == max_retry:
                raise
            st.warning(f"Nightscout slow – retry {attempt+1}/{max_retry}…")
            time.sleep(1)

    edf = pd.DataFrame(entries)
    tdf = pd.DataFrame(treats)
    prof = profile[0] if profile else {}
    if not edf.empty:
        edf["time"] = pd.to_datetime(edf["date"], unit="ms", utc=True)
    if not tdf.empty:
        tdf["time"] = pd.to_datetime(tdf["created_at"], utc=True)
    return edf, tdf, prof

# ─────────────  BASAL SCHEDULE (from profile -> step dataframe)  ─────────
def build_sched(profile, start_utc, end_utc):
    if not profile:                     # guard – profile may be empty
        return pd.DataFrame()
    base = pd.to_datetime(profile["startDate"], utc=True)
    rows = []
    for seg in profile['store']['basalprofile']:
        seg_time = base + timedelta(minutes=int(seg['i']))
        rows.append(dict(time=seg_time, rate=float(seg['v'])))
    df = pd.DataFrame(rows).sort_values("time")
    # extend one extra row to cover last segment
    df = pd.concat([df, df.tail(1).assign(time=end_utc + timedelta(hours=1))])
    df = df[(df['time'] >= start_utc) & (df['time'] <= end_utc)]
    return df.reset_index(drop=True)

# ──────────────────────────────  UI  ────────────────────────────────────
st.set_page_config("Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

today = date.today()
col1, col2, col3, col4 = st.columns(4)
with col1: sd = st.date_input("Start date",  today)
with col2: st_time = st.time_input("Start time", dtime(0,0))
with col3: ed = st.date_input("End date",    today)
with col4: et_time = st.time_input("End time",   dtime(23,59))

start_dt = LOCAL_TZ.localize(datetime.combine(sd, st_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(ed, et_time)).astimezone(pytz.UTC)

st.write("Fetching data from Nightscout…")
entries_df, treats_df, profile = fetch_ns()
st.success("Data loaded.")

# ────────────────────────  FILTER BY DATE  ─────────────────────────────
entries_df = entries_df[(entries_df['time'] >= start_dt) & (entries_df['time'] <= end_dt)]
treats_df  = treats_df[(treats_df['time']  >= start_dt) & (treats_df['time']  <= end_dt)]

# ─────────────────────────  PREPARE DATA  ──────────────────────────────
entries_df['mmol'] = (entries_df['sgv'] / 18).round(1)  # mg/dL → mmol/L

bolus_df = treats_df[treats_df['insulin'].notnull()]
smb_df   = bolus_df[bolus_df['enteredBy'].str.contains('SMB', na=False)]
man_df   = bolus_df[~bolus_df['enteredBy'].str.contains('SMB', na=False)]

carb_df  = treats_df[treats_df['carbs'].fillna(0) > 0]

basal_sched_df = build_sched(profile, start_dt, end_dt)

# ────────────────────────────  PLOTTING  ───────────────────────────────
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    row_heights=[0.5, 0.25, 0.25],
    vertical_spacing=0.02,
    specs=[[{}],[{}],[{}]]
)

# BG line
fig.add_trace(go.Scatter(
    x=entries_df['time'], y=entries_df['mmol'],
    mode='lines', line=dict(color='green'),
    name='BG (mmol/L)', hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}"
), row=1, col=1)

# Carb dots
fig.add_trace(go.Scatter(
    x=carb_df['time'], y=[15]*len(carb_df),
    mode='markers+text', marker=dict(color='orange', size=8),
    text=carb_df['carbs'].astype(int).astype(str)+' g',
    textposition="top center",
    name='Carbs'
), row=2, col=1)

# Manual bolus bars
fig.add_trace(go.Bar(
    x=man_df['time'], y=man_df['insulin'],
    marker_color='rgba(0,102,204,0.6)', name='Manual bolus'
), row=2, col=1)

# SMB bars
fig.add_trace(go.Bar(
    x=smb_df['time'], y=smb_df['insulin'],
    marker_color='rgba(255,99,132,0.6)', name='SMB'
), row=2, col=1)

# Scheduled basal (step)
if not basal_sched_df.empty:
    fig.add_trace(go.Scatter(
        x=basal_sched_df['time'], y=basal_sched_df['rate'],
        mode='lines', line_shape='hv',
        line=dict(color='black'), name='Scheduled basal'
    ), row=3, col=1)

# Temp basal (from treatments)
temp_df = treats_df[treats_df['eventType'] == "Temp Basal"]
if not temp_df.empty:
    fig.add_trace(go.Bar(
        x=temp_df['time'], y=temp_df['rate'].fillna(0),
        marker_color='rgba(200,200,200,0.6)', name='Temp basal'
    ), row=3, col=1)

# Layout tweaks
fig.update_yaxes(title_text="mmol/L", row=1, col=1, range=[2,15])
fig.update_yaxes(title_text="U / g carbs", row=2, col=1)
fig.update_yaxes(title_text="Basal U/h", row=3, col=1)

fig.update_layout(
    height=800, bargap=0.15, legend_orientation="h",
    margin=dict(t=40, r=20, b=40, l=60)
)

st.plotly_chart(fig, use_container_width=True)
