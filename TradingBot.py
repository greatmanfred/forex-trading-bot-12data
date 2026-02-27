import osimport requestsimport timeimport pandas as pdimport numpy as npimport tracebackimport datetime

============================================================

CONFIGURATION

============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

MINUTE_CALL_LIMIT = MINUTE_CALL_LIMIT = 7
DAILY_CALL_LIMIT = 750

CONFIDENCE_THRESHOLD = 68   # Balanced Institutional Mode

DEBUG = True

minute_call_count = 0daily_call_count = 0minute_window_start = time.time()daily_window_start = time.time()

============================================================

SYMBOL LIST

============================================================

SYMBOLS = ["EURUSD","GBPUSD","USDJPY","USDCHF","USDCAD","AUDUSD","NZDUSD","EURGBP","EURJPY","GBPJPY","EURAUD","EURCAD","EURCHF","GBPAUD","GBPCAD","GBPCHF","AUDJPY","AUDCAD","AUDCHF","NZDJPY","NZDCAD","NZDCHF","CADJPY","CADCHF","CHFJPY","USDSGD","USDHKD","EURSGD","GBPSEK","EURNZD"]

============================================================

UTILITIES

============================================================

def debug_log(msg):if DEBUG:print(msg)

============================================================

SESSION FILTER

============================================================

def session_allowed():hour = datetime.datetime.utcnow().hourreturn not (hour >= 22 or hour <= 3)

============================================================

RATE LIMIT CONTROL

============================================================

def check_rate_limit():global minute_call_count, daily_call_countglobal minute_window_start, daily_window_start

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

if daily_call_count >= DAILY_CALL_LIMIT:
    sleep_time = 86400 - (now - daily_window_start)
    if sleep_time > 0:
        time.sleep(sleep_time)

def register_call():global minute_call_count, daily_call_countminute_call_count += 1daily_call_count += 1

============================================================

DATA FETCH

============================================================

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

    if "values" not in data:
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

============================================================

RESAMPLE

============================================================

def resample(df, tf):

df_resampled = df.resample(tf).agg({
    "open":"first",
    "high":"max",
    "low":"min",
    "close":"last"
}).dropna()

if len(df_resampled) < 50:
    return pd.DataFrame()

return df_resampled

============================================================

REGIME MODEL

============================================================

def regime_score(df):

if len(df) < 60:
    return 0

atr = calculate_atr(df).iloc[-1]
atr_mean = calculate_atr(df).rolling(20).mean().iloc[-1]

regime_score = 0

# Expansion regime
if atr > atr_mean * 1.05:
    regime_score += 35

# Trend momentum regime
sma20 = df["close"].rolling(20).mean().iloc[-1]
sma50 = df["close"].rolling(50).mean().iloc[-1]

if sma20 > sma50:
    regime_score += 35

elif sma20 < sma50:
    regime_score += 35

# Compression penalty
if atr < atr_mean * 0.85:
    regime_score -= 20

return max(regime_score, 0)

============================================================

STRUCTURE MODEL

============================================================

def structure_score(df):

if len(df) < 10:
    return 0

highs = df["high"].rolling(6).max().iloc[-1]
highs_prev = df["high"].rolling(6).max().iloc[-6]

lows = df["low"].rolling(6).min().iloc[-1]
lows_prev = df["low"].rolling(6).min().iloc[-6]

score = 0

if highs > highs_prev:
    score += 30

if lows < lows_prev:
    score += 30

bos = detect_bos(df)

if bos is not None:
    score += 40

return score

============================================================

BOS DETECTION

============================================================

def detect_bos(df):

if len(df) < 10:
    return None

if df["high"].iloc[-1] > df["high"].iloc[-6]:
    return "BULLISH_BOS"

if df["low"].iloc[-1] < df["low"].iloc[-6]:
    return "BEARISH_BOS"

return None

============================================================

REACTION MOMENTUM MODEL

============================================================

def reaction_score(df):

if len(df) < 3:
    return 0

last = df.iloc[-1]
prev = df.iloc[-2]

score = 0

# Candle momentum reaction
if last["close"] > last["open"] and prev["close"] < prev["open"]:
    score += 50

if last["close"] < last["open"] and prev["close"] > prev["open"]:
    score += 50

return score

============================================================

ATR ENGINE

============================================================

def calculate_atr(df, period=14):

high_low = df["high"] - df["low"]
high_close = np.abs(df["high"] - df["close"].shift())
low_close = np.abs(df["low"] - df["close"].shift())

tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

return tr.rolling(period).mean().fillna(0)

============================================================

TELEGRAM

============================================================

def send_telegram(message):

try:

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID,
              "text": message},
        timeout=10
    )

except:
    traceback.print_exc()

============================================================

MAIN ENGINE

============================================================

def main():

if not session_allowed():
    return

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

    # Layer Scoring

    regime = regime_score(df_2h)

    structure = structure_score(df_30m)

    reaction = reaction_score(df_15m)

    final_score = (
        regime * 0.40 +
        structure * 0.30 +
        reaction * 0.30
    )

    direction = "BUY" if df_2h["close"].iloc[-1] > df_2h["open"].iloc[-1] else "SELL"

    if final_score >= CONFIDENCE_THRESHOLD:

        entry = df_15m["close"].iloc[-1]

        atr = calculate_atr(df_15m).iloc[-1]

        if direction == "BUY":
            swing_low = df_15m["low"].rolling(6).min().iloc[-1]
            sl = max(swing_low * 0.999, entry - atr * 1.2)

            tp = entry + min(
                (entry - sl) * 1.8,
                atr * 2.5
            )

        else:
            swing_high = df_15m["high"].rolling(6).max().iloc[-1]
            sl = min(swing_high * 1.001, entry + atr * 1.2)

            tp = entry - min(
                (sl - entry) * 1.8,
                atr * 2.5
            )

        send_telegram(
            f"{symbol} {direction}\n"
            f"Confidence: {final_score:.2f}%\n"
            f"Entry: {entry:.5f}\n"
            f"SL: {sl:.5f}\n"
            f"TP: {tp:.5f}"
        )

    else:
        invalid_signals.append(f"{symbol} {final_score:.2f}")

if invalid_signals:
    send_telegram("Low Confidence Signals:\n" + "\n".join(invalid_signals))

if name == "main":main()
