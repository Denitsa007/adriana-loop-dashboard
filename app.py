# ──────────────────────────────────────────────────────────────────────────────
#  Adriana’s Looping Dashboard  –  single-file MVP for Streamlit Cloud
#  • fast first-page load   (only “today” is fetched at start-up)
#  • user can pick another period from a select-box ⇒ re-queries only then
#  • robust JSON parsing: works with  date/dateString/created_at/time  keys
#  • three stacked charts:
#       – glucose line (+ hover values with one decimal)
#       – bolus vs SMB bars + carb bubbles (size ∝ grams)
#       – scheduled basal dashed-grey & temp-basal area
# ──────────────────────────────────────────────────────────────────────────────
import json, requests, pytz, streamlit as st
from datetime import datetime, date, time, timedelta
import pandas as pd
import plotly.graph_objects as go

NS_URL      = "https://adriana007.eu.nightscoutpro.com"   #  ← change if needed
LOCAL_TZ    = pytz.timezone("Europe/Berlin")              #  ← your tz for widgets
TIMEOUT     = (6, 15)                                     #  connect , read  (s)

# ──────────────────────────────────────────────────────────────────────────────
def _to_utc(ts: str | int) -> pd.Timestamp:
    """convert any Nightscout timestamp variant to UTC pandas.Timestamp"""
    if isinstance(ts, int):                       # millis since epoch  ( date )
        return pd.to_datetime(ts, unit="ms", utc=True)
    ts = ts.rstrip("Z")                           # tolerate trailing Z
    return pd.to_datetime(ts, utc=True, errors="coerce")

def _pick_time(df: pd.DataFrame) -> pd.Series:
    """return a UTC Timestamp series irrespective of which ts column we got"""
    for col in ("date", "dateString", "created_at", "time"):
        if col in df.columns:
            return df[col].apply(_to_utc)
    raise KeyError("No timestamp column found in JSON")

@st.cache_data(show_spinner=False, ttl=600)        # 10-min cache
def fetch_ns(start_ms: int, end_ms: int):
    """download *just* the milliseconds interval the user asked for"""
    params = {"find[date][$gte]": start_ms, "find[date][$lte]": end_ms}

    # ── entries (CGM) ───────────────────────────────────────────────────────
    r = requests.get(f"{NS_URL}/api/v1/entries.json", params=params,
                     timeout=TIMEOUT)
    entries_df = (pd.DataFrame(r.json())
                  .assign(time=lambda d: _pick_time(d))
                  .sort_values("time"))

    # ── treatments (carbs / bolus / SMB / temp-basal)  – may be empty ──────
    try:
        t = requests.get(f"{NS_URL}/api/v1/treatments.json", params=params,
                         timeout=TIMEOUT).json()
        treats_df = pd.DataFrame(t)
        if not treats_df.empty:
            treats_df["time"] = _pick_time(treats_df)
    except Exception:
        treats_df = pd.DataFrame(columns=["time"])

    # ── profile (basal schedule) – only once per session ────────────────────
    prof = requests.get(f"{NS_URL}/api/v1/profile.json", timeout=TIMEOUT).json()[0]
    return entries_df, treats_df, prof

# ──────────────────────────────────────────────────────────────────────────────
def build_sched(profile: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp(start.floor("d"), tz=pytz.UTC)
    while base < end:
        for seg in profile["store"]["basalprofile"]:
            seg_time  = base + timedelta(minutes=int(seg["i"]))
            rows.append({"time": seg_time, "rate": seg["rate"]})
        base += pd.Timedelta("1d")
    return (pd.DataFrame(rows)
            .sort_values("time")
            .query("time >= @start & time <= @end"))

# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Adriana – Loop Dashboard", layout="wide")
st.markdown("### Adriana’s Looping Dashboard (MVP)")

# ── sidebar period picker (UTC safe)──────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    s_date = st.date_input("Start date", value=date.today(), key="sd")
    s_time = st.time_input("Start time", value=time(0, 0), key="st")
with col2:
    e_date = st.date_input("End date",   value=date.today(), key="ed")
    e_time = st.time_input("End time",   value=time(23,59),  key="et")

start_dt = LOCAL_TZ.localize(datetime.combine(s_date, s_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(e_date, e_time)).astimezone(pytz.UTC)

start_ms, end_ms = int(start_dt.timestamp()*1000), int(end_dt.timestamp()*1000)

with st.spinner("Fetching Nightscout data…"):
    entries_df, treats_df, profile = fetch_ns(start_ms, end_ms)

# ─────── data subsets ────────────────────────────────────────────────────────
bg_df     = entries_df  # all rows are CGM
bol_df    = treats_df.query("eventType == 'Correction Bolus'").copy()
smb_df    = treats_df.query("eventType == 'Bolus'").copy()
carb_df   = treats_df.query("carbs.notnull() & carbs > 0").copy()
temp_df   = treats_df.query("eventType == 'Temp Basal'").copy()
basal_sched_df = build_sched(profile, start_dt, end_dt)

# simplify names for plotting
bol_df["units"] = bol_df["insulin"]
smb_df["units"] = smb_df["insulin"]

# ─────── plotly figure with 3 stacked sub-plots ──────────────────────────────
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
    row_heights=[0.45,0.27,0.28])

# 1️⃣  BG line
fig.add_trace(go.Scatter(
    x=bg_df["time"], y=bg_df["sgv"]/18,         # mg/dl➜mmol
    mode="lines+markers", line=dict(width=2),
    hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}"), row=1, col=1)

# 2️⃣  Bolus / SMB bars + Carb bubbles
if not bol_df.empty:
    fig.add_trace(go.Bar(x=bol_df["time"], y=bol_df["units"],
                         name="Manual bolus", marker_color="#1f77b4"), row=2, col=1)
if not smb_df.empty:
    fig.add_trace(go.Bar(x=smb_df["time"], y=smb_df["units"],
                         name="SMB", marker_color="#ff7f0e"), row=2, col=1)
if not carb_df.empty:
    fig.add_trace(go.Scatter(
        x=carb_df["time"], y=[0.05]*len(carb_df), mode="markers+text",
        marker=dict(size=carb_df["carbs"], color="green", opacity=0.7),
        text=[f"{c} g" for c in carb_df["carbs"]],
        textposition="top center", showlegend=False), row=2, col=1)

# 3️⃣  scheduled + temp basal
fig.add_trace(go.Scatter(
    x=basal_sched_df["time"], y=basal_sched_df["rate"],
    mode="lines", name="Scheduled basal",
    line=dict(color="grey", width=1, dash="dash")), row=3, col=1)
if not temp_df.empty:
    fig.add_trace(go.Scatter(
        x=temp_df["time"], y=temp_df["rate"],
        mode="lines", name="Temp basal",
        fill="tozeroy", line=dict(color="#9467bd")), row=3, col=1)

# ─────── layout tweaks ───────────────────────────────────────────────────────
fig.update_yaxes(title_text="mmol/L",  row=1, col=1)
fig.update_yaxes(title_text="U / g",   row=2, col=1, rangemode="tozero")
fig.update_yaxes(title_text="U/h",     row=3, col=1, rangemode="tozero")

fig.update_layout(
    bargap=0.2, height=740, legend_orientation="h",
    hovermode="x unified", xaxis3=dict(showticklabels=True),
    template="simple_white", margin=dict(t=50,b=20,l=40,r=40))

st.plotly_chart(fig, use_container_width=True)
# ──────────────────────────────────────────────────────────────────────────────
