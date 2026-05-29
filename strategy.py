import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger("solbot.strategy")

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes high-frequency scalping indicators.
    Optimized for short timeframes (3m/5m) to trigger ~10 trades daily.
    """
    try:
        df = df.copy()

        # 1. Fast Supertrend (Period=10, Multiplier=2.0) for high frequency structural switches
        st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=2.0)
        if st is not None:
            # pandas_ta returns columns: [SUPERT_10_2.0, SUPERTd_10_2.0, SUPERTl_10_2.0, SUPERTs_10_2.0]
            # SUPERTd_10_2.0 contains direction: 1 = Bullish, -1 = Bearish
            df["st_direction"] = st["SUPERTd_10_2.0"]
        else:
            df["st_direction"] = 0

        # 2. Fast RSI (Length 7) to track immediate momentum shifts
        df["rsi_fast"] = ta.rsi(df["close"], length=7)

        # 3. ATR (Length 10) for micro-volatility target positioning
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=10)

        return df

    except Exception as e:
        logger.error(f"❌ Critical error computing indicators: {str(e)}")
        # Return fallback columns to keep pipeline functional
        if "st_direction" not in df.columns:
            df["st_direction"] = 0
        if "rsi_fast" not in df.columns:
            df["rsi_fast"] = 50.0
        if "atr" not in df.columns:
            df["atr"] = 0.5
        return df


def evaluate_signal(df: pd.DataFrame) -> dict:
    """
    Scans the latest fast timeframe data for sharp momentum signals.
    """
    last = df.iloc[-1]
    details = {}

    st_dir   = last.get("st_direction", 0)
    rsi_fast = last.get("rsi_fast", None)
    atr      = last.get("atr", None)
    close_pr = last.get("close", None)

    # Core validation to avoid math evaluation on empty or corrupted arrays
    if pd.isna(rsi_fast) or pd.isna(atr) or pd.isna(close_pr) or st_dir == 0:
        return {"signal": None, "atr": atr, "details": details}

    direction = None

    # Fetch previous row data safely to prevent chasing over-extended moves
    prev_rsi = df["rsi_fast"].iloc[-2] if len(df) > 1 else 50.0

    # ── LONG TRIGGER CONDITIONS ──
    if st_dir == 1 and rsi_fast > 45:
        if pd.isna(prev_rsi) or prev_rsi <= 70:  # Avoid entering a top-heavy overbought pump
            direction = "LONG"

    # ── SHORT TRIGGER CONDITIONS ──
    elif st_dir == -1 and rsi_fast < 55:
        if pd.isna(prev_rsi) or prev_rsi >= 30:  # Avoid entering a bottom-heavy oversold dump
            direction = "SHORT"

    details["st_dir"] = int(st_dir)
    details["rsi"]    = round(float(rsi_fast), 2)
    details["close"]  = round(float(close_pr), 4)

    if direction:
        logger.info(f"⚡ FAST SCALP TRIGGERED: {direction} | RSI={rsi_fast:.2f} | Close={close_pr}")
        return {
            "signal":  direction,
            "atr":     float(atr),
            "details": details,
        }

    return {"signal": None, "atr": float(atr), "details": details}


def calculate_exits(side: str, entry_price: float, atr: float) -> tuple[float, float]:
    """
    Calculates tight risk-managed exit levels.
    Features robust validation to catch and neutralize 'NoneType' or empty string values.
    """
    # ── Robust Type & None Protection Guard ──
    if entry_price is None or pd.isna(entry_price) or atr is None or pd.isna(atr):
        logger.warning(f"⚠️ Missing critical pricing context for exit calculation (Price: {entry_price}, ATR: {atr})")
        return 0.0, 0.0

    try:
        # Cast tracking elements strictly into floats to ensure operations succeed
        entry_price = float(entry_price)
        atr = float(atr)
    except (ValueError, TypeError) as err:
        logger.error(f"❌ Failed to parse data values into float properties: {err}")
        return 0.0, 0.0

    # Guard against calculation on a zero or corrupted base
    if entry_price <= 0 or atr <= 0:
        logger.error(f"❌ Invalid entry_price ({entry_price}) or atr ({atr}) range value.")
        return 0.0, 0.0

    # ── High-Frequency Tight Profit/Loss Bands ──
    if side.upper() == "LONG":
        sl = entry_price - (1.2 * atr)
        tp = entry_price + (1.8 * atr)
    elif side.upper() == "SHORT":
        sl = entry_price + (1.2 * atr)
        tp = entry_price - (1.8 * atr)
    else:
        logger.error(f"❌ Unknown transaction layout type identified: {side}")
        return 0.0, 0.0

    return round(sl, 4), round(tp, 4)
