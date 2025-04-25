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
def fetch_nightscout_data(start_date: datetime, end_date: datetime) -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    """
    Fetch only data needed for the selected date range with retry logic.
    Returns: (entries_df, treatments_df, profile_dict)
    """
    headers = {"API-SECRET": NS_SECRET}
    since = int(start_date.timestamp() * 1000)
    until = int(end_date.timestamp() * 1000)
    
    endpoints = {
        "entries": f"{NS_URL}/api/v1/entries.json?find[date][$gte]={since}&find[date][$lte]={until}",
        "treatments": f"{NS_URL}/api/v1/treatments.json?find[created_at][$gte]={since}&find[created_at][$lte]={until}",
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
            
            # Convert timestamps with robust column checking
            if not entries_df.empty:
                if "date" in entries_df.columns:
                    entries_df["time"] = pd.to_datetime(entries_df["date"], unit="ms", utc=True)
                elif "dateString" in entries_df.columns:
                    entries_df["time"] = pd.to_datetime(entries_df["dateString"], utc=True)
                else:
                    st.warning("No timestamp column found in entries data")
                    entries_df["time"] = pd.to_datetime("now", utc=True)

            if not treatments_df.empty:
                if "created_at" in treatments_df.columns:
                    treatments_df["time"] = pd.to_datetime(treatments_df["created_at"], utc=True)
                elif "timestamp" in treatments_df.columns:
                    treatments_df["time"] = pd.to_datetime(treatments_df["timestamp"], unit="ms", utc=True)
                else:
                    st.warning("No timestamp column found in treatments data")
                    treatments_df["time"] = pd.to_datetime("now", utc=True)
            
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
    
    # Show loading message if fetching >1 day of data
    if (end_dt - start_dt) > timedelta(days=1):
        st.info("⚠️ Loading extended date range... This may take longer")
    
    # Data loading
    with st.spinner("Fetching data from Nightscout..."):
        entries_df, treats_df, profile = fetch_nightscout_data(start_dt, end_dt)
    
    # Filter data to selected time range with safety checks
    if not entries_df.empty and "time" in entries_df.columns:
        entries_df = entries_df[(entries_df["time"] >= start_dt) & (entries_df["time"] <= end_dt)]
    else:
        st.warning("No valid time data in entries")
        entries_df = pd.DataFrame()

    if not treats_df.empty and "time" in treats_df.columns:
        treats_df = treats_df[(treats_df["time"] >= start_dt) & (treats_df["time"] <= end_dt)]
    else:
        st.warning("No valid time data in treatments")
        treats_df = pd.DataFrame()
    
    # Convert glucose to mmol/L if needed
    if not entries_df.empty and "sgv" in entries_df.columns:
        entries_df["mmol"] = (entries_df["sgv"] / 18).round(1)
    
    # Categorize treatments with safety checks
    bolus_df = pd.DataFrame()
    smb_df = pd.DataFrame()
    manual_df = pd.DataFrame()
    carb_df = pd.DataFrame()
    temp_df = pd.DataFrame()
    
    if not treats_df.empty:
        if "insulin" in treats_df.columns:
            bolus_df = treats_df[treats_df["insulin"].notnull()]
            if "enteredBy" in bolus_df.columns:
                smb_df = bolus_df[bolus_df["enteredBy"].str.contains("smb", case=False, na=False)]
                manual_df = bolus_df[~bolus_df["enteredBy"].str.contains("smb", case=False, na=False)]
        
        if "carbs" in treats_df.columns:
            carb_df = treats_df[treats_df["carbs"].fillna(0) > 0]
        
        if "eventType" in treats_df.columns:
            temp_df = treats_df[treats_df["eventType"] == "Temp Basal"]
    
    basal_sched_df = build_basal_schedule(profile, start_dt, end_dt)
    
    # Calculate dynamic display limits
    max_bolus = bolus_df["insulin"].max() if not bolus_df.empty and "insulin" in bolus_df.columns else 0
    bolus_ylim = max(1, max_bolus * 1.3)  # 30% padding
    
    # ────────── Improved Carb Visualization ──────────
    carb_y_pos = bolus_ylim * 0.9  # Lower position (90% of bolus range)
    if not carb_df.empty and "carbs" in carb_df.columns:
        carb_sizes = carb_df["carbs"].fillna(0).apply(lambda g: max(8, min(g * 0.8, 35)))  # Larger bubbles
    else:
        carb_sizes = pd.Series([])
    
    # ────────── Visualization ──────────
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.5, 0.28, 0.22],
        vertical_spacing=0.06
    )
    
    # 1. Glucose Trace
    if not entries_df.empty and "time" in entries_df.columns and "mmol" in entries_df.columns:
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
    
    # 2. Boluses and Carbs (Improved)
    if not manual_df.empty and "time" in manual_df.columns and "insulin" in manual_df.columns:
        fig.add_trace(
            go.Bar(
                x=manual_df["time"],
                y=manual_df["insulin"],
                marker_color="rgba(0,102,204,0.7)",
                name="Manual bolus"
            ),
            row=2, col=1
        )
    
    if not smb_df.empty and "time" in smb_df.columns and "insulin" in smb_df.columns:
        fig.add_trace(
            go.Bar(
                x=smb_df["time"],
                y=smb_df["insulin"],
                marker_color="rgba(255,99,132,0.7)",
                name="SMB"
            ),
            row=2, col=1
        )
    
    if not carb_df.empty and "time" in carb_df.columns and "carbs" in carb_df.columns:
        fig.add_trace(
            go.Scatter(
                x=carb_df["time"],
                y=[carb_y_pos] * len(carb_df),
                mode="markers+text",
                marker=dict(
                    color="orange",
                    size=carb_sizes,
                    line=dict(width=1, color="darkorange")  # Border for visibility
                ),
                text=carb_df["carbs"].astype(int).astype(str) + "g",
                textposition="top center",
                name="Carbs",
                hoverinfo="text+x",
                hovertext=carb_df["carbs"].astype(int).astype(str) + "g carbs<br>" + 
                          carb_df["time"].dt.strftime("%H:%M")
            ),
            row=2, col=1
        )
    
    # 3. Basal Rates
    if not basal_sched_df.empty and "time" in basal_sched_df.columns and "rate" in basal_sched_df.columns:
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
    
    if not temp_df.empty and "time" in temp_df.columns and "rate" in temp_df.columns:
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
        margin=dict(t=50, r=20, b=60, l=60),
        hovermode="x unified"  # Better hover interactions
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Add manual refresh button
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

if __name__ == "__main__":
    main()
