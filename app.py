import streamlit as st
import requests
import pandas as pd

st.set_page_config(page_title="Adriana Loop Dashboard", layout="wide")
st.title("Adriana's Looping Dashboard (MVP)")

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
st.write("BG Entries Sample:")
st.dataframe(entries_df.head())
