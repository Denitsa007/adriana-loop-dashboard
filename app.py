# ────────────────────────────────────────────
#  Adriana Loop Dashboard  ·  Streamlit 1.33
#  one-file version with robust Nightscout IO
# ────────────────────────────────────────────
#  Requirements (in requirements.txt):
#  streamlit pandas plotly requests python-dateutil pytz urllib3
# ────────────────────────────────────────────

import os, math, json, time
import streamlit as st
import pandas as pd
import plotly.express as px
import pytz
from datetime import datetime, timedelta, date
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry   import Retry


# ╭──────────────────────── configuration ───────────────────────╮
NS_URL      = st.secrets.get("NIGHTSCOUT_URL" , "").rstrip("/")
NS_URL      = NS_URL or os.getenv("NIGHTSCOUT_URL")   # fallback
API_SECRET  = st.secrets.get("API_SECRET"     , "") or os.getenv("API_SECRET", "")
LOCAL_TZ    = st.secrets.get("LOCAL_TZ"       , "Europe/Berlin")

HEADERS     = {"api-secret": API_SECRET} if API_SECRET else {}
REQ_TIMEOUT = 45           # seconds per HTTP request
BLOCK_H     = 12           # hours fetched per sub-request
MAX_RETRIES = 3            # automatic retries per sub-request
# ╰──────────────────────────────────────────────────────────────╯


# ╭─ helpers ────────────────────────────────────────────────────╮
def _session_with_retries():
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=1.5,    # 0 s, 1.5 s, 3 s, …
        status_forcelist=[502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry)
    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://",  adapter)
    return s


def _fetch_chunk(endpoint: str, start_ms: int, end_ms: int) -> list[dict]:
    """Download one small time slice from Nightscout."""
    url   = f"{NS_URL}{endpoint}"
    parms = {"find[date][$gte]": start_ms,
             "find[date][$lte]": end_ms,
             "count": 10000,
             "_ttl": 0}            # disable server cache
    r = SESSION.get(url, params=parms, headers=HEADERS, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _iter_timeblocks(start_ms: int, end_ms: int, block_h=BLOCK_H):
    block_ms = block_h * 3600_000
    cur = start_ms
    while cur < end_ms:
        yield cur, min(cur + block_ms - 1, end_ms)
        cur += block_ms


def fetch_ns(start_ms: int, end_ms: int):
    """Robust Nightscout fetch that streams data in 12-h blocks."""
    entries, treats = [], []

    for s, e in _iter_timeblocks(start_ms, end_ms):
        if st.session_state.get("stop_fetch"):               # user cancelled?
            break
        entries += _fetch_chunk("/api/v1/entries.json",     s, e)
        treats  += _fetch_chunk("/api/v1/treatments.json",  s, e)

    # ── convert to DataFrames ───────────────────────────
    entries_df = (pd.DataFrame(entries)
                    .assign(time=lambda d: pd.to_datetime(d["date"], unit="ms"))
                    .sort_values("time"))
    treats_df  = (pd.DataFrame(treats)
                    .assign(time=lambda d: pd.to_datetime(d["created_at"]))
                    .sort_values("time"))

    # ── profile (single call, small) ─────────────────────
    prof_url = f"{NS_URL}/api/v1/profile"
    prof_js  = SESSION.get(prof_url, headers=HEADERS, timeout=REQ_TIMEOUT).json()
    profile  = prof_js[0] if prof_js else {}

    return entries_df, treats_df, profile
# ╰──────────────────────────────────────────────────────────────╯


# ╭─ UI: date range selector ───────────────────────────────────╮
st.set_page_config(page_title="Adriana Loop dashboard", layout="wide")
st.title("📊  Adriana CGM / Loop dashboard")

local_tz = pytz.timezone(LOCAL_TZ)
today    = date.today()

col1, col2, col3 = st.columns(3)
with col1: start_date = st.date_input("Start date", today - timedelta(days=1))
with col2: end_date   = st.date_input("End date",   today)
with col3:
    if st.button("Reload"):
        st.session_state.pop("ns_cache", None)   # clear cache

# transform to UTC ms
start_dt  = local_tz.localize(datetime.combine(start_date, datetime.min.time()))
end_dt    = local_tz.localize(datetime.combine(end_date,   datetime.max.time()))
start_ms  = int(start_dt.timestamp()*1000)
end_ms    = int(end_dt.timestamp()*1000)


# ╭─ fetch data (cached) ───────────────────────────────────────╮
SESSION = _session_with_retries()

@st.cache_data(ttl=300, show_spinner="⏳  Contacting Nightscout…")
def _cached_fetch(s_ms, e_ms):
    return fetch_ns(s_ms, e_ms)

try:
    entries_df, treats_df, profile = _cached_fetch(start_ms, end_ms)
except requests.exceptions.RequestException as err:
    st.error(f"Nightscout error → {err}")
    st.stop()

if entries_df.empty:
    st.warning("No CGM data in selected range.")
    st.stop()

# ╭─ split treatment types ─────────────────────────────────────╮
bol_df  = treats_df.query("eventType in ['Correction Bolus', 'Bolus', 'Meal Bolus']")
smb_df  = treats_df.query("eventType == 'Note' and notes == 'SMB'")   # adapt if Trio tags SMBs differently
carb_df = treats_df.query("carbs.notnull() & (carbs > 0)")
temp_df = treats_df.query("eventType == 'Temp Basal'")

# ╭─ BG chart ──────────────────────────────────────────────────╮
fig_bg = px.line(entries_df, x="time", y="sgv", title="Glucose (mg/dL)")
fig_bg.update_traces(line=dict(color="#1f77b4", width=2))
fig_bg.update_layout(height=250, margin=dict(l=60, r=30, t=40, b=40))
st.plotly_chart(fig_bg, use_container_width=True)

# ╭─ Bolus / carbs chart ───────────────────────────────────────╮
bol_max = bol_df["insulin"].max() if not bol_df.empty else 1
fig_bc  = px.bar(bol_df, x="time", y="insulin", color_discrete_sequence=["#d62728"],
                 title="Bolus vs Carbs")
fig_bc.update_layout(yaxis_title="Units", yaxis_range=[0, bol_max*1.2],
                     height=220, margin=dict(l=60, r=30, t=40, b=40))

# add SMBs ▸▼
if not smb_df.empty:
    fig_bc.add_bar(x=smb_df["time"], y=smb_df["insulin"],
                   name="SMB", marker_color="#ff9896")

# add carbs as sized bubbles ▸▼
if not carb_df.empty:
    fig_bc.add_scatter(x=carb_df["time"], y=[bol_max*1.05]*len(carb_df),
                       mode="markers+text",
                       marker=dict(size=carb_df["carbs"], color="#2ca02c", opacity=.7),
                       text=carb_df["carbs"].astype(int).astype(str)+" g",
                       textposition="top center",
                       name="Carbs")

st.plotly_chart(fig_bc, use_container_width=True)

# ╭─ Basal chart ───────────────────────────────────────────────╮
fig_basal = px.step(temp_df, x="time", y="rate", title="Temp basal (U/h)",
                    color_discrete_sequence=["#9467bd"])
fig_basal.update_traces(fill="tozeroy", line_shape="hv")
fig_basal.update_layout(height=180, margin=dict(l=60, r=30, t=40, b=40))
st.plotly_chart(fig_basal, use_container_width=True)

st.caption("⏱️  All API calls are cached for 5 minutes.  \
Blue = BG • Red = manual bolus • Pink = SMB • Green = carbs • Purple = temp basal")
