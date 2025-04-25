###############################################################################
# Adriana Loop Dashboard – robust against empty treatments                     #
###############################################################################
from __future__ import annotations
import hashlib, pytz, requests, streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ───────────────────────────── Config ────────────────────────────────────────
st.set_page_config("Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL    = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET = st.secrets.get("API_SECRET", "")
HEADERS   = {"API-SECRET": NS_SECRET} if NS_SECRET else {}
LOCAL_TZ  = pytz.timezone(str(datetime.now().astimezone().tzinfo))

# ───────────────────────── Helper utils ──────────────────────────────────────
def to_ms(dt: datetime) -> int: return int(dt.timestamp() * 1000)

def _pick_time_col(df: pd.DataFrame) -> str | None:
    for col in ("created_at", "timestamp", "date"):
        if col in df.columns:
            return col
    return None                      # ⚡ graceful “no-column” fall-back

def _empty_treat_df() -> pd.DataFrame:              # ⚡ always used when empty
    cols = ["time", "insulin", "carbs",
            "eventType", "duration", "rate", "enteredBy"]
    return pd.DataFrame(columns=cols)

@st.cache_data(ttl=900, show_spinner=False)
def fetch_ns(start_ms: int, end_ms: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    days = max(1, int((end_ms - start_ms) / 86_400_000) + 1)
    q = f"find[date][$gte]={start_ms}&find[date][$lte]={end_ms}"

    def _get(path: str, count: int):
        url = f"{NS_URL}{path}?{q}&count={count}"
        return requests.get(url, headers=HEADERS, timeout=15).json()

    entries = _get("/api/v1/entries.json",    days * 300)
    treats  = _get("/api/v1/treatments.json", days * 400)
    profile = requests.get(f"{NS_URL}/api/v1/profile.json", headers=HEADERS,
                           timeout=15).json()[0]

    # ── CGM entries ──────────────────────────────────────────────────────────
    entries_df = (pd.json_normalize(entries)
                    .loc[:, ["date", "sgv"]]
                    .assign(time=lambda d: pd.to_datetime(d["date"], unit="ms")
                                              .dt.tz_localize("UTC")
                                              .dt.tz_convert(LOCAL_TZ),
                            mmol=lambda d: d["sgv"] / 18.0)
                    .drop(columns="date"))

    # ── Treatments (may be empty) ────────────────────────────────────────────
    treats_df  = pd.json_normalize(treats)
    if treats_df.empty or (t_col := _pick_time_col(treats_df)) is None:   # ⚡
        treats_df = _empty_treat_df()
    else:
        treats_df = treats_df.assign(
            time=pd.to_datetime(treats_df[t_col]).dt.tz_convert(LOCAL_TZ)
        )

    return entries_df, treats_df, profile

def build_sched(profile: dict, start: datetime, end: datetime) -> pd.DataFrame:
    try:
        segs = profile["store"]["basalprofile"]
    except Exception:
        return pd.DataFrame(columns=["time", "rate"])

    rows, d0 = [], LOCAL_TZ.localize(datetime.combine(start.date(), datetime.min.time()))
    for seg in segs:
        off  = timedelta(seconds=int(seg["i"]))
        rate = seg["value"]
        for t in pd.date_range(start=d0 + off, end=end + timedelta(days=1),
                               freq="24h", tz=LOCAL_TZ):
            if start <= t <= end:
                rows.append((t, rate))
    return pd.DataFrame(rows, columns=["time", "rate"])

# ───────────────────────────── UI widgets ────────────────────────────────────
today = datetime.now(LOCAL_TZ).date()
c1, c2, c3, c4 = st.columns(4)
with c1: start_d = st.date_input("Start", today)
with c2: start_t = st.time_input("Time",  datetime.min.time())
with c3: end_d   = st.date_input("End",   today)
with c4: end_t   = st.time_input(" ",     datetime.max.time().replace(microsecond=0))

start_dt = LOCAL_TZ.localize(datetime.combine(start_d, start_t)).astimezone(pytz.UTC)
end_dt   = LOCAL_TZ.localize(datetime.combine(end_d, end_t)).astimezone(pytz.UTC)
start_ms, end_ms = to_ms(start_dt), to_ms(end_dt)

entries_df, treats_df, profile = fetch_ns(start_ms, end_ms)

# ───────────────────────── Split treatment types ─────────────────────────────
bolus_df = treats_df[treats_df["insulin"].notnull()]
smb_df   = bolus_df[bolus_df["enteredBy"].str.contains("SMB", na=False)]
man_df   = bolus_df.drop(smb_df.index)
carb_df  = treats_df[treats_df["carbs"].notnull() & (treats_df["carbs"] > 0)]
temp_df  = treats_df[(treats_df["eventType"] == "Temp Basal") & (treats_df["duration"] > 0)]

basal_sched = build_sched(profile, start_dt, end_dt)

# ───────────────────────────── Plot ──────────────────────────────────────────
fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                    row_heights=[0.45, 0.25, 0.30], vertical_spacing=0.05)

fig.add_trace(go.Scatter(x=entries_df.time, y=entries_df.mmol,
                         mode="lines", name="BG", line=dict(color="green", width=2)),
              row=1, col=1)

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
                             marker=dict(size=carb_df.carbs*2, color="orange", opacity=.7),
                             text=carb_df.carbs.astype(int).astype(str),
                             textposition="top center", showlegend=False), row=2, col=1)

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
