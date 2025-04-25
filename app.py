import streamlit as st
import requests
import pandas as pd

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
import plotly.graph_objects as go
from datetime import datetime, timedelta

# Convert Nightscout BG entries to DataFrame
entries_df['time'] = pd.to_datetime(entries_df['dateString'])
entries_df['mmol'] = entries_df['sgv'] / 18.0  # Convert mg/dL → mmol/L

# Convert treatments to DataFrame and filter boluses and SMBs
treatments_df['time'] = pd.to_datetime(treatments_df['created_at'])
bolus_df = treatments_df[treatments_df['insulin'].notnull()]
smb_df = bolus_df[bolus_df['enteredBy'].str.contains('SMB', na=False)]
manual_bolus_df = bolus_df[~bolus_df['enteredBy'].str.contains('SMB', na=False)]

# Plotting
fig = go.Figure()

# BG Line
fig.add_trace(go.Scatter(
    x=entries_df['time'],
    y=entries_df['mmol'],
    mode='lines+markers',
    name='BG (mmol/L)',
    line=dict(color='green')
))

# Manual Bolus Bars
fig.add_trace(go.Bar(
    x=manual_bolus_df['time'],
    y=manual_bolus_df['insulin'],
    name='Manual Bolus (U)',
    yaxis='y2',
    marker_color='rgba(0, 102, 204, 0.6)'
))

# SMB Bars
fig.add_trace(go.Bar(
    x=smb_df['time'],
    y=smb_df['insulin'],
    name='SMB (U)',
    yaxis='y2',
    marker_color='rgba(255, 99, 132, 0.6)'
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
