# ─────────────────────────────────────────────
#  SolBot v2.0 — Main Bot Loop (Scalping Patch)
# ─────────────────────────────────────────────

import ccxt
import time
import logging
import pandas as pd
from datetime import datetime, timezone

from config import load_config
from database import (
    init_db, get_state, set_state,
    open_trade, close_trade, get_open_trades,
)
from strategy import compute_indicators, evaluate_signal, calculate_exits
from executor import (
    set_leverage, fetch_balance, fetch_ticker_price,
    place_entry_order, place_sl_order, place_tp_order,
    cancel_order, cancel_all_orders, close_position,
    fetch_open_positions,
)
from risk_manager import (
    calculate_position_size, can_trade, post_trade_update,
    update_peak_balance,
)

# ── Logging setup ──────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler("solbot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("solbot.main")

# ── High Frequency Constants ───────────────────
CANDLE_WAIT_SECONDS = 15          # Core loop polling intervals
MAX_OPEN_TRADES     = 4          # Expanded ceiling for active scalping setups


def fetch_candles(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV candles and return as a structured DataFrame."""
    from executor import _exchange
    exch = _exchange()
    try:
        ohlcv = exch.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        logger.error(f"Error fetching candles: {e}")
        return pd.DataFrame()


def execute_trade(direction: str, atr: float, last_close: float) -> None:
    """Calculates position risk and executes entry + protective exit orders safely."""
    cfg = load_config()
    symbol = cfg["symbol"]

    # 1. Pull current margin framework
    try:
        balance = fetch_balance()
    except Exception as e:
        logger.error(f"Cannot execute trade, balance fetch failed: {e}")
        return

    # 2. Gatekeeper Risk Analysis
    allowed, reason = can_trade(balance)
    if not allowed:
        logger.info(f"Trade blocked by risk manager: {reason}")
        return

    if len(get_open_trades()) >= MAX_OPEN_TRADES:
        logger.info(f"Trade blocked: Max open trades ceiling reached.")
        return

    # 3. Dynamic sizing allocation calculations
    position_size_usdt = calculate_position_size(balance)
    logger.info(f"Target execution sizing derived: {position_size_usdt} USDT")

    # Establish leverage environment on the exchange exchange layout
    set_leverage(symbol)

    # 4. Firing Market Entry Order
    logger.info(f"🚀 Dispatching market execution order -> {direction} {symbol}")
    entry_order = place_entry_order(symbol, direction, position_size_usdt)
    
    if not entry_order:
        logger.error("❌ Order rejected or failed at the execution layer.")
        return

    # ── 🔥 THE CRITICAL NONE-TYPE PROTECTION LAYER 🔥 ──
    raw_price = entry_order.get("price")
    entry_price = 0.0

    if raw_price is not None:
        try:
            entry_price = float(raw_price)
        except (ValueError, TypeError):
            entry_price = 0.0

    # Fallback to strategy execution matrix properties if the API response value is missing
    if entry_price <= 0:
        logger.warning("⚠️ Exchange API response omitted market fill price. Deploying local price targets...")
        if last_close and float(last_close) > 0:
            entry_price = float(last_close)
        else:
            entry_price = fetch_ticker_price(symbol)

    logger.info(f"📈 Resolved Entry Reference Price: {entry_price:.4f}")

    # 5. Deriving dynamic structural stop loss and take profit zones
    sl_price, tp_price = calculate_exits(direction, entry_price, atr)
    logger.info(f"🛡️ Risk bands built safely -> SL: {sl_price} | TP: {tp_price}")

    if sl_price == 0.0 or tp_price == 0.0:
        logger.error("❌ Critical: Exit calculations returned invalid boundaries. Safety abort.")
        return

    # 6. Dispatch protective conditional boundary orders
    sl_order = place_sl_order(symbol, direction, sl_price, position_size_usdt)
    tp_order = place_tp_order(symbol, direction, tp_price, position_size_usdt)

    sl_id = sl_order["id"] if sl_order else "FAILED"
    tp_id = tp_order["id"] if tp_order else "FAILED"

    # 7. Persist position state metadata into SQLite database record tracking sheets
    open_trade(
        side=direction,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        atr_at_entry=atr,
        position_size_usdt=position_size_usdt,
        leverage=cfg["leverage"],
        sl_order_id=sl_id,
        tp_order_id=tp_id
    )
    logger.info(f"🎉 Trade fully synchronized and logged into local DB state layers.")


def main() -> None:
    """Master runtime scan automation cycle."""
    init_db()
    set_state("status", "running")
    set_state("is_emergency_stopped", "false")

    logger.info("🟢 SolBot Scalping Engine Engine initiated.")

    while True:
        try:
            # Sync global variables and active configurations
            if get_state("status") != "running":
                logger.info("Bot execution state paused via dashboard control elements.")
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            cfg = load_config()
            symbol = cfg["symbol"]
            timeframe = cfg["timeframe"]
            limit = int(cfg["candle_limit"])

            # Check loop balances to maintain dynamic high-water mark records
            try:
                bal = fetch_balance()
                update_peak_balance(bal)
            except Exception:
                pass

            # Fetch target frame historical datasets
            df = fetch_candles(symbol, timeframe, limit)
            if df.empty or len(df) < 15:
                logger.warning("Insufficient array frames returned. Scanning paused...")
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            # ── Execute Strategy Metrics ─────────
            df = compute_indicators(df)
            sig_analysis = evaluate_signal(df)

            if sig_analysis["signal"] is None:
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            # Extract fallback reference target pricing point context
            last_close_val = df["close"].iloc[-1]

            # ── Fire Trade Logic Execution ───────
            execute_trade(sig_analysis["signal"], sig_analysis["atr"], last_close_val)

        except ccxt.NetworkError as e:
            logger.warning(f"Network error: {e} — retrying in 30s")
            time.sleep(30)
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error: {e} — retrying in 60s")
            time.sleep(60)
        except Exception as e:
            logger.exception(f"Unexpected loop exception caught: {e}")
            time.sleep(30)

        time.sleep(CANDLE_WAIT_SECONDS)

    logger.info("🛑 SolBot stopped")


def emergency_stop() -> None:
    """Immediately close all open exposure limits across active structures."""
    cfg = load_config()
    symbol = cfg["symbol"]

    set_state("is_emergency_stopped", "true")
    set_state("status", "stopped")
    logger.warning("🚨 EMERGENCY STOP triggered")

    cancel_all_orders(symbol)

    for trade in get_open_trades():
        side = trade["side"]
        contracts = (trade["position_size_usdt"] * cfg["leverage"]) / fetch_ticker_price(symbol)
        close_position(symbol, side, contracts)
        close_trade(trade["id"], exit_price=fetch_ticker_price(symbol), pnl_usdt=0, result="EMERGENCY_CLOSE")


if __name__ == "__main__":
    main()
