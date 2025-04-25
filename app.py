import os, time, pytz, requests, streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, time as dtime, timedelta

# ──────────  CONFIG  ──────────
NS_URL    = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET = st.secrets["API_SECRET"]
DAYS_BACK = 30        # download window
READ_TO   = 30        # seconds / request
MAX_RETRY = 2
LOCAL_TZ  = pytz.timezone(time.tzname[0])

# ──────────  NIGHTSCOUT  ──────────
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns():
    hdr   = {"API-SECRET": NS_SECRET}
    since = int((datetime.utcnow() - timedelta(days=DAYS_BACK)).timestamp()*1000)

    urls = {
        "entries": f"{NS_URL}/api/v1/entries.json?find[date][$gte]={since}&count=8640",
        "treats" : f"{NS_URL}/api/v1/treatments.json?find[created_at][$gte]={since}&count=4000",
        "profile": f"{NS_URL}/api/v1/profile.json"
    }

    for a in range(MAX_RETRY + 1):
        try:
            to = (10, READ_TO)
            entries = requests.get(urls["entries"], headers=hdr, timeout=to).json()
            treats  = requests.get(urls["treats"] , headers=hdr, timeout=to).json()
            prof    = requests.get(urls["profile"], headers=hdr, timeout=to).json()
            break
        except requests.exceptions.ReadTimeout:
            if a == MAX_RETRY:
                raise
            st.warning(f"Nightscout slow – retry {a+1}/{MAX_RETRY}")
            time.sleep(1)

    edf = pd.DataFrame(entries)
    tdf = pd.DataFrame(treats)
    prof = prof[0] if prof else {}          # first profile doc or {}

    if not edf.empty:
        edf["time"] = pd.to_datetime(edf["date"], unit="ms", utc=True)
    if not tdf.empty:
        tdf["time"] = pd.to_datetime(tdf["created_at"], utc=True)

    return edf, tdf, prof

# ──────────  BASAL SCHEDULE  ──────────
def _extract_basal_segments(prof: dict):
    """
    Return list of segments with keys 'sec' (seconds from midnight) & 'rate'
    covering *either* 'store' or flat profile formats.
    """
    if not prof:
        return []

    # --- 1️⃣ “store” style  --------------------------------------------------
    if "store" in prof:
        name = prof.get("defaultProfile") or next(iter(prof["store"]))
        segs = prof["store"][name]["basal"]               # list
        return [
            {"sec": int(s.get("i", s.get("timeAsSeconds", 0))),
             "rate": float(s.get("v", s.get("value", 0)))}
            for s in segs
        ]

    # --- 2️⃣ flat style ------------------------------------------------------
    if "basalprofile" in prof:
        return [
            {"sec": int(s.get("i", s.get("timeAsSeconds", 0))),
             "rate": float(s.get("v", s.get("value", 0)))}    # v/value
            for s in prof["basalprofile"]
        ]

    return []   # unknown shape

def build_sched(profile, start_utc, end_utc):
    segs = _extract_basal_segments(profile)
    if not segs:
        return pd.DataFrame()

    # convert seg start (seconds) to absolute UTC on same date as start_utc
    base_midnight = start_utc.replace(hour=0, minute=0, second=0,
                                      microsecond=0)
    rows = []
    for s in segs:
        rows.append({
            "time": base_midnight + timedelta(seconds=s["sec"]),
            "rate": s["rate"]
        })
    df = pd.DataFrame(rows).sort_values("time")
    # append one extra row to extend to viewing window
    df = pd.concat([df, df.tail(1).assign(time=end_utc + timedelta(hours=1))])
    return df[(df["time"] >= start_utc) & (df["time"] <= end_utc)].reset_index(drop=True)

# ──────────  UI  ──────────
st.set_page_config("Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

today = date.today()
col1, col2, col3, col4 = st.columns(4)
with col1:  sd = st.date_input("Start date", today)
with col2:  st_time = st.time_input("Start time", dtime(0,0))
with col3:  ed = st.date_input("End date", today)
with col4:  et_time = st.time_input("End time", dtime(23,59))

start_dt = LOCAL_TZ.localize(datetime.combine(sd, st_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(ed, et_time)).astimezone(pytz.UTC)

st.write("Fetching data from Nightscout …")
entries_df, treats_df, profile = fetch_ns()
st.success("Data loaded.")

# ──────────  FILTER  ──────────
entries_df = entries_df[(entries_df["time"] >= start_dt) & (entries_df["time"] <= end_dt)]
treats_df  = treats_df[(treats_df["time"]  >= start_dt) & (treats_df["time"]  <= end_dt)]

# ──────────  PREP DATA  ──────────
entries_df["mmol"] = (entries_df["sgv"] / 18).round(1)

bolus_df = treats_df[treats_df["insulin"].notnull()]
smb_df   = bolus_df[bolus_df["enteredBy"].str.contains("SMB", na=False)]
man_df   = bolus_df[~bolus_df["enteredBy"].str.contains("SMB", na=False)]

carb_df  = treats_df[treats_df["carbs"].fillna(0) > 0]

basal_sched_df = build_sched(profile, start_dt, end_dt)
temp_df = treats_df[treats_df["eventType"] == "Temp Basal"]

# ──────────  PLOT  ──────────
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.02
)

fig.add_trace(go.Scatter(
    x=entries_df["time"], y=entries_df["mmol"],
    mode="lines", line=dict(color="green"),
    name="BG", hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}"
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=carb_df["time"], y=[15]*len(carb_df),
    mode="markers+text", marker=dict(color="orange", size=8),
    text=carb_df["carbs"].astype(int).astype(str)+" g", textposition="top center",
    name="Carbs"
), row=2, col=1)

fig.add_trace(go.Bar(
    x=man_df["time"], y=man_df["insulin"],
    marker_color="rgba(0,102,204,0.6)", name="Manual bolus"
), row=2, col=1)

fig.add_trace(go.Bar(
    x=smb_df["time"], y=smb_df["insulin"],
    marker_color="rgba(255,99,132,0.6)", name="SMB"
), row=2, col=1)

if not basal_sched_df.empty:
    fig.add_trace(go.Scatter(
        x=basal_sched_df["time"], y=basal_sched_df["rate"],
        mode="lines", line_shape="hv", line=dict(color="black"),
        name="Scheduled basal"
    ), row=3, col=1)

if not temp_df.empty:
    fig.add_trace(go.Bar(
        x=temp_df["time"], y=temp_df["rate"].fillna(0),
        marker_color="rgba(200,200,200,0.6)", name="Temp basal"
    ), row=3, col=1)

fig.update_yaxes(title_text="mmol/L", row=1, col=1, range=[2,15])
fig.update_yaxes(title_text="U / g",  row=2, col=1)
fig.update_yaxes(title_text="Basal U/h", row=3, col=1)

fig.update_layout(
    height=800, bargap=0.15, legend_orientation="h",
    margin=dict(t=40, r=20, b=40, l=60)
)

st.plotly_chart(fig, use_container_width=True)
