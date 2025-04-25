import os, time, pytz, requests, streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, time as dtime, timedelta

# ────────── CONFIG ──────────
NS_URL    = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET = st.secrets["API_SECRET"]
DAYS_BACK = 30
READ_TO   = 30          # seconds / request
MAX_RETRY = 2
LOCAL_TZ  = pytz.timezone(time.tzname[0])

# ────────── Nightscout download ──────────
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
    prof = prof[0] if prof else {}

    if not edf.empty:
        edf["time"] = pd.to_datetime(edf["date"], unit="ms", utc=True)
    if not tdf.empty:
        tdf["time"] = pd.to_datetime(tdf["created_at"], utc=True)

    return edf, tdf, prof

# ────────── Profile helpers ──────────
def _extract_basal_segments(prof: dict):
    # supports both “store” & flat formats
    if not prof: return []
    if "store" in prof:
        name = prof.get("defaultProfile") or next(iter(prof["store"]))
        segs = prof["store"][name]["basal"]
    elif "basalprofile" in prof:
        segs = prof["basalprofile"]
    else:
        return []

    return [
        {"sec": int(s.get("i", s.get("timeAsSeconds", 0))),
         "rate": float(s.get("v", s.get("value", 0)))}
        for s in segs
    ]

def build_sched(profile, start_utc, end_utc):
    segs = _extract_basal_segments(profile)
    if not segs:
        return pd.DataFrame()

    base_midnight = start_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [{"time": base_midnight + timedelta(seconds=s["sec"]), "rate": s["rate"]}
            for s in segs]
    df = pd.DataFrame(rows).sort_values("time")
    df = pd.concat([df, df.tail(1).assign(time=end_utc + timedelta(hours=1))])
    return df[(df["time"] >= start_utc) & (df["time"] <= end_utc)].reset_index(drop=True)

# ────────── UI ──────────
st.set_page_config("Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

today = date.today()
c1, c2, c3, c4 = st.columns(4)
with c1: sd = st.date_input("Start date", today)
with c2: st_time = st.time_input("Start time", dtime(0, 0))
with c3: ed = st.date_input("End date", today)
with c4: et_time = st.time_input("End time", dtime(23, 59))

start_dt = LOCAL_TZ.localize(datetime.combine(sd, st_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(ed, et_time)).astimezone(pytz.UTC)

st.write("Fetching data from Nightscout …")
entries_df, treats_df, profile = fetch_ns()
st.success("Data loaded.")

# ────────── Filter window ──────────
entries_df = entries_df[(entries_df["time"] >= start_dt) & (entries_df["time"] <= end_dt)]
treats_df  = treats_df[(treats_df["time"]  >= start_dt) & (treats_df["time"]  <= end_dt)]

entries_df["mmol"] = (entries_df["sgv"] / 18).round(1)

# split treatments
bolus_df = treats_df[treats_df["insulin"].notnull()]
smb_df   = bolus_df[bolus_df["enteredBy"].str.contains("smb", case=False, na=False)]
man_df   = bolus_df[~bolus_df["enteredBy"].str.contains("smb", case=False, na=False)]

carb_df  = treats_df[treats_df["carbs"].fillna(0) > 0]
temp_df  = treats_df[treats_df["eventType"] == "Temp Basal"]
basal_sched_df = build_sched(profile, start_dt, end_dt)

# ────────── Dynamic limits & positions ──────────
max_bolus = bolus_df["insulin"].max() if not bolus_df.empty else 0
bolus_ylim = max(1, max_bolus * 1.3)        # pad 30 %
carb_y      = bolus_ylim * 1.05
carb_sizes  = carb_df["carbs"].fillna(0).apply(lambda g: max(6, min(g*0.6, 30)))

# ────────── Plot ──────────
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    row_heights=[0.5, 0.28, 0.22], vertical_spacing=0.06
)

# 1️⃣  BG curve
fig.add_trace(go.Scatter(
    x=entries_df["time"], y=entries_df["mmol"],
    mode="lines", line=dict(color="green"),
    name="BG", hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}"
), row=1, col=1)

# 2️⃣  Bolus bars & carb dots
fig.add_trace(go.Bar(
    x=man_df["time"], y=man_df["insulin"],
    marker_color="rgba(0,102,204,0.7)", name="Manual bolus"
), row=2, col=1)

fig.add_trace(go.Bar(
    x=smb_df["time"], y=smb_df["insulin"],
    marker_color="rgba(255,99,132,0.7)", name="SMB"
), row=2, col=1)

fig.add_trace(go.Scatter(
    x=carb_df["time"], y=[carb_y]*len(carb_df),
    mode="markers+text",
    marker=dict(color="orange", size=carb_sizes),
    text=carb_df["carbs"].astype(int).astype(str)+" g",
    textposition="top center",
    name="Carbs"
), row=2, col=1)

# 3️⃣  Basal lines/bars
if not basal_sched_df.empty:
    fig.add_trace(go.Scatter(
        x=basal_sched_df["time"], y=basal_sched_df["rate"],
        mode="lines", line_shape="hv",
        line=dict(color="lightgrey", dash="dash"),
        name="Scheduled basal"
    ), row=3, col=1)

if not temp_df.empty:
    fig.add_trace(go.Bar(
        x=temp_df["time"], y=temp_df["rate"].fillna(0),
        marker_color="rgba(0,150,150,0.6)",
        name="Temp basal"
    ), row=3, col=1)

# ──────────  Axes & layout ──────────
fig.update_yaxes(title_text="mmol/L", row=1, col=1, range=[2, 15])
fig.update_yaxes(title_text="U / g",  row=2, col=1, range=[0, bolus_ylim])
fig.update_yaxes(title_text="Basal U/h", row=3, col=1)

fig.update_layout(
    height=850, bargap=0.15,
    legend_orientation="h", legend_y=-0.12,
    margin=dict(t=50, r=20, b=60, l=60)
)

st.plotly_chart(fig, use_container_width=True)
