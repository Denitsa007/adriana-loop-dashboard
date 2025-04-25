###############################################################################
# Adriana Loop Dashboard – compact, fast & typed
###############################################################################
from __future__ import annotations
import hashlib, json, os, pytz, requests, streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ───────────────────────────── App-wide settings ─────────────────────────────
st.set_page_config("Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL: str   = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET: str = st.secrets.get("API_SECRET", "")
HEADERS       = {"API-SECRET": NS_SECRET} if NS_SECRET else {}

LOCAL_TZ = pytz.timezone(str(datetime.now().astimezone().tzinfo))

# ──────────────────────────── Helper functions ───────────────────────────────
def to_ms(dt: datetime) -> int:            # Epoch-ms helper
    return int(dt.timestamp() * 1000)

def hash_url(url: str) -> str:             # for cache key
    return hashlib.md5(url.encode()).hexdigest()

@st.cache_data(ttl=900, show_spinner=False)
def fetch_ns(start_ms: int, end_ms: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Download & return (entries_df, treatments_df, profile)."""
    # ----- calculate minimal `count` -----
    days = max(1, int((end_ms - start_ms) / 86400000) + 1)
    count_entries   = days * 300        # CGM every 5 min ⇒ 288, add margin
    count_treats    = days * 400        # generous
    
    q = f"find[date][$gte]={start_ms}&find[date][$lte]={end_ms}"
    def _get(path: str, count: int|None = None) -> list[dict]:
        url = f"{NS_URL}{path}?{q}" + (f"&count={count}" if count else "")
        return requests.get(url, headers=HEADERS, timeout=15).json()

    entries  = _get("/api/v1/entries.json",   count_entries)
    treats   = _get("/api/v1/treatments.json", count_treats)
    profile  = _get("/api/v1/profile.json",    None)[0]

    entries_df = (pd.json_normalize(entries)
                    .loc[:, ['date','sgv']]
                    .assign(time=lambda df: pd.to_datetime(df['date'], unit='ms')
                                          .dt.tz_localize('UTC')
                                          .dt.tz_convert(LOCAL_TZ),
                            mmol=lambda df: df['sgv']/18.0)
                    .drop(columns='date'))
    
    treats_df  = (pd.json_normalize(treats)
                    .assign(time=lambda df: pd.to_datetime(df['created_at'])
                                          .dt.tz_convert(LOCAL_TZ)))
    return entries_df, treats_df, profile

def build_sched(profile: dict, start: datetime, end: datetime) -> pd.DataFrame:
    """Expand basalprofile into a time/value series for [start,end]."""
    try:
        segments = profile['store']['basalprofile']
    except Exception:
        return pd.DataFrame(columns=['time','rate'])
    
    rows = []
    day0 = LOCAL_TZ.localize(datetime.combine(start.date(), datetime.min.time()))
    for seg in segments:
        off  = timedelta(seconds=int(seg['i']))    # offset in the day
        rate = seg['value']
        # generate a date_range for every 24 h crossing our interval
        times = pd.date_range(start=day0+off, end=end+timedelta(days=1),
                              freq='24h', tz=LOCAL_TZ)
        for t in times:
            if start <= t <= end: rows.append((t, rate))
    return pd.DataFrame(rows, columns=['time','rate'])

def make_fig() -> go.Figure:
    """Return an empty 3-row subplot container."""
    return make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        vertical_spacing=0.05, row_heights=[0.45,0.25,0.30]
    )

# ────────────────────────────── UI – widgets ─────────────────────────────────
today = datetime.now(LOCAL_TZ).date()
col1, col2, col3, col4 = st.columns(4)
with col1: start_date = st.date_input("Start", today)
with col2: start_time = st.time_input("Time", datetime.min.time())
with col3: end_date   = st.date_input("End",   today)
with col4: end_time   = st.time_input(" ",     datetime.max.time().replace(microsecond=0))

if st.button("Apply / Refresh"): st.session_state['trigger']=True

# ISO-aware bounds
start_dt = LOCAL_TZ.localize(datetime.combine(start_date, start_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(end_date,   end_time  )).astimezone(pytz.UTC)

start_ms, end_ms = to_ms(start_dt), to_ms(end_dt)

# ─────────────────────────────── Load data ───────────────────────────────────
st.caption("Contacting Nightscout …")
entries_df, t_df, profile = fetch_ns(start_ms, end_ms)
st.success("Nightscout slice loaded.")

# ─────────────────────── Derive bolus / carb / basal dfs ─────────────────────
bolus_df  = t_df[t_df['insulin'].notnull()]
smb_df    = bolus_df[bolus_df['enteredBy'].str.contains('SMB', na=False)]
man_df    = bolus_df[~bolus_df['enteredBy'].str.contains('SMB', na=False)]
carb_df   = t_df[t_df['carbs'].notnull() & (t_df['carbs']>0)]
sched_df  = build_sched(profile, start_dt, end_dt)
temp_df   = t_df[(t_df['eventType']=="Temp Basal") & (t_df['duration']>0)]

# ───────────────────────────── Plot construction ─────────────────────────────
fig = make_fig()

# Row 1 – BG
fig.add_trace(go.Scatter(x=entries_df['time'], y=entries_df['mmol'],
                         mode='lines', name='BG', line=dict(color='green', width=2)),
              row=1, col=1)

# Row 2 – Bolus bars + carb circles
y_max = max((bolus_df['insulin'].max() or 0)*1.2, 1)
fig.update_yaxes(range=[0, y_max], row=2, col=1, title='U / g')

fig.add_trace(go.Bar(x=man_df['time'], y=man_df['insulin'],
                     name='Manual', marker_color='rgb(0,123,255)'), row=2, col=1)
fig.add_trace(go.Bar(x=smb_df['time'], y=smb_df['insulin'],
                     name='SMB', marker_color='rgb(255,99,132)'), row=2, col=1)

if not carb_df.empty:
    sizes = carb_df['carbs']*2
    fig.add_trace(go.Scatter(x=carb_df['time'],
                             y=[y_max*0.95]*len(carb_df),
                             mode='markers+text',
                             marker=dict(size=sizes, color='orange', opacity=.8),
                             text=carb_df['carbs'].astype(int).astype(str),
                             textposition='top center',
                             showlegend=False), row=2, col=1)

# Row 3 – basal
if not sched_df.empty:
    fig.add_trace(go.Scatter(x=sched_df['time'], y=sched_df['rate'],
                             mode='lines', name='Scheduled',
                             line=dict(color='lightgrey', dash='dash')), row=3, col=1)
if not temp_df.empty:
    fig.add_trace(go.Scatter(x=temp_df['time'], y=temp_df['rate'],
                             mode='lines', fill='tozeroy', name='Temp basal',
                             line=dict(color='rgb(102,0,204)', width=2)), row=3, col=1)
fig.update_yaxes(row=3, col=1, title='U/hr')

# layout tweaks
fig.update_layout(
    height=750,
    hovermode='x unified',
    legend_orientation='h',
    margin=dict(t=40, b=25, l=40, r=10)
)
st.plotly_chart(fig, use_container_width=True)
