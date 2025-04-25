| **B. switch to pytz** | ```python
local_tz = pytz.timezone(str(local_tz))  # convert
start_dt = local_tz.localize(datetime.combine(start_date, start_time)).astimezone(pytz.UTC)
end_dt   = local_tz.localize(datetime.combine(end_date,   end_time  )).astimezone(pytz.UTC)
``` | If you prefer pytz semantics everywhere. |

---

### Full `app.py` with **Variant A (zoneinfo-only)**

```python
# --------  Adriana's Loop Dashboard  ---------------------------------
import os, json, pytz
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import requests
from plotly.subplots import make_subplots
import plotly.graph_objects as go


st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

NS_URL     = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET  = st.secrets["API_SECRET"]
COUNT_DAYS = 90

@st.cache_data(ttl=600, show_spinner=False)
def fetch_ns():
    headers = {"API-SECRET": NS_SECRET}
    since   = int((datetime.utcnow() - timedelta(days=COUNT_DAYS)).timestamp()*1000)
    timeout = (10, 15)

    e_url = f"{NS_URL}/api/v1/entries.json?find[date][$gte]={since}&count=8640"
    entries    = requests.get(e_url, headers=headers, timeout=timeout).json()

    t_url = f"{NS_URL}/api/v1/treatments.json?find[created_at][$gte]={since}&count=4000"
    treatments = requests.get(t_url, headers=headers, timeout=timeout).json()

    prof_raw = requests.get(f"{NS_URL}/api/v1/profile.json", headers=headers,
                            timeout=timeout).json()
    profile  = prof_raw[0] if prof_raw else {}

    edf = pd.DataFrame(entries)
    tdf = pd.DataFrame(treatments)
    for df, col in [(edf, "date"), (tdf, "created_at")]:
        if col in df.columns:
            df["time"] = pd.to_datetime(df[col], utc=True)

    return edf, tdf, profile


st.info("Fetching data from Nightscout …")
try:
    entries_df, t_df, profile = fetch_ns()
    st.success("Nightscout data loaded")
except Exception as e:
    st.error(f"Nightscout error ➜ {e}")
    st.stop()

# ── Date-time pickers ────────────────────────────────────────────────
local_tz   = datetime.now().astimezone().tzinfo          # zoneinfo tz
today_date = datetime.now(local_tz).date()

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date",  today_date)
    start_time = st.time_input("Start time",  datetime.strptime("00:00","%H:%M").time())
with col2:
    end_date   = st.date_input("End date",    today_date)
    end_time   = st.time_input("End time",    datetime.strptime("23:59","%H:%M").time())

# *** zoneinfo version – no .localize() ***
start_dt = datetime.combine(start_date, start_time, tzinfo=local_tz).astimezone(pytz.UTC)
end_dt   = datetime.combine(end_date,   end_time,   tzinfo=local_tz).astimezone(pytz.UTC)

if end_dt < start_dt:
    st.error("End must be after start")
    st.stop()

# ── Slice data to interval ───────────────────────────────────────────
mask = (entries_df["time"] >= start_dt) & (entries_df["time"] <= end_dt)
entries_df = entries_df.loc[mask].copy()

mask_t = (t_df["time"] >= start_dt) & (t_df["time"] <= end_dt)
t_df = t_df.loc[mask_t].copy()

entries_df["mmol"] = (entries_df["sgv"] / 18).round(1)

bolus_df = t_df[t_df["insulin"].notna()]
smb_df   = bolus_df[bolus_df["enteredBy"].str.contains("SMB", na=False)]
man_df   = bolus_df[~bolus_df["enteredBy"].str.contains("SMB", na=False)]

carb_df  = t_df[t_df["carbs"].notna() & (t_df["carbs"] > 0)]

# ── Build scheduled basal ───────────────────────────────────────────
def build_sched(prof_dict, start_utc, end_utc):
    if not prof_dict:
        return pd.DataFrame(columns=["time","rate"])
    try:
        basal_blocks = prof_dict["store"]["basalprofile"]
    except KeyError:
        return pd.DataFrame(columns=["time","rate"])

    base = start_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [(base + timedelta(minutes=int(b["i"])), b["value"]) for b in basal_blocks]
    sched = pd.DataFrame(rows, columns=["time","rate"])
    while sched["time"].max() < end_utc:
        sched = pd.concat([sched, sched.assign(time=sched["time"] + timedelta(days=1))])
    return sched[(sched["time"]>=start_utc)&(sched["time"]<=end_utc)]

sched_df = build_sched(profile, start_dt, end_dt)

# ── Plotly sub-plots ────────────────────────────────────────────────
fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                    row_heights=[0.45,0.30,0.25], vertical_spacing=0.03,
                    subplot_titles=("Blood Glucose",
                                    "Bolus / SMB + Carbs",
                                    "Scheduled Basal (U / h)"))

fig.add_trace(go.Scatter(x=entries_df["time"], y=entries_df["mmol"],
                         mode="lines+markers", name="BG",
                         line=dict(color="green"), marker=dict(size=4),
                         hovertemplate="%{y:.1f} mmol/L<br>%{x|%Y-%m-%d %H:%M}"),
              row=1,col=1)

fig.add_trace(go.Bar(x=man_df["time"], y=man_df["insulin"],
                     name="Manual bolus", marker_color="rgba(0,102,204,.6)"),
              row=2,col=1)
fig.add_trace(go.Bar(x=smb_df["time"], y=smb_df["insulin"],
                     name="SMB", marker_color="rgba(255,99,132,.6)"),
              row=2,col=1)

fig.add_trace(go.Scatter(x=carb_df["time"], y=[0]*len(carb_df),
                         text=[f"{c} g" for c in carb_df["carbs"]],
                         mode="markers+text", textposition="top center",
                         marker=dict(symbol="circle", size=10, color="orange"),
                         name="Carbs"), row=2,col=1)

fig.add_trace(go.Scatter(x=sched_df["time"], y=sched_df["rate"],
                         mode="lines", step="post",
                         line=dict(color="darkorange"),
                         name="Scheduled basal"), row=3,col=1)

fig.update_yaxes(title_text="mmol/L", row=1,col=1, range=[2,15])
fig.update_yaxes(title_text="U",      row=2,col=1)
fig.update_yaxes(title_text="U / h",  row=3,col=1)

fig.update_layout(height=800, bargap=0.15,
                  legend=dict(orientation="h", yanchor="bottom", y=1.02,
                              xanchor="right", x=1),
                  margin=dict(t=40,b=40,l=50,r=30), hovermode="closest")

st.plotly_chart(fig, use_container_width=True)

# ── (Optional) TDD trend notice ────────────────────────────────────
try:
    bolus_df["date"] = bolus_df["time"].dt.date
    tdd = bolus_df.groupby("date")["insulin"].sum()
    last7, prev7 = tdd[-7:].mean(), tdd[-14:-7].mean()
    delta = last7 - prev7
    if abs(delta) > 2:
        txt = "higher 📈" if delta>0 else "lower 📉"
        st.info(f"Average TDD last week is **{abs(delta):.1f} U {txt}** "
                f"({last7:.1f} U vs {prev7:.1f} U).")
except Exception:
    pass
