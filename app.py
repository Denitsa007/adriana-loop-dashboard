import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time

st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard — Now with Timeline!")

st.markdown("#### Connect to Nightscout and load recent data")

NS_URL = st.secrets["NIGHTSCOUT_URL"]
NS_SECRET = st.secrets["API_SECRET"]

@st.cache_data(ttl=600)
def fetch_nightscout_data():
    headers = {"API-SECRET": NS_SECRET}
    entries = requests.get(f"{NS_URL}/api/v1/entries.json?count=1000", headers=headers).json()
    treatments = requests.get(f"{NS_URL}/api/v1/treatments.json?count=1000", headers=headers).json()
    devicestatus = requests.get(f"{NS_URL}/api/v1/devicestatus.json?count=10", headers=headers).json()
    return pd.DataFrame(entries), pd.DataFrame(treatments), pd.DataFrame(devicestatus)

st.write("Fetching data from Nightscout...")
entries_df, treatments_df, devicestatus_df = fetch_nightscout_data()
st.success("Data loaded.")

# Format timestamps and BG values
entries_df['time'] = pd.to_datetime(entries_df['dateString'])
entries_df['mmol'] = entries_df['sgv'] / 18.0  # Convert mg/dL to mmol/L

treatments_df['time'] = pd.to_datetime(treatments_df['created_at'])
bolus_df = treatments_df[treatments_df['insulin'].notnull()]

# Default range: today from 00:00 to 23:59
today = datetime.now().date()
default_start = datetime.combine(today, time.min)
default_end = datetime.combine(today, time.max)

# Time filter UI (with today as default)
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date", value=default_start.date())
    start_hour = st.time_input("Start time", value=default_start.time())
with col2:
    end_date = st.date_input("End date", value=default_end.date())
    end_hour = st.time_input("End time", value=default_end.time())

# Combine to datetimes
start_time = pd.to_datetime(datetime.combine(start_date, start_hour))
end_time = pd.to_datetime(datetime.combine(end_date, end_hour))

# Filter all data
entries_df = entries_df[(entries_df['time'] >= start_time) & (entries_df['time'] <= end_time)]
bolus_df = bolus_df[(bolus_df['time'] >= start_time) & (bolus_df['time'] <= end_time)]

# Plot
fig = go.Figure()

# BG Line
fig.add_trace(go.Scatter(
    x=entries_df['time'],
    y=entries_df['mmol'],
    mode='lines+markers',
    name='BG (mmol/L)',
    line=dict(color='green')
))

# Bolus Bars
fig.add_trace(go.Bar(
    x=bolus_df['time'],
    y=bolus_df['insulin'],
    name='Bolus (U)',
    yaxis='y2',
    marker_color='rgba(0, 102, 204, 0.4)'
))

# Layout
fig.update_layout(
    title='BG + Insulin Timeline',
    xaxis=dict(title='Time'),
    yaxis=dict(title='BG (mmol/L)', range=[2, 15]),
    yaxis2=dict(
        title='Insulin (U)',
        overlaying='y',
        side='right',
        showgrid=False
    ),
    height=500,
    legend=dict(orientation='h'),
    bargap=0.15
)

st.plotly_chart(fig, use_container_width=True)
