###############################################################################
# Adriana Loop Dashboard – fast, 3-panel version
###############################################################################
import streamlit as st
import requests, pytz, json
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ─────────────────────────────────── Config ──────────────────────────────────
st.set_page_config('Adriana Loop Dashboard', layout='wide')
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL    = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET = st.secrets.get("API_SECRET", "")
HEADERS   = {"API-SECRET": NS_SECRET} if NS_SECRET else {}

local_tz  = pytz.timezone(str(datetime.now().astimezone().tzinfo))     # user tz

# ─────────────────────────────── Nightscout fetch ────────────────────────────
def to_ms(dt: datetime) -> int:
    """Nightscout wants epoch milli-seconds (UTC)."""
    return int(dt.timestamp() * 1000)

@st.cache_data(ttl=600)  # cache by (start_ms, end_ms)
def fetch_ns(start_ms: int, end_ms: int):
    """Return entries, treatments, profile (dict)."""
    q = f"find[date][$gte]={start_ms}&find[date][$lte]={end_ms}"
    entries  = requests.get(f"{NS_URL}/api/v1/entries.json?{q}&count=8640",
                            headers=HEADERS, timeout=15).json()
    treats   = requests.get(f"{NS_URL}/api/v1/treatments.json?{q}&count=2000",
                            headers=HEADERS, timeout=15).json()
    profile  = requests.get(f"{NS_URL}/api/v1/profile.json?count=1",
                            headers=HEADERS, timeout=15).json()[0]
    return (pd.DataFrame(entries),
            pd.DataFrame(treats),
            profile)

# ──────────────────────────────── Date widgets ──────────────────────────────
today = datetime.now(local_tz).date()
start_date = st.date_input("Start date", value=today)
start_time = st.time_input("Start time", value=datetime.min.time())
end_date   = st.date_input("End date",   value=today)
end_time   = st.time_input("End time",   value=datetime.max.time().replace(microsecond=0))

if st.button("Apply / Refresh"):
    st.session_state["load_trigger"] = True

# UTC bounds for query
start_dt = local_tz.localize(datetime.combine(start_date, start_time)).astimezone(pytz.UTC)
end_dt   = local_tz.localize(datetime.combine(end_date,   end_time  )).astimezone(pytz.UTC)

start_ms, end_ms = to_ms(start_dt), to_ms(end_dt)

# ─────────────────────────────── Fetch & prep ────────────────────────────────
st.write("Fetching data …")
entries_df, t_df, profile = fetch_ns(start_ms, end_ms)
st.success("Data loaded.")

# entries → mmol
entries_df['time'] = pd.to_datetime(entries_df['date'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(local_tz)
entries_df['mmol'] = entries_df['sgv'] / 18.0

# treatments split
t_df['time'] = pd.to_datetime(t_df['created_at']).dt.tz_convert(local_tz)
bolus_df = t_df[t_df['insulin'].notnull()]
smb_df   = bolus_df[bolus_df['enteredBy'].str.contains('SMB', na=False)]
man_df   = bolus_df[~bolus_df['enteredBy'].str.contains('SMB', na=False)]
carb_df  = t_df[t_df['carbs'].notnull() & (t_df['carbs']>0)]

# scheduled & temp basal
def build_sched(prof: dict, start: datetime, end: datetime) -> pd.DataFrame:
    rows=[]
    try:
        basal_segments = prof['store']['basalprofile']
        for seg in basal_segments:
            seg_start = local_tz.localize(datetime.combine(start.date(), datetime.min.time())) \
                        + timedelta(minutes=seg['timeAsSeconds']//60)
            if seg_start < start: seg_start = start
            while seg_start < end:
                rows.append({"time": seg_start, "rate": seg['value']})
                seg_start += timedelta(days=1)
    except Exception:
        pass
    return pd.DataFrame(rows)

sched_df = build_sched(profile, start_dt, end_dt)

temp_df  = t_df[(t_df['eventType']=="Temp Basal") & (t_df['duration']>0)]
temp_df  = temp_df[['time','rate']]

# ────────────────────────────── Build sub-plots ──────────────────────────────
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    vertical_spacing=0.04,
    row_heights=[0.45,0.25,0.30]
)

# ── Row 1 – BG ───────────────────────────────────────────────────────────────
fig.add_trace(
    go.Scatter(x=entries_df['time'], y=entries_df['mmol'],
               mode='lines', name='BG', line=dict(color='green', width=2)),
    row=1, col=1
)

# ── Row 2 – Bolus / SMB bars + carb circles ──────────────────────────────────
max_ins = max( (bolus_df['insulin'].max() or 0) * 1.2 , 1)

fig.add_trace(go.Bar(
        x=man_df['time'], y=man_df['insulin'],
        name='Manual bolus', marker_color='rgba(0,123,255,0.7)'
    ), row=2, col=1)

fig.add_trace(go.Bar(
        x=smb_df['time'], y=smb_df['insulin'],
        name='SMB', marker_color='rgba(255,99,132,0.7)'
    ), row=2, col=1)

# carbs as circles above bars
if not carb_df.empty:
    sizes = carb_df['carbs'] * 2  # scale factor
    fig.add_trace(go.Scatter(
        x=carb_df['time'], y=[max_ins*0.95]*len(carb_df),
        mode='markers+text',
        marker=dict(size=sizes, color='orange', opacity=0.8),
        text=carb_df['carbs'].astype(int).astype(str),
        textposition='top center',
        name='Carbs (g)'
    ), row=2, col=1)

fig.update_yaxes(range=[0, max_ins], row=2, col=1, title='U / g')

# ── Row 3 – basal lines ──────────────────────────────────────────────────────
if not sched_df.empty:
    fig.add_trace(go.Scatter(
        x=sched_df['time'], y=sched_df['rate'],
        mode='lines', name='Scheduled basal',
        line=dict(color='lightgrey', dash='dash')
    ), row=3, col=1)

if not temp_df.empty:
    fig.add_trace(go.Scatter(
        x=temp_df['time'], y=temp_df['rate'],
        mode='lines', name='Temp basal',
        fill='tozeroy',
        line=dict(color='rgb(102,0,204)', width=2)
    ), row=3, col=1)

fig.update_yaxes(row=3, col=1, title='U/hr')

# ── Layout ───────────────────────────────────────────────────────────────────
fig.update_layout(
    height=750,
    legend_orientation='h',
    margin=dict(t=40, b=25, l=40, r=10),
    hovermode='x unified',
)

st.plotly_chart(fig, use_container_width=True)
