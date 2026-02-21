# ============================================================
# TradingBot.py
# Institutional Adaptive Volatility-Rated Intelligence Engine
# Strategy Core Unchanged - Scoring Engine Upgraded
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
# FETCH DATA (UNCHANGED)
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

        response = requests.get(url, params=params)
        register_call()
        data = response.json()

        if "values" not in data:
            return None

        df = pd.DataFrame(data["values"])
        for col in ["open","high","low","close"]:
            df[col] = df[col].astype(float)

        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.set_index("datetime", inplace=True)

        return df

    except:
        traceback.print_exc()
        return None

# ============================================================
# CORE STRATEGY (UNCHANGED)
# ============================================================

def resample(df, timeframe):
    return df.resample(timeframe).agg({
        "open":"first",
        "high":"max",
        "low":"min",
        "close":"last"
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
    highs = df["high"].rolling(5).max()
    lows = df["low"].rolling(5).min()

    if highs.iloc[-1] > highs.iloc[-5]:
        return "HH"
    if lows.iloc[-1] < lows.iloc[-5]:
        return "LL"
    return None

# ============================================================
# INSTITUTIONAL VOLATILITY ENGINE
# ============================================================

def calculate_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(period).mean()
    return atr

# ============================================================
# INSTITUTIONAL ADAPTIVE MICRO SCORING
# ============================================================

def calculate_score(df_2h, df_30m, df_15m,
                    trend_2h, trend_30m, trend_15m,
                    engulf_30m, engulf_15m,
                    structure_30m, structure_15m):

    total_score = 0

    # ===============================
    # 2H TREND INTELLIGENCE (20)
    # ===============================
    score_2h = 0

    if trend_2h != "RANGE":
        score_2h += 8

    slope = df_2h["close"].rolling(10).mean().diff().iloc[-1]
    if abs(slope) > 0:
        score_2h += 6

    distance = abs(df_2h["close"].iloc[-1] -
                   df_2h["close"].rolling(20).mean().iloc[-1])
    if distance > df_2h["close"].std():
        score_2h += 6

    total_score += score_2h

    # ===============================
    # VOLATILITY INTELLIGENCE (20)
    # ===============================
    score_vol = 0

    atr_15 = calculate_atr(df_15m).iloc[-1]
    atr_30 = calculate_atr(df_30m).iloc[-1]

    if atr_15 > df_15m["close"].std():
        score_vol += 10

    if atr_30 > df_30m["close"].std():
        score_vol += 10

    total_score += score_vol

    # ===============================
    # 30M STRUCTURE & MOMENTUM (20)
    # ===============================
    score_30m = 0

    if trend_30m == trend_2h:
        score_30m += 8

    if structure_30m:
        score_30m += 6

    if engulf_30m:
        body = abs(df_30m.iloc[-1]["close"] - df_30m.iloc[-1]["open"])
        candle_range = df_30m.iloc[-1]["high"] - df_30m.iloc[-1]["low"]
        if candle_range > 0 and body / candle_range > 0.6:
            score_30m += 6

    total_score += score_30m

    # ===============================
    # 15M ENTRY PRECISION (20)
    # ===============================
    score_15m = 0

    if trend_15m == trend_2h:
        score_15m += 8

    if structure_15m:
        score_15m += 6

    if engulf_15m:
        score_15m += 6

    total_score += score_15m

    # ===============================
    # RISK QUALITY INTELLIGENCE (20)
    # ===============================
    score_risk = 0

    rr_quality = RR_RATIO
    if rr_quality >= 2:
        score_risk += 10

    if trend_2h == trend_30m == trend_15m:
        score_risk += 10

    total_score += score_risk

    debug_log(f"[TOTAL INSTITUTIONAL SCORE] {total_score}/100")

    return total_score

# ============================================================
# TELEGRAM (UNCHANGED)
# ============================================================

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, data=payload)
    except:
        traceback.print_exc()

# ============================================================
# MAIN EXECUTION
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
