import os
import requests
import time
import pandas as pd
import numpy as np
import traceback
import datetime
from typing import List, Dict, Optional, Tuple

# ============================================================
# CONFIGURATION & INFRASTRUCTURE
# ============================================================

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
    
    # TwelveData Free Tier: 8 calls/min, 800/day
    MINUTE_CALL_LIMIT = 8
    DAILY_CALL_LIMIT = 800
    
    CONFIDENCE_THRESHOLD = 65.0
    
    SYMBOLS = [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD",
        "AUD/USD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
        "EURAUD", "EURCAD", "EURCHF", "GBPAUD", "GBPCAD",
        "GBPCHF", "AUDJPY", "AUDCAD", "AUDCHF", "NZDJPY",
        "NZDCAD", "NZDCHF", "CADJPY", "CADCHF", "CHFJPY",
        "USDSGD", "USDHKD", "EURSGD", "GBPSEK", "EURNZD"
    ]
    
    # Weights for the scoring model (Hierarchical)
    WEIGHT_MACRO = 0.40      # 35-45%
    WEIGHT_STRUCTURAL = 0.35 # 25-35%
    WEIGHT_EXECUTION = 0.25  # 20-30%

class RateLimiter:
    def __init__(self):
        self.minute_call_count = 0
        self.daily_call_count = 0
        self.minute_window_start = time.time()
        self.daily_window_start = time.time()

    def check_and_wait(self):
        now = time.time()
        # Reset minute window if 60 seconds passed
        if now - self.minute_window_start >= 60:
            self.minute_call_count = 0
            self.minute_window_start = now
        # Reset daily window if 86400 seconds (24 hours) passed
        if now - self.daily_window_start >= 86400:
            self.daily_call_count = 0
            self.daily_window_start = now
            
        # If minute limit reached, sleep until next minute window
        if self.minute_call_count >= Config.MINUTE_CALL_LIMIT:
            sleep_time = 60 - (now - self.minute_window_start) + 1 # +1 to ensure we are in the next minute
            if sleep_time > 0:
                print(f"Rate limit: Sleeping for {sleep_time:.2f}s")
                time.sleep(sleep_time)
                self.minute_call_count = 0 # Reset after sleeping
                self.minute_window_start = time.time()
                
        # If daily limit reached, stop for the day
        if self.daily_call_count >= Config.DAILY_CALL_LIMIT:
            print("Daily rate limit reached. Stopping for today.")
            return False
        return True

    def register_call(self):
        self.minute_call_count += 1
        self.daily_call_count += 1

class NotificationManager:
    @staticmethod
    def send_telegram(message: str):
        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            print(f"Telegram not configured. Message: {message}")
            return
        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": Config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
        except Exception:
            traceback.print_exc()

# ============================================================
# TECHNICAL ANALYSIS ENGINE
# ============================================================

