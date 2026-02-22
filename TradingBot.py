# ============================================================
# TradingBot.py
# Institutional Adaptive Volatility-Rated Intelligence Engine
# Strategy Core Unchanged - Stability & Error Fix Version
# ============================================================

import os
import requests
import time
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
CONFIDENCE_THRESHOLD = 60.0
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
# DEBUG
# ============================================================

def debug_log(msg):
    if DEBUG:
        print(msg)

# ============================================================
# RATE LIMIT CONTROL (UNCHANGED)
# ============================================================

def check_rate_limit():
    global minute_call_count, daily_call_count
    global minute_window_start, daily_window_start

    now = time.time()

    if now - minute_window_start >= 60:
        minute_call_count = 0
        minute_window_start = now

    if now - daily_window_start >= 86400:
        daily_call_count = 0
        daily_window_start = now

    if minute_call_count >= MINUTE_CALL_LIMIT:
        sleep_time = 60 - (now - minute_window_start)
        if sleep_time > 0:
            time.sleep(sleep_time)
        minute_call_count = 0
        minute_window_start = time.time()

    if daily_call_count >= DAILY_CALL_LIMIT:
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
# FETCH DATA (STABILITY FIXED)
# ============================================================

def fetch_5m_data(symbol, weeks_required=2):
    try:
        candles_required = weeks_required * 7 * 12 * 24
        check_rate_limit()

        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": f"{symbol[:3]}/{symbol[3:]}",
            "interval": "5min",
            "outputsize": candles_required,
            "apikey": TWELVEDATA_API_KEY
        }

        response = requests.get(url, params=params, timeout=10)
        register_call()

        if response.status_code != 200:
            return None

        data = response.json()

        if "values" not in data or len(data["values"]) < 200:
            return None

        df = pd.DataFrame(data["values"])

        for col in ["open","high","low","close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(inplace=True)

        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df.dropna(subset=["datetime"], inplace=True)

        df.sort_values("datetime", inplace=True)
        df.set_index("datetime", inplace=True)

        if len(df) < 200:
            return None

        return df

    except:
        traceback.print_exc()
        return None

# ============================================================
# CORE STRATEGY (LOGIC UNCHANGED — SAFETY ADDED)
# ============================================================

def resample(df, timeframe):
    df_resampled = df.resample(timeframe).agg({
        "open":"first",
        "high":"max",
        "low":"min",
        "close":"last"
    }).dropna()

    return df_resampled if len(df_resampled) > 50 else pd.DataFrame()

def determine_trend(df):
    if len(df) < 50:
        return "RANGE"

    sma_fast = df["close"].rolling(20).mean()
    sma_slow = df["close"].rolling(50).mean()

    if pd.isna(sma_fast.iloc[-1]) or pd.isna(sma_slow.iloc[-1]):
        return "RANGE"

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
        return "BULLISH"
    if bearish:
        return "BEARISH"
    return None

def calculate_structure(df):
    if len(df) < 6:
        return None

    highs = df["high"].rolling(5).max()
    lows = df["low"].rolling(5).min()

    if pd.isna(highs.iloc[-1]) or pd.isna(highs.iloc[-5]):
        return None

    if highs.iloc[-1] > highs.iloc[-5]:
        return "HH"
    if lows.iloc[-1] < lows.iloc[-5]:
        return "LL"
    return None

# ============================================================
# VOLATILITY ENGINE (NaN SAFE)
# ============================================================

def calculate_atr(df, period=14):
    if len(df) < period + 1:
        return pd.Series([0])

    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)

    atr = true_range.rolling(period).mean()
    atr = atr.fillna(0)

    return atr

# ============================================================
# SCORE (LOGIC UNCHANGED — SAFE EXECUTION)
# ============================================================

def calculate_score(df_2h, df_30m, df_15m,
                    trend_2h, trend_30m, trend_15m,
                    engulf_30m, engulf_15m,
                    structure_30m, structure_15m):

    if df_2h.empty or df_30m.empty or df_15m.empty:
        return 0

    total_score = 0

    # 2H TREND
    if trend_2h != "RANGE":
        total_score += 8

    slope_series = df_2h["close"].rolling(10).mean().diff()
    slope = slope_series.iloc[-1] if not slope_series.empty else 0

    if not pd.isna(slope) and abs(slope) > 0:
        total_score += 6

    sma20 = df_2h["close"].rolling(20).mean().iloc[-1]
    if not pd.isna(sma20):
        distance = abs(df_2h["close"].iloc[-1] - sma20)
        if distance > df_2h["close"].std():
            total_score += 6

    # VOLATILITY
    atr_15 = calculate_atr(df_15m).iloc[-1]
    atr_30 = calculate_atr(df_30m).iloc[-1]

    if atr_15 > df_15m["close"].std():
        total_score += 10

    if atr_30 > df_30m["close"].std():
        total_score += 10

    # 30M
    if trend_30m == trend_2h:
        total_score += 8

    if structure_30m:
        total_score += 6

    if engulf_30m:
        body = abs(df_30m.iloc[-1]["close"] - df_30m.iloc[-1]["open"])
        candle_range = df_30m.iloc[-1]["high"] - df_30m.iloc[-1]["low"]
        if candle_range > 0 and body / candle_range > 0.6:
            total_score += 6

    # 15M
    if trend_15m == trend_2h:
        total_score += 8

    if structure_15m:
        total_score += 6

    if engulf_15m:
        total_score += 6

    # RISK
    if RR_RATIO >= 2:
        total_score += 10

    if trend_2h == trend_30m == trend_15m:
        total_score += 10

    debug_log(f"[TOTAL INSTITUTIONAL SCORE] {total_score}/100")

    return total_score

# ============================================================
# TELEGRAM (UNCHANGED)
# ============================================================

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, data=payload, timeout=10)
    except:
        traceback.print_exc()

# ============================================================
# MAIN
# ============================================================

def main():

    invalid_signals = []

    for symbol in SYMBOLS:

        df_5m = fetch_5m_data(symbol)
        if df_5m is None:
            continue

        df_2h = resample(df_5m, "2h")
        df_30m = resample(df_5m, "30min")
        df_15m = resample(df_5m, "15min")

        if df_2h.empty or df_30m.empty or df_15m.empty:
            continue

        trend_2h = determine_trend(df_2h)
        trend_30m = determine_trend(df_30m)
        trend_15m = determine_trend(df_15m)

        engulf_30m = detect_engulfing(df_30m)
        engulf_15m = detect_engulfing(df_15m)

        structure_30m = calculate_structure(df_30m)
        structure_15m = calculate_structure(df_15m)

        confidence = calculate_score(
            df_2h, df_30m, df_15m,
            trend_2h, trend_30m, trend_15m,
            engulf_30m, engulf_15m,
            structure_30m, structure_15m
        )

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
