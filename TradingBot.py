# ============================================================
# TradingBot.py
# Structured Multi-Timeframe Confluence Trading Bot
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

MINUTE_CALL_LIMIT = 7
DAILY_CALL_LIMIT = 750
CONFIDENCE_THRESHOLD = 68.5
RR_RATIO = 2.0

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

    # Enforce minute limit
    if minute_call_count == MINUTE_CALL_LIMIT:
        sleep_time = 60 - (now - minute_window_start)
        if sleep_time > 0:
            time.sleep(sleep_time)
        minute_call_count = 0
        minute_window_start = time.time()

    # Enforce daily limit
    if daily_call_count == DAILY_CALL_LIMIT:
        sleep_time = 86400 - (now - daily_window_start)
        if sleep_time > 0:
            time.sleep(sleep_time)
        daily_call_count = 0
        daily_window_start = time.time()

def register_call():
    global minute_call_count, daily_call_count
    minute_call_count += 1
    daily_call_count += 1

# ============================================================
# DATA FETCH (5M ONLY)
# ============================================================

def fetch_5m_data(symbol, weeks_required=2):
    """
    Fetch 5M historical data sufficient to construct
    2H candles for desired weeks.
    """

    # 2H candles per week = 7 days * 12 candles/day = 84
    # For 2 weeks = 168 2H candles
    # Each 2H candle = 24 x 5M candles
    # Required 5M candles = 168 * 24 = 4032

    candles_5m_required = weeks_required * 7 * 12 * 24

    check_rate_limit()

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "5min",
        "outputsize": candles_5m_required,
        "apikey": TWELVEDATA_API_KEY
    }

    response = requests.get(url, params=params)
    register_call()

    data = response.json()

    if "values" not in data:
        return None

    df = pd.DataFrame(data["values"])
    df = df.astype(float)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.sort_values("datetime", inplace=True)
    df.set_index("datetime", inplace=True)

    return df

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
    last = df.iloc[-1]
    prev = df.iloc[-2]

    bullish = (prev["close"] < prev["open"] and
               last["close"] > last["open"] and
               last["close"] > prev["open"] and
               last["open"] < prev["close"])

    bearish = (prev["close"] > prev["open"] and
               last["close"] < last["open"] and
               last["open"] > prev["close"] and
               last["close"] < prev["open"])

    if bullish:
        return "BULLISH"
    if bearish:
        return "BEARISH"
    return None

def calculate_structure(df):
    highs = df["high"].rolling(5).max()
    lows = df["low"].rolling(5).min()

    if highs.iloc[-1] > highs.iloc[-5]:
        return "HH"
    if lows.iloc[-1] < lows.iloc[-5]:
        return "LL"
    return None

# ============================================================
# SCORING SYSTEM (100 POINTS TOTAL)
# ============================================================

def calculate_score(trend_2h, trend_30m, trend_15m,
                    engulf_30m, engulf_15m,
                    structure_30m, structure_15m):

    score = 0

    # 2H trend weight = 25
    if trend_2h != "RANGE":
        score += 25

    # 30M alignment weight = 20
    if trend_30m == trend_2h:
        score += 20

    # 15M alignment weight = 15
    if trend_15m == trend_2h:
        score += 15

    # 30M engulfing weight = 10
    if engulf_30m:
        score += 10

    # 15M engulfing weight = 10
    if engulf_15m:
        score += 10

    # 30M structure weight = 10
    if structure_30m:
        score += 10

    # 15M structure weight = 10
    if structure_15m:
        score += 10

    return score

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    requests.post(url, data=payload)

# ============================================================
# MAIN EXECUTION
# ============================================================

def main():

    invalid_signals = []

    for symbol in SYMBOLS:

        df_5m = fetch_5m_data(symbol)
        if df_5m is None:
            continue

        df_2h = resample(df_5m, "2H")
        df_30m = resample(df_5m, "30T")
        df_15m = resample(df_5m, "15T")

        trend_2h = determine_trend(df_2h)
        trend_30m = determine_trend(df_30m)
        trend_15m = determine_trend(df_15m)

        engulf_30m = detect_engulfing(df_30m)
        engulf_15m = detect_engulfing(df_15m)

        structure_30m = calculate_structure(df_30m)
        structure_15m = calculate_structure(df_15m)

        score = calculate_score(
            trend_2h,
            trend_30m,
            trend_15m,
            engulf_30m,
            engulf_15m,
            structure_30m,
            structure_15m
        )

        confidence = (score / 100) * 100

        if confidence >= CONFIDENCE_THRESHOLD:

            entry = df_15m["close"].iloc[-1]

            if trend_2h == "UP":
                sl = df_15m["low"].iloc[-1] * 0.999
                tp = entry + (entry - sl) * RR_RATIO
                direction = "BUY"
            else:
                sl = df_15m["high"].iloc[-1] * 1.001
                tp = entry - (sl - entry) * RR_RATIO
                direction = "SELL"

            message = (
                f"{symbol} {direction}\n"
                f"Confidence: {confidence:.2f}%\n"
                f"Entry: {entry:.5f}\n"
                f"SL: {sl:.5f}\n"
                f"TP: {tp:.5f}"
            )

            send_telegram(message)

        else:
            invalid_signals.append(f"{symbol} - {confidence:.2f}%")

    if invalid_signals:
        summary = "Invalid Signals Summary:\n" + "\n".join(invalid_signals)
        send_telegram(summary)

if __name__ == "__main__":
    main()
