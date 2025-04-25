#  Adriana – Loop dashboard  (full file)
import json, requests, pytz, streamlit as st
from datetime import datetime, date, time, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

NS_URL      = "https://adriana007.eu.nightscoutpro.com"
NS_SECRET   = st.secrets.get("NS_SECRET", "")            # ← add in ⚙️-Secrets if required
LOCAL_TZ    = pytz.timezone("Europe/Berlin")
TIMEOUT     = (6, 15)

# ───────────────── helpers ──────────────────────────────────────────────────
def _to_utc(ts):
    if isinstance(ts, int):
        return pd.to_datetime(ts, unit="ms", utc=True)
    return pd.to_datetime(ts.rstrip("Z"), utc=True, errors="coerce")

def _pick_time(df):
    for col in ("date", "dateString", "created_at", "time"):
        if col in df.columns:
            return df[col].apply(_to_utc)
    raise KeyError("no timestamp column in JSON")

def _as_df(payload):
    """return always a DataFrame, even for scalar / empty JSON"""
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()          # fallback for '', None …

@st.cache_data(show_spinner=False, ttl=600)
def fetch_ns(start_ms, end_ms):
    params = {
        "find[date][$gte]": start_ms,
        "find[date][$lte]": end_ms,
        "token": NS_SECRET or None
    }

    def _get(path):
        r = requests.get(f"{NS_URL}{path}", params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"Nightscout error {r.status_code}: {r.text[:120]}")
        return _as_df(r.json())

    entries_df  = _get("/api/v1/entries.json").assign(time=lambda d: _pick_time(d)).sort_values("time")
    treats_df   = _get("/api/v1/treatments.json")
    if not treats_df.empty:
        treats_df["time"] = _pick_time(treats_df)

    profile     = _get("/api/v1/profile.json").iloc[0] if not _get("/api/v1/profile.json").empty else {}

    return entries_df, treats_df, profile

def build_sched(profile, start, end):
    rows, base = [], pd.Timestamp(start.floor("d"), tz=pytz.UTC)
    while base < end:
        for seg in profile.get("store", {}).get("basalprofile", []):
            rows.append({"time": base + timedelta(minutes=int(seg["i"])),
                         "rate": seg["rate"]})
        base += pd.Timedelta("1d")
    return pd.DataFrame(rows).query("time>=@start & time<=@end").sort_values("time")

# ───────────────── UI ───────────────────────────────────────────────────────
st.set_page_config("Adriana – Loop", layout="wide")
st.markdown("### Adriana’s Looping Dashboard")

c1, c2 = st.columns(2)
with c1:
    s_date = st.date_input("Start date", date.today())
    s_time = st.time_input("Start time", time(0,0))
with c2:
    e_date = st.date_input("End date",   date.today())
    e_time = st.time_input("End time",   time(23,59))

start_dt = LOCAL_TZ.localize(datetime.combine(s_date, s_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(e_date, e_time)).astimezone(pytz.UTC)
start_ms, end_ms = int(start_dt.timestamp()*1000), int(end_dt.timestamp()*1000)

with st.spinner("Fetching Nightscout…"):
    entries_df, treats_df, profile = fetch_ns(start_ms, end_ms)

bg_df   = entries_df
bol_df  = treats_df.query("eventType=='Correction Bolus'").assign(units=lambda d: d["insulin"])
smb_df  = treats_df.query("eventType=='Bolus'").assign(units=lambda d: d["insulin"])
carb_df = treats_df.query("carbs.notnull() & carbs>0")
temp_df = treats_df.query("eventType=='Temp Basal'")
basal_sched_df = build_sched(profile, start_dt, end_dt)

fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                    row_heights=[0.45,0.27,0.28])

# BG
fig.add_trace(go.Scatter(x=bg_df["time"], y=bg_df["sgv"]/18,
                         mode="lines+markers", name="BG",
                         hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}"), 1,1)

# Bolus / SMB / Carbs
if not bol_df.empty:
    fig.add_trace(go.Bar(x=bol_df["time"], y=bol_df["units"], name="Manual bolus",
                         marker_color="#1f77b4"), 2,1)
if not smb_df.empty:
    fig.add_trace(go.Bar(x=smb_df["time"], y=smb_df["units"], name="SMB",
                         marker_color="#ff7f0e"), 2,1)
if not carb_df.empty:
    fig.add_trace(go.Scatter(x=carb_df["time"], y=[0.05]*len(carb_df),
                             mode="markers+text",
                             marker=dict(size=carb_df["carbs"], color="green", opacity=.7),
                             text=[f"{c} g" for c in carb_df["carbs"]],
                             textposition="top center", showlegend=False), 2,1)

# Basal
fig.add_trace(go.Scatter(x=basal_sched_df["time"], y=basal_sched_df["rate"],
                         mode="lines", name="Scheduled basal",
                         line=dict(color="grey", width=1, dash="dash")), 3,1)
if not temp_df.empty:
    fig.add_trace(go.Scatter(x=temp_df["time"], y=temp_df["rate"],
                             mode="lines", name="Temp basal", fill="tozeroy",
                             line=dict(color="#9467bd")), 3,1)

fig.update_yaxes(title="mmol/L", row=1,col=1)
fig.update_yaxes(title="U / g",  row=2,col=1, rangemode="tozero")
fig.update_yaxes(title="U/h",    row=3,col=1, rangemode="tozero")
fig.update_layout(height=740, bargap=.2, legend_orientation="h",
                  hovermode="x unified", template="simple_white",
                  margin=dict(t=48,b=20,l=40,r=40))

st.plotly_chart(fig, use_container_width=True)
