# ──────────────────────────────────────────────────────────────────────────────
# Adriana's Loop Dashboard  ·  Streamlit (MVP)
# ──────────────────────────────────────────────────────────────────────────────
import os, json, requests, pytz, numpy as np, pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
# ──────────────────────────────────────────────────────────────────────────────
NS_URL    = st.secrets.get("NIGHTSCOUT_URL",  os.getenv("NIGHTSCOUT_URL",  ""))
API_TOKEN = st.secrets.get("API_SECRET",      os.getenv("API_SECRET",      ""))
HEADERS   = {"api-secret": API_TOKEN} if API_TOKEN else {}
LOCAL_TZ  = pytz.timezone(str(datetime.now().astimezone().tzinfo))
# ───────────────────────────────────────── helpers ────────────────────────────
REQ_TIMEOUT = 15   # seconds

def _empty_treat_df() -> pd.DataFrame:
    """Return an empty Treatment DF with all the columns we always use."""
    cols = ["time", "carbs", "insulin", "eventType"]
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}).astype(
        {"eventType":"object"}
    )

def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee required cols exist even if the Nightscout JSON lacks them."""
    needed = {"time": "datetime64[ns, UTC]",
              "carbs": "float64",
              "insulin": "float64",
              "eventType": "object"}
    for col, dtype in needed.items():
        if col not in df.columns:
            df[col] = pd.Series(dtype=dtype)
    return df.astype(needed)

@st.cache_data(ttl=900, show_spinner=False)
def fetch_ns(start_ms: int, end_ms: int):
    """Fetch NS /entries & /treatments between two epoch-ms timestamps."""
    try:
        entries = requests.get(
            f"{NS_URL}/api/v1/entries.json",
            params=dict(find=f'{{"date":{{"$gte":{start_ms},"$lte":{end_ms}}}}}',
                        count=0,  # return all that match
                        sort='date'),
            headers=HEADERS, timeout=REQ_TIMEOUT).json()

        treats  = requests.get(
            f"{NS_URL}/api/v1/treatments.json",
            params=dict(find=f'{{"created_at":{{"$gte":{start_ms},"$lte":{end_ms}}}}}',
                        count=0,
                        sort='created_at'),
            headers=HEADERS, timeout=REQ_TIMEOUT).json()
    except requests.exceptions.RequestException as e:
        st.error(f"Nightscout error ➜ {e}")
        return (pd.DataFrame(), _empty_treat_df(), {})

    # Entries ────────────────────────────────────────────────────────────────
    entries_df = (pd.json_normalize(entries)
                    .rename(columns={"date": "time", "sgv": "bg"})
                    .assign(time=lambda df: pd.to_datetime(df["time"], unit="ms",
                                                           utc=True))
                    .filter(["time", "bg"])
                    .sort_values("time"))

    # Treatments ─────────────────────────────────────────────────────────────
    if treats:
        treats_df = (pd.json_normalize(treats)
                       .rename(columns={"created_at": "time"})
                       .assign(time=lambda df: pd.to_datetime(df["time"], utc=True)))
        treats_df = _ensure_cols(treats_df)
    else:
        treats_df = _empty_treat_df()

    # Profile  (only need basal schedule)  – fetch the most recent profile doc
    prof_url = f"{NS_URL}/api/v1/profile"
    try:
        profile = requests.get(prof_url, headers=HEADERS,
                               timeout=REQ_TIMEOUT).json()[0]
    except Exception:
        profile = {}

    return entries_df, treats_df, profile

def build_sched(profile: dict, start: datetime, end: datetime) -> pd.DataFrame:
    """Return a DF of scheduled basal between start & end (UTC)."""
    if not profile:
        return pd.DataFrame(columns=["time", "basal"])

    base   = start.replace(hour=0, minute=0, second=0, microsecond=0)
    sched  = []
    for seg in profile.get("store", {}).get("basalprofile", []):
        seg_time = base + timedelta(minutes=int(seg.get("i", 0)))
        sched.append({"time": seg_time, "basal": float(seg["v"])})
    df = pd.DataFrame(sched).set_index("time").sort_index()

    # forward-fill across the whole requested window
    df = df.reindex(pd.date_range(start, end, freq="5min", tz="UTC")).ffill().reset_index()
    df.columns = ["time", "basal"]
    return df

# ───────────────────────────────────────── Sidebar (date pickers) ─────────────
st.title("Adriana’s Loop Dashboard  (MVP)")

today = datetime.now().date()
start_date = st.sidebar.date_input("Start date", value=today)
end_date   = st.sidebar.date_input("End date",   value=today)
start_time = st.sidebar.time_input("Start time", value=datetime.min.time())
end_time   = st.sidebar.time_input("End time",   value=datetime.max.time())

start_dt = LOCAL_TZ.localize(datetime.combine(start_date, start_time)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(end_date,   end_time  )).astimezone(pytz.UTC)
start_ms, end_ms = int(start_dt.timestamp()*1000), int(end_dt.timestamp()*1000)

st.write(f"⏳ Fetching Nightscout data …")
entries_df, treats_df, profile = fetch_ns(start_ms, end_ms)
if entries_df.empty:
    st.stop()

# ───────────────────────────────── Plotly figure (BG) ─────────────────────────
fig_bg = go.Figure()
fig_bg.add_scatter(x=entries_df["time"], y=entries_df["bg"],
                   mode="lines", name="BG (mg/dL)",
                   line=dict(color="#0974e3", width=2))
fig_bg.update_layout(title="Blood Glucose",
                     yaxis_title="mg/dL",
                     margin=dict(t=40, b=40, l=60, r=20),
                     height=250)

# ───────────────────── Basal schedule & temp basal (area) ─────────────────────
basal_sched_df = build_sched(profile, start_dt, end_dt)
fig_basal = go.Figure()
fig_basal.add_scatter(x=basal_sched_df["time"], y=basal_sched_df["basal"],
                      fill="tozeroy", mode="lines", name="Scheduled basal",
                      line=dict(color="lightgrey", width=1, dash="dash"))
# temp basal bars
temp_df = treats_df[treats_df["eventType"] == "Temp Basal"]
if not temp_df.empty:
    fig_basal.add_scatter(x=temp_df["time"], y=temp_df["insulin"],
                          mode="lines", fill="tozeroy",
                          name="Temp basal", line=dict(color="#ffa600"))
fig_basal.update_layout(title="Basal insulin (U/h)", height=200,
                        margin=dict(t=40, b=40, l=60, r=20))

# ─────────────── Bolus/SMB bars & carb circles (variable marker) ─────────────
bol_df  = treats_df[treats_df["insulin"].notna() & (treats_df["insulin"] > 0)]
carb_df = treats_df[treats_df["carbs"].notna()   & (treats_df["carbs"]   > 0)]

fig_bolus = go.Figure()
if not bol_df.empty:
    # separate SMB vs manual according to Nightscout type / enteredBy
    smbs = bol_df[bol_df["eventType"].str.contains("Bolus", na=False) &
                  bol_df["enteredBy"].str.contains("SMB", na=False)]
    man  = bol_df.drop(smbs.index)

    if not smbs.empty:
        fig_bolus.add_bar(x=smbs["time"], y=smbs["insulin"],
                          name="SMB", marker_color="#ff976d")
    if not man.empty:
        fig_bolus.add_bar(x=man["time"],  y=man["insulin"],
                          name="Manual bolus", marker_color="#4c78a8")

# carbs as circles proportional to amount
if not carb_df.empty:
    fig_bolus.add_scatter(
        x=carb_df["time"], y=[0]*len(carb_df),
        mode="markers+text", text=[str(int(v)) for v in carb_df["carbs"]],
        textposition="top center",
        marker=dict(size=carb_df["carbs"]*0.8, color="#7ddc1f", opacity=0.8),
        showlegend=True, name="Carbs (g)"
    )
fig_bolus.update_layout(
    title="Bolus insulin (U) & Carbs (g)",
    barmode="stack", height=220,
    yaxis=dict(title="Units", rangemode="tozero", automargin=True),
    margin=dict(t=40, b=40, l=60, r=20)
)

# ─────────────────────────────── Show the charts ─────────────────────────────
st.plotly_chart(fig_bg,   use_container_width=True)
st.plotly_chart(fig_bolus, use_container_width=True)
st.plotly_chart(fig_basal, use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────
