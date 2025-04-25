# ────────────────────────────────────────────────────────────────────
#  Adriana Loop Dashboard – BG · Bolus/Carbs · Basal (+ scheduled)
# ────────────────────────────────────────────────────────────────────
import streamlit as st, requests, pandas as pd, re, json
from datetime import datetime, time as dtime, timedelta, timezone
from plotly.subplots import make_subplots
import plotly.graph_objects as go

# ─── 0.  Page / secrets ─────────────────────────────────────────────
st.set_page_config(page_title="Adriana's Looping Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL, NS_SECRET = st.secrets["NIGHTSCOUT_URL"], st.secrets["API_SECRET"]
HDRS, TIMEOUT = {"API-SECRET": NS_SECRET}, 30

# ─── 1.  Date-time pickers ──────────────────────────────────────────
today_utc = datetime.now(timezone.utc).date()
c1, c2 = st.columns(2)
with c1:
    sd = st.date_input("Start date", today_utc)
    stime = st.time_input("Start time", dtime(0, 0))
with c2:
    ed = st.date_input("End date", today_utc)
    etime = st.time_input("End time", dtime(23, 59))

start_dt = datetime.combine(sd, stime, tzinfo=timezone.utc)
end_dt   = datetime.combine(ed, etime, tzinfo=timezone.utc)

# ─── 2.  Helpers ────────────────────────────────────────────────────
def _get(endpoint, count):
    qs = f"api/v1/{endpoint}.json?count={count}"
    r = requests.get(f"{NS_URL}/{qs}", headers=HDRS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns():
    entries   = _get("entries",    8640)   # ~3 days CGM
    treats    = _get("treatments", 2000)
    profiles  = _get("profile",       1)   # newest profile set
    return (pd.DataFrame(entries),
            pd.DataFrame(treats),
            json.loads(json.dumps(profiles))[0])   # ensure dict

# ─── 3.  Load data ──────────────────────────────────────────────────
st.write("Fetching Nightscout data…")
try:
    entries_df, t_df, profile = fetch_ns()
    st.success("Data loaded")
except Exception as e:
    st.error(f"Nightscout error → {e}")
    st.stop()

# ─── 4.  CGM entries ────────────────────────────────────────────────
entries_df['time'] = pd.to_datetime(entries_df['dateString'], utc=True)
entries_df['mmol'] = (entries_df['sgv'] / 18).round(1)

# ─── 5.  Treatments → bolus / carbs / temp basal ───────────────────
t_df['time'] = pd.to_datetime(t_df['created_at'], utc=True)

bolus_col = next((c for c in ['insulin', 'bolus', 'amount'] if c in t_df.columns), None)
if bolus_col:
    t_df[bolus_col] = pd.to_numeric(t_df[bolus_col], errors='coerce')
    bolus_df = t_df[t_df[bolus_col].notnull()].copy()
    bolus_df.rename(columns={bolus_col: 'units'}, inplace=True)
else:
    bolus_df = pd.DataFrame(columns=['time', 'units'])

smb_mask   = bolus_df['enteredBy'].str.contains('smb', flags=re.I, na=False)
smb_df     = bolus_df[smb_mask]
manual_df  = bolus_df[~smb_mask]
carb_df    = t_df[t_df['carbs'].fillna(0) > 0][['time', 'carbs']]
temp_df    = t_df[t_df['eventType'] == 'Temp Basal'].copy()
temp_df['rate'] = pd.to_numeric(temp_df.get('rate'), errors='coerce')

# ─── 6.  Scheduled basal from profile ───────────────────────────────
def build_sched(profile_dict, window_start, window_end):
    """Return a DataFrame with time & rate for the profile basal schedule."""
    default = profile_dict['store'][profile_dict['defaultProfile']]
    sched   = default['basal']   # list of {'i': min-of-day, 'rate': x}
    dfs = []
    day0 = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    num_days = (window_end.date() - window_start.date()).days + 1
    for d in range(num_days):
        base = day0 + timedelta(days=d)
        for seg in sched:
            seg_time = base + timedelta(minutes=int(seg['i']))
            dfs.append({'time': seg_time, 'rate': seg['rate']})
        # add a final point @23:59 to make step look nice
        dfs.append({'time': base + timedelta(hours=23, minutes=59, seconds=59),
                    'rate': sched[-1]['rate']})
    df = pd.DataFrame(dfs)
    return df[(df['time'] >= window_start) & (df['time'] <= window_end)]

sched_df = build_sched(profile, start_dt, end_dt)

# ─── 7.  Window-filter everything ───────────────────────────────────
def wnd(df):
    return df[(df['time'] >= start_dt) & (df['time'] <= end_dt)]
entries_df, manual_df, smb_df = map(wnd, (entries_df, manual_df, smb_df))
carb_df, temp_df, sched_df   = map(wnd, (carb_df, temp_df, sched_df))

# ─── 8.  Diagnostics ────────────────────────────────────────────────
st.write(
    f"Rows – BG {len(entries_df)} | bolus {len(manual_df)} | SMB {len(smb_df)} "
    f"| carbs {len(carb_df)} | temp {len(temp_df)} | sched {len(sched_df)}"
)

# ─── 9.  Plot – three stacked panels ────────────────────────────────
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.02,
                    row_heights=[0.45, 0.30, 0.25])

# 9-A  BG
fig.add_trace(go.Scatter(
    x=entries_df['time'], y=entries_df['mmol'],
    mode='lines+markers', name='BG (mmol/L)', line=dict(color='green'),
    hovertemplate='%{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
), row=1, col=1)

# 9-B  Bolus / carbs
for df, name, col in [
    (manual_df, 'Manual Bolus (U)', 'rgba(0,102,204,0.6)'),
    (smb_df,    'SMB (U)',          'rgba(255,99,132,0.6)'),
    (carb_df,   'Carbs (g)',        'rgba(255,165,0,0.55)')]:
    if not df.empty:
        fig.add_trace(go.Bar(
            x=df['time'], y=df[df.columns[-1]],
            name=name, marker_color=col,
            hovertemplate='%{y} '+name.split()[0]+
                          '<br>%{x|%Y-%m-%d %H:%M}<extra></extra>'
        ), row=2, col=1)

# 9-C  Basal
if not sched_df.empty:           # scheduled (grey dashed)
    fig.add_trace(go.Scatter(
        x=sched_df['time'], y=sched_df['rate'],
        name='Scheduled Basal (U/h)', mode='lines', line_shape='hv',
        line=dict(color='grey', dash='dash')
    ), row=3, col=1)
if not temp_df.empty:            # temporary (purple)
    fig.add_trace(go.Scatter(
        x=temp_df['time'], y=temp_df['rate'],
        name='Temp Basal (U/h)', mode='lines', line_shape='hv',
        line=dict(color='purple')
    ), row=3, col=1)

# ─── 10.  Layout ────────────────────────────────────────────────────
fig.update_yaxes(title_text="BG (mmol/L)", row=1, col=1, range=[2, 15])
fig.update_yaxes(title_text="Bolus / Carbs", row=2, col=1)
fig.update_yaxes(title_text="Basal (U/h)",  row=3, col=1)

fig.update_layout(
    height=800, bargap=0.15, legend=dict(orientation='h'),
    title="BG, Insulin, Carbs & Basal (scheduled + temp)"
)
st.plotly_chart(fig, use_container_width=True)
# ────────────────────────────────────────────────────────────────────
