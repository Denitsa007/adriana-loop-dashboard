# ──────────────────────────────────────────────────────────────────────────────
#  Adriana Loop Dashboard  •  Streamlit  •  Nightscout ➜ 3-panel Tidepool-style
# ──────────────────────────────────────────────────────────────────────────────
import os, requests, json, math
from datetime import datetime, date, time, timedelta, timezone
import pytz, pandas as pd, numpy as np, streamlit as st
from plotly.subplots import make_subplots
import plotly.graph_objects as go

# ── CONFIG ────────────────────────────────────────────────────────────────────
NS_URL      = st.secrets.get("NIGHTSCOUT_URL"     , "").rstrip("/")
API_SECRET  = st.secrets.get("API_SECRET", "")
HEADERS     = {"api-secret": API_SECRET} if API_SECRET else {}
LOCAL_TZ    = pytz.timezone(str(st.secrets.get("LOCAL_TZ","UTC")))  # e.g. "Europe/Berlin"
REQ_TIMEOUT = 15                                                    # seconds

# ── HELPERS ───────────────────────────────────────────────────────────────────
def _pick_time_col(df: pd.DataFrame) -> str:
    for c in ("time","date","dateString","created_at","mills","timestamp"):
        if c in df.columns: return c
    raise KeyError("No timestamp column found")

def _to_dt(s):
    # accepts ms-epoch or iso8601
    try:   return pd.to_datetime(s, unit="ms", utc=True)
    except Exception:
        return pd.to_datetime(s,             utc=True, errors="coerce")

