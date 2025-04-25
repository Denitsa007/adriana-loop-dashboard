# adriana_loop_dashboard/app.py  ───────────────────────────────────────
import streamlit as st, requests, pandas as pd, re
from datetime import datetime, timedelta, timezone
from plotly.subplots import make_subplots
import plotly.graph_objects as go

# ─── Streamlit page basics ────────────────────────────────────────────
st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL, NS_SECRET = st.secrets["NIGHTSCOUT_URL"], st.secrets["API_SECRET"]
HDRS, TIMEOUT = {"API-SECRET": NS_SECRET}, 20

# ─── pull a small sample so we can discover column names  ─────────────
def _sample(endpoint, n=40):
    url = f"{NS_URL}/api/v1/{endpoint}.json?count={n}"
    r = requests.get(url, headers=HDRS, timeout=TIMEOUT); r.raise_for_status()
    return r.json()

sample_entries   = _sample("entries")
sample_treats    = _sample("treatments")

def find_key(records, *patterns):
    keys = set().union(*(rec.keys() for rec in records))
    for pat in patterns:
        for k in keys:
            if re.search(pat, k, re.I):
                return k
    return None

BGL_KEY  = find_key(sample_entries, r'^(sgv|glucose|value)$')
TIME_KEY = find_key(sample_entries, r'(dateString|created_at|date)')
INS_KEY  = find_key(sample_treats,  r'^(insulin|bolus|units|amount)$')
CARB_KEY = find_key(sample_treats,  r'^(carbs|carbsInput|carbohydrates)$')
RATE_KEY = find_key(sample_treats,  r'^(rate|value)$')    # temp-basal rate

# ─── verify required fields were detected  ────────────────────────────
required = {
    "BG"  : BGL_KEY,
    "TIME": TIME_KEY,
    "INS" : INS_KEY,
    "CARB": CARB_KEY,
}
missing = [name for name, val in required.items() if val is None]
if missing:
    st.error(
        "Can’t find required Nightscout keys: "
        + ", ".join(missing)
        + ".  Please check your Nightscout data."
    )
    st.stop()

# ─── download full data & cache for 10 min  ───────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def load_data():
    entries = pd.DataFrame(
        _sample("entries", 8640)   # ≈ 3 days @ 5-min resolution
    )
    treats  = pd.DataFrame(
        _sample("treatments", 2000)
    )
    profile = _sample("profile", 1)[0] if _sample("profile", 1) else {}
    return entries, treats, profile

with st.spinner("Fetching Nightscout data..."):
    entries_df, treat_df, profile = load_data()
st.success("Data loaded.")

# ─── basic time-window widgets  ───────────────────────────────────────
today = datetime.now().date()
col1, col2 = st.columns(2)
start_date = col1.date_input("Start date", value=today)
end_date   = col2.date_input("End date",   value=today)
start_time = col1.time_input("Start time", value=datetime.min.time())
end_time   = col2.time_input("End time",   value=datetime.max.time().replace(microsecond=0))

start_dt = datetime.combine(start_date, start_time, tzinfo=timezone.utc)
end_dt   = datetime.combine(end_date,   end_time,   tzinfo=timezone.utc)

# ─── tidy / filter data  ──────────────────────────────────────────────
entries_df['time'] = pd.to_datetime(entries_df[TIME_KEY], utc=True)
entries_df['mmol'] = (entries_df[BGL_KEY] / 18).round(1)
entries_df = entries_df[(entries_df['time'] >= start_dt) & (entries_df['time'] <= end_dt)]

treat_df['time'] = pd.to_datetime(treat_df['created_at'], utc=True)
treat_df = treat_df[(treat_df['time'] >= start_dt) & (treat_df['time'] <= end_dt)]

bolus_df = treat_df[pd.to_numeric(treat_df[INS_KEY], errors='coerce').notna()].rename(columns={INS_KEY:'units'})
carb_df  = treat_df[pd.to_numeric(treat_df[CARB_KEY], errors='coerce').notna()].rename(columns={CARB_KEY:'grams'})

temp_df  = treat_df[
    treat_df['eventType'].eq('Temp Basal') &
    pd.to_numeric(treat_df[RATE_KEY], errors='coerce').notna()
].rename(columns={RATE_KEY:'rate'})

# ─── scheduled basal from profile  ────────────────────────────────────
def build_sched(prof, window_start, window_end):
    if not prof:
        return pd.DataFrame()
    tz   = timezone.utc
    base = datetime.combine(window_start.date(), datetime.min.time(), tzinfo=tz)
    sched=[]
    for seg in prof['store']['basalprofile']:
        t   = base + timedelta(minutes=int(seg['i']))
        if window_start <= t <= window_end:
            sched.append({"time": t, "rate": seg['v']})
    return pd.DataFrame(sched)

sched_df = build_sched(profile, start_dt, end_dt)

# ─── create 3-panel plot  ─────────────────────────────────────────────
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.02,
                    row_heights=[0.45, 0.30, 0.25])

# 1️⃣  BG
fig.add_trace(go.Scatter(
    x=entries_df['time'], y=entries_df['mmol'],
    mode='lines+markers', name='BG',
    hovertemplate='%{y:.1f} mmol'), row=1, col=1)

# 2️⃣  Bolus + Carbs
if not bolus_df.empty:
    fig.add_trace(go.Bar(
        x=bolus_df['time'], y=bolus_df['units'],
        name='Bolus (U)', marker_color='royalblue',
        hovertemplate='%{y} U'), row=2, col=1)

if not carb_df.empty:
    fig.add_trace(go.Scatter(
        x=carb_df['time'], y=[0]*len(carb_df),
        mode='markers+text', text=carb_df['grams'],
        textposition='top center',
        name='Carbs (g)', marker=dict(color='orange', size=10),
        hovertemplate='%{text} g'), row=2, col=1)

# 3️⃣  Basal
if not sched_df.empty:
    fig.add_trace(go.Scatter(
        x=sched_df['time'], y=sched_df['rate'],
        mode='lines', line=dict(color='gray', dash='dot'),
        name='Scheduled basal'), row=3, col=1)

if not temp_df.empty:
    fig.add_trace(go.Scatter(
        x=temp_df['time'], y=temp_df['rate'],
        mode='lines', line_shape='hv',
        line=dict(color='purple'),
        name='Temp basal'), row=3, col=1)

fig.update_layout(
    height=850, bargap=0.15, legend_orientation='h',
    yaxis_title='BG (mmol/L)', yaxis2_title='Bolus/Carbs', yaxis3_title='Basal U/h'
)

st.plotly_chart(fig, use_container_width=True)
