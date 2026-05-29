# ─────────────────────────────────────────────
#  SolBot v2.0 — Order Executor
# ─────────────────────────────────────────────

import ccxt
import logging
import time
from config import load_config

logger = logging.getLogger("solbot.executor")

# Rate-limit safety: pause between API calls (seconds)
API_CALL_DELAY = 0.3


def _exchange() -> ccxt.mexc:
    """Build and return an authenticated MEXC futures exchange instance."""
    cfg = load_config()
    exchange = ccxt.mexc({
        "apiKey":  cfg["api_key"],
        "secret":  cfg["api_secret"],
        "options": {"defaultType": "swap"},  # perpetual futures
    })
    return exchange


def set_leverage(symbol: str) -> None:
    """Set leverage on the symbol before entering a position."""
    cfg  = load_config()
    exch = _exchange()
    try:
        exch.set_leverage(cfg["leverage"], symbol)
        logger.info(f"Leverage set to {cfg['leverage']}x on {symbol}")
    except Exception as e:
        logger.warning(f"set_leverage failed (may already be set): {e}")


def fetch_balance(quote: str = "USDT") -> float:
    """Return the free margin balance in USDT from MEXC futures account."""
    exch = _exchange()
    balance = exch.fetch_balance()
    free = balance.get(quote, {}).get("free", 0.0)
    logger.info(f"Live balance: {free:.4f} {quote}")
    return float(free)


def fetch_ticker_price(symbol: str) -> float:
    """Return the latest mark/last price for a futures symbol."""
    exch = _exchange()
    ticker = exch.fetch_ticker(symbol)
    return float(ticker["last"])


def place_entry_order(
    symbol: str,
    side: str,       # 'LONG' | 'SHORT'
    margin_usdt: float,
) -> dict | None:
    """
    Place a market entry order.
    margin_usdt is the ACTUAL margin to commit (already includes 0.95 buffer).
    Returns the full CCXT order dict or None on failure.
    """
    cfg      = load_config()
    leverage = cfg["leverage"]
    exch     = _exchange()

    order_side = "buy" if side == "LONG" else "sell"

    try:
        # Calculate notional contracts from margin
        price     = fetch_ticker_price(symbol)
        notional  = margin_usdt * leverage          # USD value of position
        contracts = notional / price                # number of SOL contracts

        # Round down to exchange precision
        markets  = exch.load_markets()
        precision = markets[symbol].get("precision", {}).get("amount", 0.01)
        contracts = float(exch.amount_to_precision(symbol, contracts))

        logger.info(
            f"📤 ENTRY {side}: {contracts} contracts @ ~${price:.4f} | "
            f"margin=${margin_usdt:.4f} | notional=${notional:.2f}"
        )

        time.sleep(API_CALL_DELAY)
        order = exch.create_order(
            symbol=symbol,
            type="market",
            side=order_side,
            amount=contracts,
            params={"positionSide": "LONG" if side == "LONG" else "SHORT"},
        )
        logger.info(f"✅ Entry order placed successfully: id={order['id']}")
        return order
        
    except ccxt.InsufficientFunds as e:
        logger.error(f"❌ Entry order failed: Insufficient funds - {e}")
        return None
    except ccxt.InvalidOrder as e:
        logger.error(f"❌ Entry order failed: Invalid order parameters - {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Entry order failed: {e}")
        return None


def place_sl_order(
    symbol: str,
    side: str,
    contracts: float,
    sl_price: float,
) -> dict | None:
    """
    Place a Stop-Loss trigger order with reduce_only=True.
    reduce_only ensures it can ONLY close an existing position.
    """
    exch       = _exchange()
    close_side = "sell" if side == "LONG" else "buy"

    try:
        time.sleep(API_CALL_DELAY)
        order = exch.create_order(
            symbol=symbol,
            type="stop_market",
            side=close_side,
            amount=round(contracts, 2),  # Precision fix applied
            params={
                "stopPrice":    round(sl_price, 4),
                "reduce_only":  True,
                "positionSide": "LONG" if side == "LONG" else "SHORT",
            },
        )
        logger.info(f"SL order placed: id={order['id']}  trigger=${sl_price:.4f}")
        return order
    except Exception as e:
        logger.error(f"Stop-Loss order registration aborted: {e}")
        return None


def place_tp_order(
    symbol: str,
    side: str,
    contracts: float,
    tp_price: float,
) -> dict | None:
    """
    Place a Take-Profit trigger order with reduce_only=True.
    reduce_only ensures it can ONLY close an existing position.
    """
    exch       = _exchange()
    close_side = "sell" if side == "LONG" else "buy"

    try:
        time.sleep(API_CALL_DELAY)
        order = exch.create_order(
            symbol=symbol,
            type="take_profit_market",
            side=close_side,
            amount=round(contracts, 2),  # Precision fix applied
            params={
                "stopPrice":    round(tp_price, 4),
                "reduce_only":  True,
                "positionSide": "LONG" if side == "LONG" else "SHORT",
            },
        )
        logger.info(f"TP order placed: id={order['id']}  trigger=${tp_price:.4f}")
        return order
    except Exception as e:
        logger.error(f"Take-Profit order registration aborted: {e}")
        return None


def cancel_order(symbol: str, order_id: str) -> None:
    """Cancel a specific order by ID (used to clean up orphan SL/TP)."""
    if not order_id:
        return
    exch = _exchange()
    try:
        time.sleep(API_CALL_DELAY)
        exch.cancel_order(order_id, symbol)
        logger.info(f"Cancelled order {order_id}")
    except Exception as e:
        logger.warning(f"Cancel order {order_id} failed: {e}")


def cancel_all_orders(symbol: str) -> None:
    """Cancel ALL open orders for a symbol (used by emergency stop)."""
    exch = _exchange()
    try:
        time.sleep(API_CALL_DELAY)
        exch.cancel_all_orders(symbol)
        logger.info(f"All orders cancelled for {symbol}")
    except Exception as e:
        logger.warning(f"cancel_all_orders failed: {e}")


def close_position(symbol: str, side: str, contracts: float) -> dict | None:
    """
    Immediately close a position with a market order (emergency stop).
    reduce_only=True prevents accidental flip.
    """
    exch       = _exchange()
    close_side = "sell" if side == "LONG" else "buy"
    try:
        time.sleep(API_CALL_DELAY)
        order = exch.create_order(
            symbol=symbol,
            type="market",
            side=close_side,
            amount=contracts,
            params={
                "reduce_only":  True,
                "positionSide": "LONG" if side == "LONG" else "SHORT",
            },
        )
        logger.info(f"Position closed (emergency): {order['id']}")
        return order
    except Exception as e:
        logger.error(f"close_position failed: {e}")
        return None


def fetch_open_positions(symbol: str) -> list | None:
    """Return list of open futures positions for the symbol."""
    exch = _exchange()
    try:
        positions = exch.fetch_positions([symbol])
        return [p for p in positions if float(p.get("contracts", 0)) != 0]
    except Exception as e:
        logger.error(f"fetch_open_positions failed: {e}")
        return None
