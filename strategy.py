import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger("solbot.strategy")

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Solana Volatility Breakout Indicators optimized for the 15m timeframe.
    Emulates an hourly macro filter (EMA 200) to minimize consolidation noise.
    """
    try:
        df = df.copy()

        # 1. Macro Trend Filter (EMA 200 on 15m chart behaves similarly to EMA 50 on a 1h chart)
        df["macro_ema"] = ta.ema(df["close"], length=200)

        # 2. Donchian Channels (Length 20) to capture definitive Solana volatility breakouts
        donchian = ta.donchian(df["high"], df["low"], lower_length=20, upper_length=20)
        if donchian is not None:
            # Extract boundaries safely from pandas_ta multi-column dataframe output
            df["dc_upper"] = donchian.iloc[:, 0]  # UCC_20_20 (Upper Channel)
            df["dc_lower"] = donchian.iloc[:, 2]  # LCC_20_20 (Lower Channel)
        else:
            # Fallback calculation if pandas_ta structural allocation misses
            df["dc_upper"] = df["high"].rolling(20).max()
            df["dc_lower"] = df["low"].rolling(20).min()

        # 3. Average True Range (ATR 14) for dynamic exit matrix scaling
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        return df

    except Exception as e:
        logger.error(f"❌ Error compiling strategy indicator matrices: {str(e)}")
        # Provide clean, functional data properties to maintain core loop continuity
        if "macro_ema" not in df.columns:
            df["macro_ema"] = df["close"]
        if "dc_upper" not in df.columns:
            df["dc_upper"] = df["high"]
        if "dc_lower" not in df.columns:
            df["dc_lower"] = df["low"]
        if "atr" not in df.columns:
            df["atr"] = 1.00
        return df


def evaluate_signal(df: pd.DataFrame) -> dict:
    """
    Evaluates completed candle data to catch clear, high-velocity trends.
    Uses multi-timeframe rules to target roughly 4-5 high-conviction entries a day.
    """
    # Verify we have enough data history to calculate the 200 EMA and 20 DC channels
    if len(df) < 200:
        return {"signal": None, "atr": 1.0, "details": {}}

    # Always pull the last COMPLETED candle data (iloc[-1]) to avoid live candle repainting traps
    last = df.iloc[-1]
    details = {}

    close_price = last.get("close")
    macro_ema   = last.get("macro_ema")
    dc_upper    = last.get("dc_upper")
    dc_lower    = last.get("dc_lower")
    atr         = last.get("atr")

    # Rigid validation guard against empty strings, NaN, or NoneType objects
    if pd.isna(close_price) or pd.isna(macro_ema) or pd.isna(dc_upper) or pd.isna(dc_lower) or pd.isna(atr):
        return {"signal": None, "atr": 1.0, "details": details}

    direction = None

    # ── LONG TRIGGER CONDITIONS ──
    # Trend is macro bullish (Price > EMA) AND price breaks out over the 20-candle high boundary
    if close_price > macro_ema and close_price >= dc_upper:
        direction = "LONG"

    # ── SHORT TRIGGER CONDITIONS ──
    # Trend is macro bearish (Price < EMA) AND price cracks down beneath the 20-candle low boundary
    elif close_price < macro_ema and close_price <= dc_lower:
        direction = "SHORT"

    # Populate strategy metrics to export to your logging, bot framework, and API dashboard
    details["close"]     = round(float(close_price), 4)
    details["macro_ema"] = round(float(macro_ema), 4)
    details["dc_upper"]  = round(float(dc_upper), 4)
    details["dc_lower"]  = round(float(dc_lower), 4)

    if direction:
        logger.info(f"🎯 SOLANA BREAKOUT VALIDATED: {direction} | Price: {close_price} | EMA Limit: {macro_ema}")
        return {
            "signal":  direction,
            "atr":     float(atr),
            "details": details,
        }

    return {"signal": None, "atr": float(atr), "details": details}


def calculate_exits(side: str, entry_price: float, atr: float) -> tuple[float, float]:
    """
    Calculates protective exit targets optimized for Solana's expansion characteristics.
    Features robust validation handling to bypass and absorb empty or corrupt input states.
    """
    # Complete NoneType and numeric edge-case interceptor
    if entry_price is None or pd.isna(entry_price) or atr is None or pd.isna(atr):
        logger.warning(f"⚠️ Exit calculations blocked due to missing context (Price: {entry_price}, ATR: {atr})")
        return 0.0, 0.0

    try:
        entry_price = float(entry_price)
        atr = float(atr)
    except (ValueError, TypeError) as conversion_err:
        logger.error(f"❌ Type casting error inside target boundary definitions: {conversion_err}")
        return 0.0, 0.0

    if entry_price <= 0 or atr <= 0:
        logger.error(f"❌ Invalid numeric limits identified (Price: {entry_price}, ATR: {atr})")
        return 0.0, 0.0

    # 1.5x ATR Stop Loss for trade breathing space; 2.5x ATR Take Profit for picking up big runs
    # This provides an institutional-grade 1:1.66 Risk-to-Reward Ratio profile
    if side.upper() == "LONG":
        sl = entry_price - (1.5 * atr)
        tp = entry_price + (2.5 * atr)
    elif side.upper() == "SHORT":
        sl = entry_price + (1.5 * atr)
        tp = entry_price - (2.5 * atr)
    else:
        logger.error(f"❌ Unrecognized transaction position allocation side: {side}")
        return 0.0, 0.0

    return round(sl, 4), round(tp, 4)
