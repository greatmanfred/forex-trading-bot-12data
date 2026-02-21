# ============================================================
# TradingBot.py
# Structured Multi-Timeframe Confluence Trading Bot
# DEBUG VERSION (Full Logging Enabled)
# FULLY STABLE FREE-TIER SAFE VERSION
# ============================================================

import os
import requests
import time
import datetime
import pandas as pd
import numpy as np

# ============================================================
# ENVIRONMENT VARIABLES (GitHub Secrets)
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

# ============================================================
# CONFIGURATION
# ============================================================

MINUTE_CALL_LIMIT = 4   # ✅ SAFE FREE TIER LIMIT
DAILY_CALL_LIMIT = 750
CONFIDENCE_THRESHOLD = 68.5
RR_RATIO = 2.0

DEBUG = True

minute_call_count = 0
daily_call_count = 0
minute_window_start = time.time()
daily_window_start = time.time()

# ============================================================
# FOREX SYMBOLS (Exness Compatible)
# ============================================================

SYMBOLS = [
    "EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD",
    "AUDUSD","NZDUSD","EURGBP","EURJPY","GBPJPY",
    "EURAUD","EURCAD","EURCHF","GBPAUD","GBPCAD",
    "GBPCHF","AUDJPY","AUDCAD","AUDCHF","NZDJPY",
    "NZDCAD","NZDCHF","CADJPY","CADCHF","CHFJPY",
    "USDSGD","USDHKD","EURSGD","GBPSEK","EURNZD"
]

# ============================================================
# RATE CONTROL
# ============================================================

def check_rate_limit():
    global minute_call_count, daily_call_count
    global minute_window_start, daily_window_start

    now = time.time()

    # Reset minute window
    if now - minute_window_start >= 60:
        minute_call_count = 0
        minute_window_start = now

    # Reset daily window
    if now - daily_window_start >= 86400:
        daily_call_count = 0
        daily_window_start = now

    if DEBUG:
        print(f"[RATE CHECK] Minute: {minute_call_count}/{MINUTE_CALL_LIMIT} | Daily: {daily_call_count}/{DAILY_CALL_LIMIT}")

    # Minute limit protection
    if minute_call_count >= MINUTE_CALL_LIMIT:
        sleep_time = 60 - (now - minute_window_start)
        if sleep_time > 0:
            print(f"[SLEEP] Waiting {sleep_time:.2f}s (Minute limit)")
            time.sleep(sleep_time)

        minute_call_count = 0
        minute_window_start = time.time()

    # Daily limit protection
    if daily_call_count >= DAILY_CALL_LIMIT:
        sleep_time = 86400 - (now - daily_window_start)
        if sleep_time > 0:
            print(f"[SLEEP] Waiting {sleep_time:.2f}s (Daily limit)")
            time.sleep(sleep_time)

        daily_call_count = 0
        daily_window_start = time.time()


def register_call():
    global minute_call_count, daily_call_count
    minute_call_count += 1
    daily_call_count += 1

    if DEBUG:
        print(f"[API CALL REGISTERED] Minute: {minute_call_count} | Daily: {daily_call_count}")

# ============================================================
# DATA FETCH (5M ONLY)
# ============================================================

def fetch_5m_data(symbol, weeks_required=2):

    candles_5m_required = weeks_required * 7 * 12 * 24

    check_rate_limit()

    # ✅ FIXED SYMBOL FORMAT FOR 12DATA
    formatted_symbol = f"{symbol[:3]}/{symbol[3:]}"

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": formatted_symbol,
        "interval": "5min",
        "outputsize": candles_5m_required,
        "apikey": TWELVEDATA_API_KEY
    }

    if DEBUG:
        print(f"[FETCH] {symbol} ({formatted_symbol}) | 5M Candles: {candles_5m_required}")

    response = requests.get(url, params=params)
    register_call()

    if DEBUG:
        print(f"[FETCH STATUS] {symbol} | HTTP {response.status_code}")

    try:
        data = response.json()
    except Exception as e:
        print(f"[JSON ERROR] {symbol} -> {e}")
        return None

    if DEBUG:
        print(f"[FETCH DATA KEYS] {symbol}: {list(data.keys())}")

    # Handle API error responses
    if "values" not in data:
        print(f"[FULL API ERROR] {symbol} -> {data}")
        return None

    try:
        df = pd.DataFrame(data["values"])
        df = df.astype(float)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.set_index("datetime", inplace=True)

        if DEBUG:
            print(f"[DATA RECEIVED] {symbol} | Rows: {len(df)}")

        return df

    except Exception as e:
        print(f"[DATA PARSE ERROR] {symbol} -> {e}")
        return None


# ============================================================
# RESAMPLING
# ============================================================

def resample(df, timeframe):
    return df.resample(timeframe).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    }).dropna()


# ============================================================
# STRATEGY LOGIC
# ============================================================

def determine_trend(df):
    sma_fast = df["close"].rolling(20).mean()
    sma_slow = df["close"].rolling(50).mean()

    if sma_fast.iloc[-1] > sma_slow.iloc[-1]:
        return "UP"
    elif sma_fast.iloc[-1] < sma_slow.iloc[-1]:
        return "DOWN"
    return "RANGE"


def detect_engulfing(df):
    if len(df) < 2:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    bullish = (
        prev["close"] < prev["open"] and
        last["close"] > last["open"] and
        last["close"] > prev["open"] and
        last["open"] < prev["close"]
    )

    bearish = (
        prev["close"] > prev["open"] and
        last["close"] < last["open"] and
        last["open"] > prev["close"] and
        last["close"] < prev["open"]
    )

    if bullish:
        return "BUY"
    elif bearish:
        return "SELL"
    return None
