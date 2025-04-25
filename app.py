import os
import time
import pytz
import requests
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, time as dtime, timedelta
from typing import Tuple, Dict, Optional

# Constants
DAYS_BACK = 30  # Default days to fetch
READ_TIMEOUT = 30  # seconds per request
MAX_RETRIES = 2

# ────────── Nightscout Configuration ──────────
def get_nightscout_config() -> Tuple[str, str]:
    """Get Nightscout URL and API secret from Streamlit secrets."""
    try:
        ns_url = st.secrets["NIGHTSCOUT_URL"].rstrip("/")
        ns_secret = st.secrets["API_SECRET"]
        return ns_url, ns_secret
    except KeyError as e:
        st.error(f"Missing Streamlit secret: {e}")
        st.stop()

NS_URL, NS_SECRET = get_nightscout_config()
LOCAL_TZ = pytz.timezone(time.tzname[0]) if time.tzname else pytz.UTC

# ────────── Data Fetching ──────────
@st.cache_data(ttl=600, show_spinner=False)
def fetch_nightscout_data() -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    """
    Fetch data from Nightscout API with retry logic.
    Returns: (entries_df, treatments_df, profile_dict)
    """
    headers = {"API-SECRET": NS_SECRET}
    since = int((datetime.utcnow() - timedelta(days=DAYS_BACK)).timestamp() * 1000
    
    endpoints = {
        "entries": f"{NS_URL}/api/v1/entries.json?find[date][$gte]={since}&count=8640",
        "treatments": f"{NS_URL}/api/v1/treatments.json?find[created_at][$gte]={since}&count=4000",
        "profile": f"{NS_URL}/api/v1/profile.json"
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = {
                "entries": requests.get(endpoints["entries"], headers=headers, timeout=READ_TIMEOUT),
                "treatments": requests.get(endpoints["treatments"], headers=headers, timeout=READ_TIMEOUT),
                "profile": requests.get(endpoints["profile"], headers=headers, timeout=READ_TIMEOUT)
            }
            
            # Validate responses
            for key, res in response.items():
                res.raise_for_status()
            
            # Process data
            entries_df = pd.DataFrame(response["entries"].json())
            treatments_df = pd.DataFrame(response["treatments"].json())
            profile = response["profile"].json()[0] if response["profile"].json() else {}
            
            # Convert timestamps
            if not entries_df.empty:
                entries_df["time"] = pd.to_datetime(entries_df["date"], unit="ms", utc=True)
            if not treatments_df.empty:
                treatments_df["time"] = pd.to_datetime(treatments_df["created_at"], utc=True)
            
            return entries_df, treatments_df, profile
            
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                st.error(f"Failed to fetch Nightscout data after {MAX_RETRIES} attempts: {e}")
                st.stop()
            st.warning(f"Nightscout request failed (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(1)

# ────────── Profile Processing ──────────
def extract_basal_segments(profile: Dict) -> Optional[pd.DataFrame]:
    """Extract basal rate segments from Nightscout profile."""
    if not profile:
        return None
        
    # Handle different profile formats
    if "store" in profile:
        default_profile = profile.get("defaultProfile") or next(iter(profile["store"]))
        segments = profile["store"][default_profile]["basal"]
    elif "basalprofile" in profile:
        segments = profile["basalprofile"]
    else:
        return None

    return pd.DataFrame([
        {
            "time_offset": timedelta(seconds=int(s.get("i", s.get("timeAsSeconds", 0)))),
            "rate": float(s.get("v", s.get("value", 0)))
        }
        for s in segments
    ])

def build_basal_schedule(profile: Dict, start: datetime, end: datetime) -> pd.DataFrame:
    """Generate basal rate schedule for visualization."""
    segments = extract_basal_segments(profile)
    if segments is None:
        return pd.DataFrame()

    # Create schedule starting at midnight of the start date
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    schedule = segments.copy()
    schedule["time"] = midnight + schedule["time_offset"]
    
    # Add final segment and filter to date range
    schedule = pd.concat([
        schedule,
        schedule.tail(1).assign(time=end + timedelta(hours=1))
    ])
    
    return schedule[
        (schedule["time"] >= start) & 
        (schedule["time"] <= end)
    ].reset_index(drop=True)

# ────────── UI Components ──────────
def setup_date_selectors() -> Tuple[datetime, datetime]:
    """Create date/time input widgets and return UTC datetimes."""
    today = date.today()
    cols = st.columns(4)
    
    with cols[0]:
        start_date = st.date_input("Start date", today)
    with cols[1]:
        start_time = st.time_input("Start time", dtime(0, 0))
    with cols[2]:
        end_date = st.date_input("End date", today)
    with cols[3]:
        end_time = st.time_input("End time", dtime(23, 59))
    
    # Convert to timezone-aware datetimes
    start_dt = LOCAL_TZ.localize(datetime.combine(start_date, start_time)).astimezone(pytz.UTC)
    end_dt = LOCAL_TZ.localize(datetime.combine(end_date, end_time)).astimezone(pytz.UTC)
    
    return start_dt, end_dt

# ────────── Main App ──────────
def main():
    st.set_page_config("Adriana Loop Dashboard", layout="wide")
    st.title("Adriana's Looping Dashboard")
    
    # Date selection
    start_dt, end_dt = setup_date_selectors()
    
    # Data loading
    with st.spinner("Fetching data from Nightscout..."):
        entries_df, treats_df, profile = fetch_nightscout_data()
    
    # Filter data to selected time range
    entries_df = entries_df[(entries_df["time"] >= start_dt) & (entries_df["time"] <= end_dt)]
    treats_df = treats_df[(treats_df["time"] >= start_dt) & (treats_df["time"] <= end_dt)]
    
    # Convert glucose to mmol/L if needed
    if "sgv" in entries_df.columns:
        entries_df["mmol"] = (entries_df["sgv"] / 18).round(1)
    
    # Categorize treatments
    bolus_df = treats_df[treats_df["insulin"].notnull()]
    smb_df = bolus_df[bolus_df["enteredBy"].str.contains("smb", case=False, na=False)]
    manual_df = bolus_df[~bolus_df["enteredBy"].str.contains("smb", case=False, na=False)]
    carb_df = treats_df[treats_df["carbs"].fillna(0) > 0]
    temp_df = treats_df[treats_df["eventType"] == "Temp Basal"]
    basal_sched_df = build_basal_schedule(profile, start_dt, end_dt)
    
    # Calculate dynamic display limits
    max_bolus = bolus_df["insulin"].max() if not bolus_df.empty else 0
    bolus_ylim = max(1, max_bolus * 1.3)  # 30% padding
    carb_y_pos = bolus_ylim * 1.05
    carb_sizes = carb_df["carbs"].fillna(0).apply(lambda g: max(6, min(g * 0.6, 30)))
    
    # ────────── Visualization ──────────
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.5, 0.28, 0.22],
        vertical_spacing=0.06
    )
    
    # 1. Glucose Trace
    fig.add_trace(
        go.Scatter(
            x=entries_df["time"],
            y=entries_df["mmol"],
            mode="lines",
            line=dict(color="green"),
            name="BG",
            hovertemplate="%{y:.1f} mmol/L<br>%{x|%H:%M}"
        ),
        row=1, col=1
    )
    
    # 2. Boluses and Carbs
    fig.add_trace(
        go.Bar(
            x=manual_df["time"],
            y=manual_df["insulin"],
            marker_color="rgba(0,102,204,0.7)",
            name="Manual bolus"
        ),
        row=2, col=1
    )
    
    fig.add_trace(
        go.Bar(
            x=smb_df["time"],
            y=smb_df["insulin"],
            marker_color="rgba(255,99,132,0.7)",
            name="SMB"
        ),
        row=2, col=1
    )
    
    fig.add_trace(
        go.Scatter(
            x=carb_df["time"],
            y=[carb_y_pos] * len(carb_df),
            mode="markers+text",
            marker=dict(color="orange", size=carb_sizes),
            text=carb_df["carbs"].astype(int).astype(str) + " g",
            textposition="top center",
            name="Carbs"
        ),
        row=2, col=1
    )
    
    # 3. Basal Rates
    if not basal_sched_df.empty:
        fig.add_trace(
            go.Scatter(
                x=basal_sched_df["time"],
                y=basal_sched_df["rate"],
                mode="lines",
                line_shape="hv",
                line=dict(color="lightgrey", dash="dash"),
                name="Scheduled basal"
            ),
            row=3, col=1
        )
    
    if not temp_df.empty:
        fig.add_trace(
            go.Bar(
                x=temp_df["time"],
                y=temp_df["rate"].fillna(0),
                marker_color="rgba(0,150,150,0.6)",
                name="Temp basal"
            ),
            row=3, col=1
        )
    
    # Axis configuration
    fig.update_yaxes(title_text="mmol/L", row=1, col=1, range=[2, 15])
    fig.update_yaxes(title_text="U / g", row=2, col=1, range=[0, bolus_ylim])
    fig.update_yaxes(title_text="Basal U/h", row=3, col=1)
    
    fig.update_layout(
        height=850,
        bargap=0.15,
        legend_orientation="h",
        legend_y=-0.12,
        margin=dict(t=50, r=20, b=60, l=60)
    )
    
    st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()
