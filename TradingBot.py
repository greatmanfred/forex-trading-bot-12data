# ============================================================
# TradingBot.py
# Structured Multi-Timeframe Confluence Trading Bot
# FULL DEBUG VERSION (Strategy Unchanged - Data Fix Applied)
# ============================================================

import os
import requests
import time
import datetime
import pandas as pd
import numpy as np
import traceback

# ============================================================
# ENVIRONMENT VARIABLES
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
DEBUG = True

minute_call_count = 0
daily_call_count = 0
minute_window_start = time.time()
daily_window_start = time.time()

# ============================================================
# SYMBOLS (UNCHANGED)
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
# DEBUG PRINT
# ============================================================

def debug_log(message):
    if DEBUG:
        print(message)

# ============================================================
# RATE CONTROL (UNCHANGED LOGIC)
# ============================================================

def check_rate_limit():
    global minute_call_count, daily_call_count
    global minute_window_start, daily_window_start

    now = time.time()

    debug_log(f"[RATE CHECK] Minute: {minute_call_count}/7 | Daily: {daily_call_count}/750")

    if now - minute_window_start >= 60:
        debug_log("[RESET] Minute window reset")
        minute_call_count = 0
        minute_window_start = now

    if now - daily_window_start >= 86400:
        debug_log("[RESET] Daily window reset")
        daily_call_count = 0
        daily_window_start = now

    if minute_call_count >= MINUTE_CALL_LIMIT:
        sleep_time = 60 - (now - minute_window_start)
        if sleep_time > 0:
            debug_log(f"[SLEEP] Waiting {sleep_time:.2f}s (Minute limit)")
            time.sleep(sleep_time)
        minute_call_count = 0
        minute_window_start = time.time()

    if daily_call_count >= DAILY_CALL_LIMIT:
        sleep_time = 86400 - (now - daily_window_start)
        if sleep_time > 0:
            debug_log(f"[SLEEP] Waiting {sleep_time:.2f}s (Daily limit)")
            time.sleep(sleep_time)
        daily_call_count = 0
        daily_window_start = time.time()

def register_call():
    global minute_call_count, daily_call_count
    minute_call_count += 1
    daily_call_count += 1
    debug_log(f"[API REGISTERED] Minute: {minute_call_count} | Daily: {daily_call_count}")

# ============================================================
# FETCH DATA (FIXED SAFELY)
# ============================================================

def fetch_5m_data(symbol, weeks_required=2):

    try:
        candles_required = weeks_required * 7 * 12 * 24

        debug_log(f"\n[FETCH START] {symbol}")
        debug_log(f"[REQUESTING] 5M Candles: {candles_required}")

        check_rate_limit()

        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": f"{symbol[:3]}/{symbol[3:]}",
            "interval": "5min",
            "outputsize": candles_required,
            "apikey": TWELVEDATA_API_KEY
        }

        response = requests.get(url, params=params)
        register_call()

        debug_log(f"[HTTP STATUS] {response.status_code}")

        data = response.json()
        debug_log(f"[API KEYS] {list(data.keys())}")

        if "values" not in data:
            debug_log(f"[FULL API ERROR] {symbol} -> {data}")
            return None

        df = pd.DataFrame(data["values"])

        # ✅ FIX: Convert ONLY numeric columns
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)

        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.set_index("datetime", inplace=True)

        debug_log(f"[DATA RECEIVED] Rows: {len(df)}")

        return df

    except Exception:
        print(f"[FETCH EXCEPTION] {symbol}")
        traceback.print_exc()
        return None

# ============================================================
# STRATEGY LOGIC (100% UNCHANGED)
# ============================================================

def resample(df, timeframe):
    debug_log(f"[RESAMPLING] Timeframe: {timeframe}")
    return df.resample(timeframe).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last"
    }).dropna()

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

def calculate_score(trend_2h, trend_30m, trend_15m,
                    engulf_30m, engulf_15m,
                    structure_30m, structure_15m):

    score = 0

    if trend_2h != "RANGE":
        score += 25
    if trend_30m == trend_2h:
        score += 20
    if trend_15m == trend_2h:
        score += 15
    if engulf_30m:
        score += 10
    if engulf_15m:
        score += 10
    if structure_30m:
        score += 10
    if structure_15m:
        score += 10

    debug_log(f"[SCORE BREAKDOWN] Final Score: {score}/100")
    return score

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        response = requests.post(url, data=payload)

        debug_log(f"[TELEGRAM STATUS] {response.status_code}")
        debug_log(f"[TELEGRAM RESPONSE] {response.text}")

    except Exception:
        print("[TELEGRAM ERROR]")
        traceback.print_exc()

def main():

    print("\n==============================")
    print("TRADING BOT STARTED (DEBUG MODE)")
    print("==============================")

    invalid_signals = []

    for symbol in SYMBOLS:

        print("\n==============================")
        print(f"[SCANNING] {symbol}")

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
            trend_2h, trend_30m, trend_15m,
            engulf_30m, engulf_15m,
            structure_30m, structure_15m
        )

        confidence = score
        debug_log(f"[CONFIDENCE] {confidence:.2f}%")

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

            debug_log(f"[VALID SIGNAL] {symbol} | {direction}")

            message = (
                f"{symbol} {direction}\n"
                f"Confidence: {confidence:.2f}%\n"
                f"Entry: {entry:.5f}\n"
                f"SL: {sl:.5f}\n"
                f"TP: {tp:.5f}"
            )

            send_telegram(message)

        else:
            debug_log(f"[REJECTED] {symbol} | Confidence: {confidence:.2f}%")
            invalid_signals.append(f"{symbol} - {confidence:.2f}%")

    if invalid_signals:
        summary = "Invalid Signals Summary:\n" + "\n".join(invalid_signals)
        send_telegram(summary)

    print("\nBOT FINISHED SCANNING ALL SYMBOLS")

if __name__ == "__main__":
    main()