@st.cache_data(ttl=300, show_spinner=False)
def fetch_ns(since_ms: int, until_ms: int):
    # entries (CGM)
    entries_url = f"{NS_URL}/api/v1/entries.json"
    params_e    = {"find[date][$gte]":since_ms, "find[date][$lte]":until_ms}
    r           = requests.get(entries_url, params=params_e, headers=HEADERS, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    e_df        = pd.DataFrame(r.json())
    if e_df.empty: e_df = pd.DataFrame(columns=["sgv","time"])
    e_df        = ( e_df.assign(time=lambda d:_to_dt(d[_pick_time_col(d)]))
                         .dropna(subset=["time"])
                         .sort_values("time") )

    # treatments
    treats_url  = f"{NS_URL}/api/v1/treatments.json"
    params_t    = {"find[created_at][$gte]":since_ms, "find[created_at][$lte]":until_ms}
    r           = requests.get(treats_url, params=params_t, headers=HEADERS, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    t_df        = pd.DataFrame(r.json())
    if t_df.empty: t_df = pd.DataFrame(columns=["eventType","time"])
    t_df        = ( t_df.assign(time=lambda d:_to_dt(d[_pick_time_col(d)]))
                         .dropna(subset=["time"])
                         .sort_values("time") )

    # latest profile (for scheduled basal)
    prof_url    = f"{NS_URL}/api/v1/profile.json?count=1"
    prof        = requests.get(prof_url, headers=HEADERS, timeout=REQ_TIMEOUT).json()[0]
    return e_df, t_df, prof

def build_sched(profile, start_dt, end_dt):
    rows = []
    for seg in profile["store"]["Default"]["basalprofile"]:        # "Default" profile name
        rate = seg["value"]
        seg_start = datetime.combine(start_dt.date(), time()) + timedelta(minutes=seg["time"]//60)
        while seg_start < end_dt:
            seg_end = seg_start + timedelta(minutes=profile["store"]["Default"]["basalprofile"][(seg["i"]+1)%len(profile["store"]["Default"]["basalprofile"])]["time"]//60)
            rows.append({"time":seg_start.replace(tzinfo=timezone.utc),"rate":rate})
            seg_start = seg_end
    return pd.DataFrame(rows)

# ── UI  – DATE PICKERS ────────────────────────────────────────────────────────
st.set_page_config(page_title="Adriana Dashboard", layout="wide")
st.title("Adriana Loop Dashboard (MVP)")

col1, col2 = st.columns(2)
with col1:
    sel_date = st.date_input("Date", date.today())
with col2:
    tz_now   = datetime.now(LOCAL_TZ).time()
    start_t  = st.time_input("Start time", time(0,0))
    end_t    = st.time_input("End time"  , time(23,59,59))

start_dt = LOCAL_TZ.localize(datetime.combine(sel_date, start_t)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(sel_date, end_t  )).astimezone(pytz.UTC)
start_ms, end_ms = int(start_dt.timestamp()*1000), int(end_dt.timestamp()*1000)

st.markdown("Fetching Nightscout …")
entries_df, treats_df, profile = fetch_ns(start_ms, end_ms)

# ── DATAFRAMES ───────────────────────────────────────────────────────────────
bg_df    = entries_df[["time","sgv"]].dropna()

bol_df   = treats_df.loc[treats_df["eventType"].isin(("Correction Bolus","Bolus"))].copy()
bol_df["insulin"] = bol_df["insulin"].fillna(bol_df.get("amount",np.nan))
bol_df   = bol_df.dropna(subset=["insulin"])
bol_df["kind"]    = np.where(bol_df.get("enteredBy","").str.contains("smb",case=False),"SMB","Manual")

carb_df  = treats_df.loc[treats_df["carbs"].notnull() & (treats_df["carbs"]>0), ["time","carbs"]]

temp_df  = treats_df.loc[treats_df["eventType"]=="Temp Basal"].copy()
temp_df  = temp_df.assign(rate=lambda d:d["rate"].fillna(d["absolute"])).dropna(subset=["rate"]).sort_values("time")

sched_df = build_sched(profile, start_dt, end_dt)

# ── PLOTLY SUB-PLOTS ─────────────────────────────────────────────────────────
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
    row_heights=[0.4,0.3,0.3], specs=[[{}],[{}],[{}]]
)

# 1️⃣ BG line
fig.add_trace(go.Scatter(
    x=bg_df["time"], y=bg_df["sgv"]/18.0,   # mg/dl ➞ mmol/L
    mode="lines+markers", line=dict(color="mediumseagreen"),
    hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}", name="BG"
), row=1,col=1)

# 2️⃣ Bolus & Carbs
for kind,color in [("Manual","royalblue"),("SMB","lightskyblue")]:
    sub = bol_df.loc[bol_df["kind"]==kind]
    if not sub.empty:
        fig.add_trace(go.Bar(
            x=sub["time"], y=sub["insulin"], name=kind,
            marker_color=color, hovertemplate=f"{kind}: "+"%{y:.2f} U<br>%{x|%H:%M}"
        ), row=2, col=1)

if not carb_df.empty:
    fig.add_trace(go.Scatter(
        x=carb_df["time"], y=[bol_df["insulin"].max()*1.05]*len(carb_df),
        mode="markers+text",
        marker=dict(size=np.clip(carb_df["carbs"],5,30), color="sandybrown", line=dict(width=1,color="black")),
        text=carb_df["carbs"].astype(int),
        textposition="top center",
        name="Carbs",
        hovertemplate="%{text} g<br>%{x|%H:%M}"
    ), row=2, col=1)

# 3️⃣ Basal
# scheduled
fig.add_trace(go.Scatter(
    x=sched_df["time"], y=sched_df["rate"],
    mode="lines", line=dict(color="lightgrey", width=1, dash="dash"),
    name="Scheduled basal", hovertemplate="%{y:.2f} U/h<br>%{x|%H:%M}"
), row=3,col=1)

# temp basal as filled area
if not temp_df.empty:
    fig.add_trace(go.Scatter(
        x=temp_df["time"], y=temp_df["rate"],
        mode="lines", line=dict(color="tomato"),
        fill="tozeroy", fillcolor="rgba(255,99,71,0.3)",
        name="Temp basal", hovertemplate="%{y:.2f} U/h<br>%{x|%H:%M}"
    ), row=3,col=1)

# ── LAYOUT ───────────────────────────────────────────────────────────────────
max_bolus = bol_df["insulin"].max() if not bol_df.empty else 1
fig.update_yaxes(title_text="mmol/L", row=1,col=1)
fig.update_yaxes(title_text="Insulin U", range=[0,max_bolus*1.3], row=2,col=1)
fig.update_yaxes(title_text="Basal U/h", row=3,col=1)
fig.update_layout(
    height=800, bargap=0.15, legend=dict(orientation="h",y=1.02,x=0),
    hovermode="x unified", template="plotly_white"
)

st.plotly_chart(fig, use_container_width=True)