class TA:
    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        # Ensure enough data for ATR calculation
        if len(df) < period: return pd.Series([0.0] * len(df), index=df.index)
        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean().fillna(0.0)

    @staticmethod
    def get_fractal_swings(df: pd.DataFrame, window: int = 2) -> List[Dict]:
        """
        Detects fractal pivots: higher/lower than 'window' neighbors on both sides.
        A valid swing high is higher than 'window' candles to its left and 'window' candles to its right.
        A valid swing low is lower than 'window' candles to its left and 'window' candles to its right.
        """
        swings = []
        # Ensure enough data points for the given window size
        if len(df) < window * 2 + 1: return swings
        highs = df["high"].values
        lows = df["low"].values
        times = df.index
        
        for i in range(window, len(df) - window):
            # Check for Swing High
            is_high = True
            for j in range(1, window + 1):
                if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]:
                    is_high = False
                    break
            if is_high:
                swings.append({"type": "high", "price": highs[i], "index": i, "time": times[i]})
                
            # Check for Swing Low
            is_low = True
            for j in range(1, window + 1):
                if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]:
                    is_low = False
                    break
            if is_low:
                swings.append({"type": "low", "price": lows[i], "index": i, "time": times[i]})
        return swings

    @staticmethod
    def detect_bos_choch(df: pd.DataFrame, swings: List[Dict]) -> Dict:
        """
        Detects Break of Structure (BOS) and Change of Character (CHOCH).
        BOS confirms trend continuation, CHOCH suggests potential reversal.
        Micro-points are assigned based on the detected state.
        """
        if len(swings) < 4:
            return {"state": "NEUTRAL", "trend": "SIDEWAYS", "points": 0}
            
        last_close = df["close"].iloc[-1]
        
        # Filter and get recent swing highs and lows
        highs = [s for s in swings if s["type"] == "high"]
        lows = [s for s in swings if s["type"] == "low"]
        
        if not highs or not lows or len(highs) < 2 or len(lows) < 2:
            return {"state": "NEUTRAL", "trend": "SIDEWAYS", "points": 0}
            
        # Get the last two significant swing highs and lows
        last_h = highs[-1]["price"]
        prev_h = highs[-2]["price"]
        last_l = lows[-1]["price"]
        prev_l = lows[-2]["price"]
        
        # Determine current trend based on higher highs/lows or lower highs/lows
        trend = "SIDEWAYS"
        if last_h > prev_h and last_l > prev_l: # Higher highs and higher lows
            trend = "BULLISH"
        elif last_h < prev_h and last_l < prev_l: # Lower highs and lower lows
            trend = "BEARISH"
            
        state = "NEUTRAL"
        points = 0
        
        # BOS: Price breaks above last swing high in an uptrend or below last swing low in a downtrend
        if trend == "BULLISH" and last_close > last_h:
            state = "BOS"
            points = 40 # Micro-points for BOS
        elif trend == "BEARISH" and last_close < last_l:
            state = "BOS"
            points = 40 # Micro-points for BOS
        # CHOCH: Price breaks below last swing low in an uptrend or above last swing high in a downtrend
        elif trend == "BULLISH" and last_close < last_l:
            state = "CHOCH"
            points = 60 # Micro-points for CHOCH (stronger signal of change)
        elif trend == "BEARISH" and last_close > last_h:
            state = "CHOCH"
            points = 60 # Micro-points for CHOCH
            
        return {"state": state, "trend": trend, "points": points}

    @staticmethod
    def detect_aoi(df: pd.DataFrame, swings: List[Dict], timeframe_label: str) -> List[Dict]:
        """
        Institutional-grade Area of Interest (AOI) detection with durability and reaction strength.
        Rules: Body rejection, wick rejection, structural respect (touched/respected >= 3 times).
        """
        aois = []
        if not swings: return aois
        
        # Group nearby swing prices into potential zones
        potential_zones = []
        # Sort swings by price to facilitate clustering
        sorted_swings = sorted(swings, key=lambda x: x["price"])
        
        if not sorted_swings: return aois

        current_cluster = [sorted_swings[0]["price"]]
        for i in range(1, len(sorted_swings)):
            # If current swing is within 0.1% of the previous one, cluster them
            if abs(sorted_swings[i]["price"] - current_cluster[-1]) / current_cluster[-1] < 0.001:
                current_cluster.append(sorted_swings[i]["price"])
            else:
                potential_zones.append(current_cluster)
                current_cluster = [sorted_swings[i]["price"]]
        potential_zones.append(current_cluster) # Add the last cluster

        for cluster in potential_zones:
            # Use the average price of the cluster as the zone center
            zone_price = np.mean(cluster)
            # Define a zone around the cluster average (e.g., 0.05% above/below)
            zone_min = zone_price * 0.9995
            zone_max = zone_price * 1.0005
            
            touches = 0
            rejections = 0
            reaction_strength_sum = 0.0
            reaction_decay_sum = 0.0
            
            # Check historical data for respect and calculate durability metrics
            for i in range(len(df)):
                low = df["low"].iloc[i]
                high = df["high"].iloc[i]
                open_p = df["open"].iloc[i]
                close_p = df["close"].iloc[i]
                candle_range = high - low
                
                # Touch check: if any part of the candle touches the zone
                if low <= zone_max and high >= zone_min:
                    touches += 1
                    
                    # Rejection check: Body stays outside or wick is significant
                    body_min = min(open_p, close_p)
                    body_max = max(open_p, close_p)
                    
                    is_body_rejection = (body_max < zone_min or body_min > zone_max)
                    is_wick_rejection = False
                    if candle_range > 0: # Avoid division by zero
                        # Wick penetrates but body closes away, indicating rejection
                        if (high > zone_max and close_p < zone_max) or (low < zone_min and close_p > zone_min):
                            is_wick_rejection = True
                    
                    if is_body_rejection or is_wick_rejection:
                        rejections += 1
                        
                        # Reaction Strength Decay Model
                        # Measure rejection momentum (displacement away from zone)
                        displacement = 0.0
                        if close_p > zone_max: # Bullish rejection from resistance
                            displacement = (close_p - zone_max) / candle_range if candle_range > 0 else 0
                        elif close_p < zone_min: # Bearish rejection from support
                            displacement = (zone_min - close_p) / candle_range if candle_range > 0 else 0
                        
                        # Exponential-like decay: more recent reactions contribute more
                        decay_factor = np.exp(-0.05 * (len(df) - 1 - i)) # Decay rate 0.05, i is index from start
                        reaction_strength_sum += displacement * decay_factor
                        reaction_decay_sum += decay_factor
            
            # Calculate average reaction strength, weighted by decay
            avg_reaction_strength = reaction_strength_sum / reaction_decay_sum if reaction_decay_sum > 0 else 0

            # AOI Durability Score: based on touches, rejections, and structural respect continuity
            # Structural respect continuity is implicitly captured by multiple touches and rejections
            durability_score = 0
            if touches >= 3: durability_score += 30 # Minimum 3 touches
            if rejections >= 2: durability_score += 40 # Strong rejection behavior
            if avg_reaction_strength > 0.2: durability_score += 30 # Meaningful displacement
            
            if touches >= 3 and rejections >= 2 and durability_score > 50: # Valid AOI criteria
                aois.append({
                    "price": zone_price,
                    "min": zone_min,
                    "max": zone_max,
                    "touches": touches,
                    "rejections": rejections,
                    "durability_score": durability_score,
                    "avg_reaction_strength": avg_reaction_strength,
                    "tf": timeframe_label
                })
        
        # Deduplicate zones based on proximity (already handled by clustering, but a final check)
        unique_aois = []
        for a in aois:
            # Check if this AOI is too close to an already added unique AOI
            if not any(abs(a["price"] - u["price"]) / u["price"] < 0.001 for u in unique_aois):
                unique_aois.append(a)
        return unique_aois

    @staticmethod
    def calculate_zone_entropy(df: pd.DataFrame, aoi_zones: List[Dict]) -> float:
        """
        Measures the randomness of price visits inside AOI zones (Zone Visitation Entropy Score).
        Low entropy = stable institutional zone, High entropy = unstable zone penalty.
        """
        if not aoi_zones: return 0.0
        
        total_entropy_score = 0.0
        for zone in aoi_zones:
            zone_min = zone["min"]
            zone_max = zone["max"]
            
            # Count entries and exits from the zone
            entries = 0
            exits = 0
            inside_zone = False
            
            for i in range(1, len(df)):
                prev_close = df["close"].iloc[i-1]
                current_close = df["close"].iloc[i]
                
                # Check if price entered the zone
                if not inside_zone and (zone_min <= current_close <= zone_max) and not (zone_min <= prev_close <= zone_max):
                    entries += 1
                    inside_zone = True
                # Check if price exited the zone
                elif inside_zone and not (zone_min <= current_close <= zone_max) and (zone_min <= prev_close <= zone_max):
                    exits += 1
                    inside_zone = False
            
            # Simple entropy approximation: more entries/exits without clear direction implies higher entropy
            # A stable zone should have fewer, decisive entries/exits, or sustained presence
            # Penalize frequent crossing back and forth
            if entries + exits > 0:
                # Ratio of entries/exits to total candles in zone interaction
                # Lower ratio (fewer crosses) for a given number of touches means lower entropy
                zone_interaction_candles = sum(1 for i in range(len(df)) if zone_min <= df["close"].iloc[i] <= zone_max)
                if zone_interaction_candles > 0:
                    entropy_measure = (entries + exits) / zone_interaction_candles
                    total_entropy_score += (1 - min(entropy_measure, 1.0)) * 100 # Convert to score (0-100), lower entropy = higher score
                
        return total_entropy_score / len(aoi_zones) if aoi_zones else 0.0

