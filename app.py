###############################################################################
# Adriana Loop Dashboard – 2025-04-25
###############################################################################
from __future__ import annotations
import hashlib, pytz, requests, streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ───────────────────────────── App settings ──────────────────────────────────
st.set_page_config("Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL      = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET   = st.secrets.get("API_SECRET", "")
HEADERS     = {"API-SECRET": NS_SECRET} if NS_SECRET else {}
LOCAL_TZ    = pytz.timezone(str(datetime.now().astimezone().tzinfo))

# ─────────────────────────── Helper utilities ────────────────────────────────
def to_ms(dt: datetime) -> int: return int(dt.timestamp() * 1000)
def hash_url(url: str) -> str:  return hashlib.md5(url.encode()).hexdigest()

def _pick_time_col(df: pd.DataFrame) -> str:
    """Return the first existing timestamp column in treatments."""
    for col in ("created_at", "timestamp", "date"):      # ordered preference
        if col in df.columns:
            return col
    raise KeyError("No timestamp column found in treatments JSON")

@st.cache_data(ttl=900, show_spinner=False)
def fetch_ns(start_ms: int, end_ms: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (entries_df, treatments_df, profile_dict) for the given epoch-ms window."""
    days = max(1, int((end_ms - start_ms) / 86_400_000) + 1)
    cgm_cnt, trt_cnt = days * 300, days * 400
    q = f"find[date][$gte]={start_ms}&find[date][$lte]={end_ms}"

    def _get(path: str, count: int | None = None):
        url = f"{NS_URL}{path}?{q}" + (f"&count={count}" if count else "")
        return requests.get(url, headers=HEADERS, timeout=15).json()

    entries  = _get("/api/v1/entries.json",    cgm_cnt)
    treats   = _get("/api/v1/treatments.json", trt_cnt)
    profile  = _get("/api/v1/profile.json")[0]

    # CGM entries → mmol
    entries_df = (pd.json_normalize(entries)
                    .loc[:, ["date", "sgv"]]
                    .assign(time=lambda d: pd.to_datetime(d["date"], unit="ms")
                                              .dt.tz_localize("UTC")
                                              .dt.tz_convert(LOCAL_TZ),
                            mmol=lambda d: d["sgv"] / 18.0)
                    .drop(columns="date"))

    # Treatments
    treats_df  = pd.json_normalize(treats)
    t_col      = _pick_time_col(treats_df)
    treats_df  = treats_df.assign(
        time=pd.to_datetime(treats_df[t_col]).dt.tz_convert(LOCAL_TZ)
    )

    return entries_df, treats_df, profile

def build_sched(profile: dict, start: datetime, end: datetime) -> pd.DataFrame:
    """Expand basalprofile from profile dict into a (time, rate) series."""
    try:
        segs = profile["store"]["basalprofile"]
    except Exception:
        return pd.DataFrame(columns=["time", "rate"])

    rows, day0 = [], LOCAL_TZ.localize(datetime.combine(start.date(), datetime.min.time()))
    for seg in segs:
        off  = timedelta(seconds=int(seg["i"]))
        rate = seg["value"]
        times = pd.date_range(start=day0 + off, end=end + timedelta(days=1),
                              freq="24h", tz=LOCAL_TZ)
        for t in times:
            if start <= t <= end:
                rows.append((t, rate))
    return pd.DataFrame(rows, columns=["time", "rate"])

def make_canvas() -> go.Figure:
    return make_subplots(rows=3, cols=1, shared_xaxes=True,
                         vertical_spacing=0.05, row_heights=[0.45, 0.25, 0.30])

# ───────────────────────────── Widgets ───────────────────────────────────────
today = datetime.now(LOCAL_TZ).date()
c1, c2, c3, c4 = st.columns(4)
with c1: start_d = st.date_input("Start", today)
with c2: start_t = st.time_input("Time",  datetime.min.time())
with c3: end_d   = st.date_input("End",   today)
with c4: end_t   = st.time_input(" ",     datetime.max.time().replace(microsecond=0))

if st.button("Apply / Refresh"): st.session_state["doit"] = True

start_dt = LOCAL_TZ.localize(datetime.combine(start_d, start_t)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(end_d, end_t)).astimezone(pytz.UTC)
start_ms, end_ms = to_ms(start_dt), to_ms(end_dt)

# ─────────────────────────── Data fetch ──────────────────────────────────────
st.caption("Contacting Nightscout …")
entries_df, treats_df, profile = fetch_ns(start_ms, end_ms)
st.success("Nightscout slice loaded.")

# derive bolus / carb / basal slices
bolus_df = treats_df[treats_df["insulin"].notnull()]
smb_df   = bolus_df[bolus_df.get("enteredBy","").str.contains("SMB", na=False)]
man_df   = bolus_df.drop(smb_df.index)
carb_df  = treats_df[treats_df["carbs"].notnull() & (treats_df["carbs"] > 0)]

basal_sched = build_sched(profile, start_dt, end_dt)
temp_df     = treats_df[(treats_df["eventType"] == "Temp Basal") & (treats_df["duration"] > 0)]

# ───────────────────────────── Plot ──────────────────────────────────────────
fig = make_canvas()

# Row 1: BG
fig.add_trace(go.Scatter(x=entries_df.time, y=entries_df.mmol, mode="lines",
                         name="BG", line=dict(color="green", width=2)), row=1, col=1)

# Row 2: insulin bars + carbs
y_max = max((bolus_df["insulin"].max() or 0) * 1.3, 1)
fig.update_yaxes(range=[0, y_max], row=2, col=1, title="U / g")

fig.add_trace(go.Bar(x=man_df.time, y=man_df.insulin, name="Manual",
                     marker_color="rgb(0,123,255)"), row=2, col=1)
fig.add_trace(go.Bar(x=smb_df.time, y=smb_df.insulin, name="SMB",
                     marker_color="rgb(255,99,132)"), row=2, col=1)

if not carb_df.empty:
    fig.add_trace(go.Scatter(x=carb_df.time,
                             y=[y_max*0.9]*len(carb_df),
                             mode="markers+text",
                             marker=dict(size=carb_df.carbs*2, color="orange", opacity=.8),
                             text=carb_df.carbs.astype(int).astype(str),
                             textposition="top center", showlegend=False), row=2, col=1)

# Row 3: basal schedule + temp
if not basal_sched.empty:
    fig.add_trace(go.Scatter(x=basal_sched.time, y=basal_sched.rate,
                             mode="lines", name="Scheduled",
                             line=dict(color="lightgrey", dash="dash")), row=3, col=1)
if not temp_df.empty:
    fig.add_trace(go.Scatter(x=temp_df.time, y=temp_df.rate,
                             mode="lines", fill="tozeroy", name="Temp basal",
                             line=dict(color="rgb(102,0,204)", width=2)), row=3, col=1)
fig.update_yaxes(row=3, col=1, title="U/hr")

fig.update_layout(height=750, hovermode="x unified",
                  legend_orientation="h",
                  margin=dict(t=40, b=25, l=40, r=10))
st.plotly_chart(fig, use_container_width=True)
