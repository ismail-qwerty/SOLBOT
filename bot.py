# ─────────────────────────────────────────────
#  SolBot v2.0 — Main Bot Loop
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

# ── Constants ──────────────────────────────────
CANDLE_WAIT_SECONDS = 15          # polling interval (seconds)
MAX_OPEN_TRADES     = 4


def fetch_candles(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV candles and return as a DataFrame."""
    from executor import _exchange
    exch = _exchange()  # Reuses the persistent authenticated instance
    raw = exch.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def check_closed_trades() -> None:
    """
    Poll MEXC to see if any SL/TP has been triggered on open trades.
    If position is gone → mark trade as closed in DB.
    """
    cfg    = load_config()
    symbol = cfg["symbol"]

    open_db_trades = get_open_trades()
    if not open_db_trades:
        return

    raw_positions = fetch_open_positions(symbol)
    
    # Safety Check Integration:
    if raw_positions is None:
        logger.warning("Position fetch returned None. Skipping safety check loop to prevent ghost exits.")
        return
        
    live_positions = {
        p["symbol"]: p for p in raw_positions
    }

    for t in open_db_trades:
        trade_id = t["id"]
        side     = t["side"]

        # Check if the position still exists on exchange
        pos_key = symbol
        if pos_key not in live_positions:
            # Position is gone — trade was closed by SL or TP
            current_price = fetch_ticker_price(symbol)

            # Determine result by comparing exit vs entry
            entry = t["entry_price"]
            if side == "LONG":
                pnl_pct = (current_price - entry) / entry
            else:
                pnl_pct = (entry - current_price) / entry

            # Estimate PnL from position size and leverage
            notional = t["position_size_usdt"] * cfg["leverage"]
            pnl_usdt = round(notional * pnl_pct, 4)
            result   = "WIN" if pnl_usdt >= 0 else "LOSS"

            close_trade(trade_id, current_price, pnl_usdt, result)

            # Cancel any orphan SL/TP orders
            cancel_order(symbol, t.get("mexc_sl_order_id", ""))
            cancel_order(symbol, t.get("mexc_tp_order_id", ""))

            # Update risk counters
            balance = fetch_balance()
            post_trade_update(pnl_usdt, balance)

            logger.info(
                f"Trade #{trade_id} CLOSED | {result} | PnL={pnl_usdt:+.4f} USDT"
            )


def execute_trade(signal: str, atr: float) -> bool:
    """
    Full trade execution pipeline:
    1. Size position  2. Entry order  3. SL/TP orders  4. Log to DB
    Returns True if trade executed successfully, False otherwise.
    """
    cfg    = load_config()
    symbol = cfg["symbol"]

    try:
        balance  = fetch_balance()
        margin   = calculate_position_size(balance)

        if margin < 1.0:
            logger.error(f"❌ TRADE REJECTED: Margin too small (${margin:.4f} < $1.00)")
            return False

        logger.info(f"💰 Balance: ${balance:.2f} | Margin allocated: ${margin:.2f}")
        
        set_leverage(symbol)

        # ── Entry ─────────────────────────────────────
        logger.info(f"📤 Placing {signal} entry order...")
        entry_order = place_entry_order(symbol, signal, margin)
        
        if not entry_order:
            logger.error(f"❌ TRADE FAILED: Entry order returned None")
            return False
            
        entry_price = float(entry_order.get("average") or entry_order.get("price") or
                            fetch_ticker_price(symbol))
        contracts   = float(entry_order.get("filled") or entry_order.get("amount"))

        # Validate entry execution
        if contracts <= 0:
            logger.error(f"❌ TRADE FAILED: Entry order returned zero contracts")
            return False

        logger.info(f"✅ Entry filled: {contracts} contracts @ ${entry_price:.4f}")

        # ── Exits ─────────────────────────────────────
        sl_price, tp_price = calculate_exits(signal, entry_price, atr)
        logger.info(f"🎯 Setting exits: SL=${sl_price:.4f} | TP=${tp_price:.4f}")

        # Allow order book synchronization
        time.sleep(0.5)

        sl_order = place_sl_order(symbol, signal, contracts, sl_price)
        if not sl_order:
            logger.warning(f"⚠️ WARNING: Stop-Loss order failed to place")
            
        tp_order = place_tp_order(symbol, signal, contracts, tp_price)
        if not tp_order:
            logger.warning(f"⚠️ WARNING: Take-Profit order failed to place")

        # ── Log to database ───────────────────────────
        sl_order_id = str(sl_order.get("id", "")) if sl_order else ""
        tp_order_id = str(tp_order.get("id", "")) if tp_order else ""
        
        trade_id = open_trade(
            side               = signal,
            entry_price        = entry_price,
            sl_price           = sl_price,
            tp_price           = tp_price,
            atr                = atr,
            position_size_usdt = margin,
            leverage           = cfg["leverage"],
            entry_order_id     = str(entry_order.get("id", "")),
            sl_order_id        = sl_order_id,
            tp_order_id        = tp_order_id,
        )

        update_peak_balance(balance)
        logger.info(
            f"✅ TRADE #{trade_id} OPENED SUCCESSFULLY | {signal} | "
            f"Entry=${entry_price:.4f} | SL=${sl_price:.4f} | TP=${tp_price:.4f} | ATR={atr:.4f}"
        )
        return True
        
    except ccxt.InsufficientFunds as e:
        logger.error(f"❌ TRADE FAILED: Insufficient funds - {e}")
        return False
    except ccxt.InvalidOrder as e:
        logger.error(f"❌ TRADE FAILED: Invalid order - {e}")
        return False
    except ccxt.ExchangeError as e:
        logger.error(f"❌ TRADE FAILED: Exchange error - {e}")
        return False
    except Exception as e:
        logger.error(f"❌ TRADE FAILED: Unexpected error - {e}")
        return False


def run_bot() -> None:
    """Main bot loop — runs until status is set to 'stopped'."""
    init_db()
    set_state("status", "running")
    set_state("is_emergency_stopped", "false")
    logger.info("🚀 SolBot v2.0 started")

    cfg = load_config()

    last_candle_ts = None   # Track last processed candle to avoid duplicates

    while get_state("status") == "running":
        try:
            # ── 1. Check for newly closed trades ──────
            check_closed_trades()

            # ── 2. Enforce max open trades ─────────────
            open_trades = get_open_trades()
            if len(open_trades) >= MAX_OPEN_TRADES:
                logger.debug(f"⏸️ Max open trades ({MAX_OPEN_TRADES}) reached — waiting for exits")
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            # ── 3. Fetch candles ───────────────────────
            df = fetch_candles(cfg["symbol"], cfg["timeframe"], cfg["candle_limit"])
            closed_candle = df.iloc[-2]
            latest_ts = closed_candle["timestamp"]

            if latest_ts == last_candle_ts:
                # Same candle, no new close yet
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            last_candle_ts = latest_ts
            logger.debug(f"New candle closed at {latest_ts}")

            # ── 4. Risk gate ───────────────────────────
            balance = fetch_balance()
            allowed, reason = can_trade(balance)
            if not allowed:
                logger.warning(f"🚫 TRADE BLOCKED: {reason}")
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            # ── 5. Compute indicators + signal ─────────
            df  = compute_indicators(df)
            sig = evaluate_signal(df)

            if sig["signal"] is None:
                logger.debug(f"📊 No signal detected | ST_dir={sig['details'].get('st_dir', 'N/A')} | RSI={sig['details'].get('rsi', 'N/A')}")
                time.sleep(CANDLE_WAIT_SECONDS)
                continue

            # ── 6. Execute trade ───────────────────────
            logger.info(f"🎯 SIGNAL DETECTED: {sig['signal']} | ATR={sig['atr']:.4f}")
            success = execute_trade(sig["signal"], sig["atr"])
            
            if success:
                logger.info(f"✅ Trade execution completed successfully")
            else:
                logger.error(f"❌ Trade execution failed - check logs above for details")

        except ccxt.NetworkError as e:
            logger.error(f"🌐 Network error: {e} — retrying in 30s")
            time.sleep(30)
        except ccxt.ExchangeError as e:
            logger.error(f"🔴 Exchange error: {e} — retrying in 60s")
            time.sleep(60)
        except Exception as e:
            logger.exception(f"💥 Unexpected error: {e}")
            time.sleep(30)

        time.sleep(CANDLE_WAIT_SECONDS)

    logger.info("🛑 SolBot stopped")


def emergency_stop() -> None:
    """Immediately close all positions and cancel all orders."""
    cfg    = load_config()
    symbol = cfg["symbol"]

    set_state("is_emergency_stopped", "true")
    set_state("status", "stopped")
    logger.warning("🚨 EMERGENCY STOP triggered")

    cancel_all_orders(symbol)

    # Close any open positions
    for trade in get_open_trades():
        side      = trade["side"]
        contracts = (trade["position_size_usdt"] * cfg["leverage"]) / fetch_ticker_price(symbol)
        close_position(symbol, side, round(contracts, 4))

    logger.warning("🚨 All positions and orders closed")


if __name__ == "__main__":
    run_bot()
