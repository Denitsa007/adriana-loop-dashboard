# ─── auto-detect Nightscout fields ───────────────────────
import streamlit as st, requests, pandas as pd, re, json
from datetime import datetime, timedelta, time as dtime, timezone
from plotly.subplots import make_subplots
import plotly.graph_objects as go

NS_URL, NS_SECRET = st.secrets["NIGHTSCOUT_URL"], st.secrets["API_SECRET"]
HDRS, TIMEOUT = {"API-SECRET": NS_SECRET}, 20

# ─── grab a tiny sample just to sniff the keys ───────────
def _sample(endpoint, n=30):
    url = f"{NS_URL}/api/v1/{endpoint}.json?count={n}"
    r = requests.get(url, headers=HDRS, timeout=TIMEOUT); r.raise_for_status()
    return r.json()

sample_entries   = _sample("entries")
sample_treats    = _sample("treatments")

# ─── helper to find the first key that matches a pattern ─
def find_key(records, *patterns):
    keys = set().union(*(rec.keys() for rec in records))
    for pat in patterns:
        for k in keys:
            if re.search(pat, k, re.I):
                return k
    return None

BGL_KEY   = find_key(sample_entries,  r'^(sgv|glucose|value)$')
TIME_KEY  = find_key(sample_entries,  r'(dateString|created_at|date)')
INS_KEY   = find_key(sample_treats,   r'^(insulin|bolus|units|amount)$')
CARB_KEY  = find_key(sample_treats,   r'^(carbs|carbsInput|carbohydrates)$')
RATE_KEY  = find_key(sample_treats,   r'^(rate|value)$')   # basal / temp

# safety-net
missing = [k for k,(label,k) in dict(
    BG=BGL_KEY, TIME=TIME_KEY, INS=INS_KEY, CARB=CARB_KEY).items() if k is None]
if missing:
    st.error(f"Can’t find required Nightscout keys: {', '.join(missing)}")
    st.stop()

# ─── now load *full* data using the detected keys ────────
@st.cache_data(ttl=600)
def load_data():
    entries = pd.DataFrame(_sample("entries", 8640))
    treats  = pd.DataFrame(_sample("treatments", 2000))
    prof    = _sample("profile", 1)[0]
    return entries, treats, prof

entries_df, treat_df, profile = load_data()

# ─── convert & filter time window (pickers omitted here) ─
entries_df['time'] = pd.to_datetime(entries_df[TIME_KEY], utc=True)
entries_df['mmol'] = (entries_df[BGL_KEY] / 18).round(1)

treat_df['time']  = pd.to_datetime(treat_df['created_at'], utc=True)

bolus_df = treat_df[pd.to_numeric(treat_df[INS_KEY], errors='coerce').notna()] \
              .rename(columns={INS_KEY:'units'})
carb_df  = treat_df[pd.to_numeric(treat_df[CARB_KEY], errors='coerce').notna()] \
              .rename(columns={CARB_KEY:'grams'})
temp_df  = treat_df[treat_df['eventType'].eq('Temp Basal') & \
                    pd.to_numeric(treat_df[RATE_KEY], errors='coerce').notna()] \
              .rename(columns={RATE_KEY:'rate'})

# ─── figure (BG + bolus/carbs + basal) exactly as before ─
fig = make_subplots(rows=3, cols=1,
                    shared_xaxes=True, vertical_spacing=0.02,
                    row_heights=[0.45,0.30,0.25])

fig.add_trace(go.Scatter(x=entries_df['time'], y=entries_df['mmol'],
              mode='lines+markers', name='BG',
              hovertemplate='%{y:.1f} mmol'), row=1, col=1)

if not bolus_df.empty:
    fig.add_trace(go.Bar(x=bolus_df['time'], y=bolus_df['units'],
                         name='Bolus', marker_color='royalblue',
                         hovertemplate='%{y} U'), row=2, col=1)
if not carb_df.empty:
    fig.add_trace(go.Scatter(x=carb_df['time'], y=[0]*len(carb_df),
                             mode='markers+text', text=carb_df['grams'],
                             name='Carbs (g)', marker=dict(color='orange', size=10),
                             hovertemplate='%{text} g'), row=2, col=1)
if not temp_df.empty:
    fig.add_trace(go.Scatter(x=temp_df['time'], y=temp_df['rate'],
                             mode='lines', line_shape='hv', name='Temp basal',
                             line=dict(color='purple')), row=3, col=1)

fig.update_layout(height=800, bargap=0.15, legend_orientation='h')
st.plotly_chart(fig, use_container_width=True)