# ============================================================
# CORE TRADING ENGINE
# ============================================================

class TradingBot:
    def __init__(self):
        self.limiter = RateLimiter()
        self.notifier = NotificationManager()

    def fetch_5m_data_adaptive(self, symbol: str) -> Optional[pd.DataFrame]:
        """Adaptive historical depth retrieval: 4500 -> 4000 -> 3000.
        Fetches 5-minute data, retrying with smaller depths if initial fetch is insufficient.
        Ensures enough data for higher timeframe construction and structural analysis.
        """
        depths = [4500, 4000, 3000]
        for depth in depths:
            if not self.limiter.check_and_wait(): return None
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": symbol,
                "interval": "5min",
                "outputsize": depth,
                "apikey": Config.TWELVEDATA_API_KEY
            }
            try:
                response = requests.get(url, params=params, timeout=20)
                self.limiter.register_call()
                data = response.json()
                # Check if 'values' key exists and if enough data is returned (e.g., > 500 candles)
                if "values" in data and isinstance(data["values"], list) and len(data["values"]) > 500:
                    df = pd.DataFrame(data["values"])
                    for col in ["open", "high", "low", "close"]:
                        # Convert to numeric, coercing errors to NaN, then drop NaNs
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df.set_index("datetime", inplace=True)
                    return df.sort_index()
                print(f"Insufficient data for {symbol} at depth {depth}. Retrying...")
            except Exception as e:
                print(f"Error fetching data for {symbol} at depth {depth}: {e}")
                traceback.print_exc()
        return None

    def resample_data(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """Mathematically derive higher timeframes from 5m data.
        Aggregates 5-minute candles into specified higher timeframe candles.
        """
        # Ensure the DataFrame is not empty before resampling
        if df.empty: return pd.DataFrame()
        
        resampled = df.resample(interval).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last"
        }).dropna()
        return resampled

    def get_macro_layer(self, df_2h: pd.DataFrame) -> Dict:
        """2H Macro Dominance Layer (40% weight contribution).
        Evaluates trend alignment using SMAs and volatility expansion using ATR.
        Assigns micro-points based on these macro-level indicators.
        """
        micro_points = 0
        # Ensure enough data for SMA calculations
        if len(df_2h) < 50: # Need at least 50 candles for SMA50
            return {"score": 0, "trend": "SIDEWAYS"}

        sma20 = df_2h["close"].rolling(20).mean().iloc[-1]
        sma50 = df_2h["close"].rolling(50).mean().iloc[-1]
        
        # SMA Trend Alignment (50 micro points if aligned and separated)
        # Check for minimal separation to confirm alignment, not just crossing
        if sma50 != 0 and abs(sma20 - sma50) / sma50 > 0.0005:
            micro_points += 50
            
        # ATR Volatility Expansion (50 micro points if current ATR > average ATR)
        atr_series = TA.calculate_atr(df_2h)
        if len(atr_series) < 20: # Need enough ATR values for its rolling mean
            current_atr = atr_series.iloc[-1] if not atr_series.empty else 0
            atr_sma = current_atr # Fallback if not enough data for rolling mean
        else:
            current_atr = atr_series.iloc[-1]
            atr_sma = atr_series.rolling(20).mean().iloc[-1]

        if current_atr > atr_sma and atr_sma > 0: # Avoid division by zero
            micro_points += 50
            
        return {"score": micro_points, "trend": "BULLISH" if sma20 > sma50 else "BEARISH"}

    def get_structural_layer(self, df_30m: pd.DataFrame) -> Dict:
        """30M Structural Compression Layer (35% weight contribution).
        Detects BOS/CHOCH and volatility contraction. Integrates AOI durability and entropy.
        """
        micro_points = 0
        # Ensure enough data for swing detection
        if len(df_30m) < 10: # Minimum for fractal swings with window=2
            return {"score": 0, "swings": [], "analysis": {"state": "NEUTRAL", "trend": "SIDEWAYS", "points": 0}}

        swings = TA.get_fractal_swings(df_30m)
        analysis = TA.detect_bos_choch(df_30m, swings)
        
        # BOS/CHOCH Confirmation micro-points
        micro_points += analysis["points"]
        
        # Volatility Contraction (Compression) micro-points
        # Ensure enough data for rolling mean calculations
        if len(df_30m) >= 10:
            recent_vol = (df_30m["high"] - df_30m["low"]).rolling(10).mean().iloc[-1]
            prev_vol = (df_30m["high"] - df_30m["low"]).rolling(10).mean().iloc[-10]
            if recent_vol < prev_vol and prev_vol > 0: # Check for actual contraction and avoid div by zero
                micro_points += 40
            
        return {"score": min(micro_points, 100), "swings": swings, "analysis": analysis}

    def get_execution_layer(self, df_15m: pd.DataFrame) -> Dict:
        """15M Execution Precision Layer (25% weight contribution).
        Detects impulse reactions, momentum displacement, and enhanced liquidity sweeps.
        """
        micro_points = 0
        direction = "NEUTRAL"
        
        # Ensure enough data for calculations
        if len(df_15m) < 2: return {"score": 0, "direction": "NEUTRAL", "swings": []}

        last = df_15m.iloc[-1]
        prev = df_15m.iloc[-2]
        
        # Impulse Reaction / Momentum Displacement (40 micro points)
        body = abs(last["close"] - last["open"])
        # Ensure enough data for rolling mean
        if len(df_15m) >= 20:
            avg_body = abs(df_15m["close"] - df_15m["open"]).rolling(20).mean().iloc[-1]
            if avg_body > 0 and body > avg_body * 1.8: # Check for significant impulse
                micro_points += 40
                direction = "BUY" if last["close"] > last["open"] else "SELL"
            
        # Enhanced Liquidity Sweep Signature (60 micro points)
        # Use swings from previous candles to avoid look-ahead bias
        swings = TA.get_fractal_swings(df_15m[:-1])
        if swings:
            # Get recent swing high/low for sweep detection
            last_h = max([s["price"] for s in swings if s["type"] == "high"][-3:] or [0.0])
            last_l = min([s["price"] for s in swings if s["type"] == "low"][-3:] or [float('inf')])
            
            candle_range = last["high"] - last["low"]
            
            # Bearish Sweep: Price sweeps previous high then closes below it
            if last["high"] > last_h and last["close"] < last_h:
                # Rejection Geometry: Displacement must show meaningful structural rejection
                # Displacement is how much price moved away from the swept level relative to candle range
                if candle_range > 0:
                    displacement = (last_h - last["close"]) / candle_range
                    if displacement > 0.3: # Threshold for meaningful rejection
                        micro_points += 60
                        direction = "SELL"
            # Bullish Sweep: Price sweeps previous low then closes above it
            elif last["low"] < last_l and last["close"] > last_l:
                if candle_range > 0:
                    displacement = (last["close"] - last_l) / candle_range
                    if displacement > 0.3: # Threshold for meaningful rejection
                        micro_points += 60
                        direction = "BUY"
                    
        return {"score": min(micro_points, 100), "direction": direction, "swings": swings}

    def get_aoi_confluence_score(self, df_15m, df_30m, df_2h) -> float:
        """Calculates AOI confluence score based on durability, reaction strength, and nesting.
        Higher timeframe levels dominate lower timeframe probability scoring.
        """
        aoi_2h = TA.detect_aoi(df_2h, TA.get_fractal_swings(df_2h), "2H")
        aoi_30m = TA.detect_aoi(df_30m, TA.get_fractal_swings(df_30m), "30M")
        aoi_15m = TA.detect_aoi(df_15m, TA.get_fractal_swings(df_15m), "15M")
        
        aoi_confluence_points = 0
        last_price = df_15m["close"].iloc[-1]
        
        # 1. Higher timeframe levels dominate: 2H AOI respect
        for aoi in aoi_2h:
            if aoi["min"] <= last_price <= aoi["max"]:
                # If current price is within a durable 2H AOI, add significant points
                aoi_confluence_points += aoi["durability_score"] * 0.5 # 50% of durability score
                
                # Check for aligned 30M and 15M zones within the 2H AOI
                for aoi30 in aoi_30m:
                    if aoi["min"] <= aoi30["price"] <= aoi["max"]:
                        aoi_confluence_points += aoi30["durability_score"] * 0.3 # 30% of 30M durability
                        for aoi15 in aoi_15m:
                            if aoi30["min"] <= aoi15["price"] <= aoi30["max"]:
                                aoi_confluence_points += aoi15["durability_score"] * 0.2 # 20% of 15M durability
                                break # Found nesting, move to next 30M
                        break # Found nesting, move to next 2H
        
        # Also consider direct 30M and 15M AOIs if not nested in 2H
        for aoi in aoi_30m:
            if aoi["min"] <= last_price <= aoi["max"]:
                aoi_confluence_points += aoi["durability_score"] * 0.4 # Direct 30M contribution
        for aoi in aoi_15m:
            if aoi["min"] <= last_price <= aoi["max"]:
                aoi_confluence_points += aoi["durability_score"] * 0.2 # Direct 15M contribution

        return min(aoi_confluence_points, 100.0)

    def get_zone_visitation_entropy_score(self, df_15m, df_30m, df_2h) -> float:
        """Calculates Zone Visitation Entropy Score for each timeframe and averages them.
        Low entropy (stable zone) contributes positively, high entropy (unstable) penalizes.
        """
        entropy_score_2h = TA.calculate_zone_entropy(df_2h, TA.detect_aoi(df_2h, TA.get_fractal_swings(df_2h), "2H"))
        entropy_score_30m = TA.calculate_zone_entropy(df_30m, TA.detect_aoi(df_30m, TA.get_fractal_swings(df_30m), "30M"))
        entropy_score_15m = TA.calculate_zone_entropy(df_15m, TA.detect_aoi(df_15m, TA.get_fractal_swings(df_15m), "15M"))
        
        # Average entropy score across timeframes
        total_scores = [s for s in [entropy_score_2h, entropy_score_30m, entropy_score_15m] if s is not None]
        if not total_scores: return 0.0
        
        # Normalize to 0-100 scale, where 100 is low entropy (stable)
        return np.mean(total_scores)

    def run_scan(self):
        """Single execution scan for external scheduling. Scans all symbols and exits cleanly.
        Includes session filter to operate only during active trading hours.
        """
        print(f"Starting Institutional Scan: {datetime.datetime.now()}")
        
        # Session Filter: Only trade during active market hours (e.g., London/NY overlap)
        # Hours 3-21 UTC are generally active (22-2 UTC are less active)
        hour = datetime.datetime.utcnow().hour
        if hour >= 22 or hour <= 2:
            print("Outside active trading session (UTC 3-21). Exiting.")
            return

        for symbol in Config.SYMBOLS:
            try:
                print(f"Scanning {symbol}...")
                # 1. Adaptive Data Fetch (5m only) - single API call per symbol
                df_5m = self.fetch_5m_data_adaptive(symbol)
                if df_5m is None or df_5m.empty: 
                    print(f"Skipping {symbol}: No sufficient 5m data.")
                    continue
                
                # 2. Mathematical Resampling to higher timeframes
                df_15m = self.resample_data(df_5m, "15min")
                df_30m = self.resample_data(df_5m, "30min")
                df_2h = self.resample_data(df_5m, "2h")
                
                # Ensure enough data for analysis on all timeframes
                if df_15m.empty or df_30m.empty or df_2h.empty or len(df_2h) < 50 or len(df_30m) < 50 or len(df_15m) < 50:
                    print(f"Skipping {symbol}: Insufficient resampled data for analysis.")
                    continue
                
                # 3. Layer Analysis - Micro-point contributions
                macro_analysis = self.get_macro_layer(df_2h)
                struct_analysis = self.get_structural_layer(df_30m)
                exec_analysis = self.get_execution_layer(df_15m)
                
                # 4. AOI Confluence and Zone Entropy Scoring
                aoi_confluence_score = self.get_aoi_confluence_score(df_15m, df_30m, df_2h)
                zone_entropy_score = self.get_zone_visitation_entropy_score(df_15m, df_30m, df_2h)
                
                # 5. Micro-Point Confluence Scoring - Summing and weighting
                # Total micro-points from each layer
                total_macro_points = macro_analysis["score"]
                total_structural_points = struct_analysis["score"] + aoi_confluence_score + zone_entropy_score # AOI and Entropy blend into structural
                total_execution_points = exec_analysis["score"]
                
                # Normalize scores to 0-100 range if they exceed it (though individual components are capped)
                total_macro_points = min(total_macro_points, 100)
                total_structural_points = min(total_structural_points, 100)
                total_execution_points = min(total_execution_points, 100)

                # Final Weighted Confidence Calculation
                confidence = (total_macro_points * Config.WEIGHT_MACRO) + \
                             (total_structural_points * Config.WEIGHT_STRUCTURAL) + \
                             (total_execution_points * Config.WEIGHT_EXECUTION)
                
                direction = exec_analysis["direction"]
                
                # Directional Gating: 2H layer must be evaluated first but must NOT block scanning.
                # If macro trend doesn't align, reduce confidence (soft filter).
                if direction == "BUY" and macro_analysis["trend"] != "BULLISH": 
                    confidence *= 0.8 # 20% penalty for misalignment
                elif direction == "SELL" and macro_analysis["trend"] != "BEARISH": 
                    confidence *= 0.8 # 20% penalty for misalignment
                
                print(f"Symbol: {symbol} | Confidence: {confidence:.2f}% | Direction: {direction}")
                
                # Trade signal is valid when confidence >= 65% and a clear direction is identified
                if confidence >= Config.CONFIDENCE_THRESHOLD and direction != "NEUTRAL":
                    self.execute_signal(symbol, direction, confidence, df_15m, df_30m, df_2h)
                    
            except Exception as e:
                print(f"Error processing {symbol}: {e}")
                traceback.print_exc()
        
        print("Scan complete. Exiting.")

    def execute_signal(self, symbol, direction, confidence, df15, df30, df2h):
        """Executes a trade signal by calculating Entry, Stop Loss, and Take Profit.
        Applies Risk Geometry Engine and sends Telegram notification.
        """
        entry = df15["close"].iloc[-1]
        atr_series = TA.calculate_atr(df15)
        if atr_series.empty or atr_series.iloc[-1] == 0: 
            print(f"Cannot execute signal for {symbol}: ATR is zero or empty.")
            return
        atr = atr_series.iloc[-1]
        
        swings15 = TA.get_fractal_swings(df15)
        swings30 = TA.get_fractal_swings(df30)
        swings2h = TA.get_fractal_swings(df2h)
        
        # Risk Geometry Engine
        sl = entry # Initialize SL
        tp = entry # Initialize TP

        if direction == "BUY":
            # SL must be below 15M swing structure
            # Find the most recent swing low below the current entry
            valid_swing_lows = [s["price"] for s in reversed(swings15) if s["type"] == "low" and s["price"] < entry]
            last_low = valid_swing_lows[0] if valid_swing_lows else (entry - atr * 0.5) # Fallback if no recent swing low
            sl = last_low - (atr * 0.1) # Add a small buffer below the swing low
            
            # Constraint: SL distance must be between 0.8xATR and 1.5xATR
            sl_dist = entry - sl
            if sl_dist < 0.8 * atr: sl = entry - (0.8 * atr)
            elif sl_dist > 1.5 * atr: sl = entry - (1.5 * atr)
            
            # TP Engine: Project TP toward nearest opposing structural zone
            # Nearest opposing 30M swing high above entry
            opposing_30m_highs = [s["price"] for s in swings30 if s["type"] == "high" and s["price"] > entry]
            opposing_30m = min(opposing_30m_highs) if opposing_30m_highs else (entry + atr * 3) # Fallback
            tp = opposing_30m
            
            # Hard cap TP below opposing 2H major structure
            opposing_2h_highs = [s["price"] for s in swings2h if s["type"] == "high" and s["price"] > entry]
            opposing_2h = min(opposing_2h_highs) if opposing_2h_highs else float('inf') # Fallback to infinity
            if tp > opposing_2h: tp = opposing_2h * 0.9995 # Cap slightly below 2H resistance

        else: # SELL direction
            # SL must be above 15M swing structure
            # Find the most recent swing high above the current entry
            valid_swing_highs = [s["price"] for s in reversed(swings15) if s["type"] == "high" and s["price"] > entry]
            last_high = valid_swing_highs[0] if valid_swing_highs else (entry + atr * 0.5) # Fallback if no recent swing high
            sl = last_high + (atr * 0.1) # Add a small buffer above the swing high
            
            # Constraint: SL distance must be between 0.8xATR and 1.5xATR
            sl_dist = sl - entry
            if sl_dist < 0.8 * atr: sl = entry + (0.8 * atr)
            elif sl_dist > 1.5 * atr: sl = entry + (1.5 * atr)
            
            # TP Engine: Project TP toward nearest opposing structural zone
            # Nearest opposing 30M swing low below entry
            opposing_30m_lows = [s["price"] for s in swings30 if s["type"] == "low" and s["price"] < entry]
            opposing_30m = max(opposing_30m_lows) if opposing_30m_lows else (entry - atr * 3) # Fallback
            tp = opposing_30m
            
            # Hard cap TP above opposing 2H major structure
            opposing_2h_lows = [s["price"] for s in swings2h if s["type"] == "low" and s["price"] < entry]
            opposing_2h = max(opposing_2h_lows) if opposing_2h_lows else 0.0 # Fallback to zero
            if tp < opposing_2h: tp = opposing_2h * 1.0005 # Cap slightly above 2H support

        # Calculate Risk-Reward Ratio, handle potential division by zero
        rr_ratio = (abs(tp - entry) / abs(entry - sl)) if abs(entry - sl) > 0 else 0.0

        msg = (
            f"ðŸš€ *INSTITUTIONAL SIGNAL: {symbol}*\n"
            f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
            f"Direction: *{direction}*\n"
            f"Confidence: `{confidence:.2f}%`\n"
            f"Entry: `{entry:.5f}`\n"
            f"Stop Loss: `{sl:.5f}`\n"
            f"Take Profit: `{tp:.5f}`\n"
            f"RR Ratio: `1:{rr_ratio:.2f}`\n"
            f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”"
        )
        self.notifier.send_telegram(msg)

if __name__ == "__main__":
    bot = TradingBot()
    bot.run_scan()
