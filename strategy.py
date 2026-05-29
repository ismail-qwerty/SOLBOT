# ─────────────────────────────────────────────
#  SolBot v2.0 — Fast Scalping Strategy
# ─────────────────────────────────────────────

import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger("solbot.strategy")

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute high-frequency fast scalping indicators."""
    df = df.copy()

    # 1. Fast Supertrend (Period=10, Multiplier=2.0) for frequent structural flips
    st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=2.0)
    if st is not None:
        df["st_direction"] = st["SUPERTd_10_2.0"] # 1 = Bullish, -1 = Bearish
    else:
        df["st_direction"] = 0

    # 2. Fast RSI (Length 7) for immediate momentum tracking
    df["rsi_fast"] = ta.rsi(df["close"], length=7)

    # 3. ATR for tight dynamic exits
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=10)

    return df


def evaluate_signal(df: pd.DataFrame) -> dict:
    """Fast momentum scanner aiming for multiple daily trades."""
    last = df.iloc[-1]
    details = {}

    st_dir   = last["st_direction"]
    rsi_fast = last["rsi_fast"]
    atr      = last["atr"]

    if pd.isna(rsi_fast) or pd.isna(atr) or st_dir == 0:
        logger.debug("Indicators not ready or invalid")
        return {"signal": None, "atr": atr if not pd.isna(atr) else 0, "details": details}

    direction = None

    # ── LONG TRIGGER ──
    if st_dir == 1 and rsi_fast > 45:
        if df["rsi_fast"].iloc[-2] <= 70: # Ensure we aren't chasing an exhausted pump
            direction = "LONG"
            logger.info(f"✅ LONG conditions met: Supertrend=BULLISH | RSI={rsi_fast:.1f} > 45")
        else:
            logger.debug(f"⚠️ LONG blocked: RSI exhaustion (prev RSI > 70)")

    # ── SHORT TRIGGER ──
    elif st_dir == -1 and rsi_fast < 55:
        if df["rsi_fast"].iloc[-2] >= 30: # Ensure we aren't chasing an exhausted dump
            direction = "SHORT"
            logger.info(f"✅ SHORT conditions met: Supertrend=BEARISH | RSI={rsi_fast:.1f} < 55")
        else:
            logger.debug(f"⚠️ SHORT blocked: RSI exhaustion (prev RSI < 30)")
    else:
        logger.debug(f"❌ No signal: ST_dir={st_dir} | RSI={rsi_fast:.1f}")

    details["st_dir"] = int(st_dir)
    details["rsi"]    = round(float(rsi_fast), 2)

    if direction:
        logger.info(f"⚡ SCALP SIGNAL TRIGGERED: {direction} | RSI={rsi_fast:.1f} | ATR={atr:.4f}")
        return {
            "signal":  direction,
            "atr":     float(atr),
            "details": details,
        }

    return {"signal": None, "atr": float(atr), "details": details}


def calculate_exits(side: str, entry_price: float, atr: float) -> tuple[float, float]:
    """Tight dynamic exits to secure fast profits and maintain quick turnover."""
    if side == "LONG":
        sl = entry_price - 1.2 * atr
        tp = entry_price + 1.8 * atr
    else:
        sl = entry_price + 1.2 * atr
        tp = entry_price - 1.8 * atr
    return round(sl, 4), round(tp, 4)
