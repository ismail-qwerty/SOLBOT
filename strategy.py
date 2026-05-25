# ─────────────────────────────────────────────
#  SolBot v2.0 — Strategy Engine
# ─────────────────────────────────────────────

import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone
import logging

logger = logging.getLogger("solbot.strategy")


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all required indicators on a OHLCV DataFrame.
    Expected columns: open, high, low, close, volume
    """
    df = df.copy()

    # EMA 200
    df["ema200"] = ta.ema(df["close"], length=200)

    # RSI 14
    df["rsi"] = ta.rsi(df["close"], length=14)

    # MACD (12, 26, 9)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"]        = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]

    # Volume 20-period SMA
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # ADX 14
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    df["adx"] = adx_df["ADX_14"]

    # ATR 14
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    return df


def _is_trading_window() -> bool:
    """Return True if current UTC time is between 08:00 and 22:00."""
    now_utc = datetime.now(timezone.utc)
    return 8 <= now_utc.hour < 22


def _fresh_macd_crossover(df: pd.DataFrame, direction: str) -> bool:
    """
    Check for a FRESH MACD crossover on the most recent closed candle.
    direction: 'bullish' or 'bearish'
    """
    if len(df) < 3:
        return False
    curr_macd   = df["macd"].iloc[-1]
    curr_signal = df["macd_signal"].iloc[-1]
    prev_macd   = df["macd"].iloc[-2]
    prev_signal = df["macd_signal"].iloc[-2]

    if direction == "bullish":
        # Previous: macd <= signal  →  Current: macd > signal
        return (prev_macd <= prev_signal) and (curr_macd > curr_signal)
    else:
        # Previous: macd >= signal  →  Current: macd < signal
        return (prev_macd >= prev_signal) and (curr_macd < curr_signal)


def evaluate_signal(df: pd.DataFrame) -> dict:
    """
    Run all 6 conditions on a prepared indicator DataFrame.
    Returns a dict:
      {
        "signal":  "LONG" | "SHORT" | None,
        "atr":     float,
        "details": {...}   # per-condition results for logging
      }
    """
    last = df.iloc[-1]

    details = {}

    # ── Condition 6 — Time window ──────────────────
    details["time_ok"] = _is_trading_window()
    if not details["time_ok"]:
        logger.debug("SKIP: outside trading window")
        return {"signal": None, "atr": last["atr"], "details": details}

    # ── Determine direction from EMA ───────────────
    close   = last["close"]
    ema200  = last["ema200"]
    if pd.isna(ema200):
        logger.debug("SKIP: EMA200 not ready (need more candles)")
        return {"signal": None, "atr": last["atr"], "details": details}

    if close > ema200:
        direction = "LONG"
    elif close < ema200:
        direction = "SHORT"
    else:
        return {"signal": None, "atr": last["atr"], "details": details}

    details["direction"] = direction
    details["ema_ok"]    = True

    # ── Condition 5 — ADX > 25 ────────────────────
    adx = last["adx"]
    details["adx"]    = round(float(adx), 2) if not pd.isna(adx) else None
    details["adx_ok"] = (not pd.isna(adx)) and (adx > 25)
    if not details["adx_ok"]:
        logger.debug(f"SKIP: ADX={adx:.2f} not above 25")
        return {"signal": None, "atr": last["atr"], "details": details}

    # ── Condition 4 — Volume > 1.5x avg ──────────
    vol     = last["volume"]
    vol_avg = last["vol_sma20"]
    details["vol_ok"] = (not pd.isna(vol_avg)) and (vol > 1.5 * vol_avg)
    if not details["vol_ok"]:
        logger.debug("SKIP: volume not 1.5x average")
        return {"signal": None, "atr": last["atr"], "details": details}

    # ── Condition 2 — RSI zone ────────────────────
    rsi = last["rsi"]
    details["rsi"] = round(float(rsi), 2) if not pd.isna(rsi) else None
    if direction == "LONG":
        details["rsi_ok"] = (not pd.isna(rsi)) and (50 <= rsi <= 75)
    else:
        details["rsi_ok"] = (not pd.isna(rsi)) and (25 <= rsi <= 50)
    if not details["rsi_ok"]:
        logger.debug(f"SKIP: RSI={rsi:.2f} not in zone for {direction}")
        return {"signal": None, "atr": last["atr"], "details": details}

    # ── Condition 3 — Fresh MACD crossover ────────
    macd_dir = "bullish" if direction == "LONG" else "bearish"
    details["macd_ok"] = _fresh_macd_crossover(df, macd_dir)
    if not details["macd_ok"]:
        logger.debug(f"SKIP: no fresh {macd_dir} MACD crossover")
        return {"signal": None, "atr": last["atr"], "details": details}

    # ── All conditions passed ──────────────────────
    atr = last["atr"]
    logger.info(f"✅ SIGNAL: {direction}  |  ATR={atr:.4f}  |  RSI={rsi:.1f}  |  ADX={adx:.1f}")
    return {
        "signal":  direction,
        "atr":     float(atr),
        "details": details,
    }


def calculate_exits(side: str, entry_price: float, atr: float) -> tuple[float, float]:
    """
    Return (sl_price, tp_price) for the given side and ATR.
    LONG : SL = entry - 1.5×ATR   TP = entry + 3.0×ATR
    SHORT: SL = entry + 1.5×ATR   TP = entry - 3.0×ATR
    """
    if side == "LONG":
        sl = entry_price - 1.5 * atr
        tp = entry_price + 3.0 * atr
    else:
        sl = entry_price + 1.5 * atr
        tp = entry_price - 3.0 * atr
    return round(sl, 4), round(tp, 4)
