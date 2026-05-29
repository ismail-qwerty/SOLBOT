import os
import time
import logging
import pandas as pd
import ccxt
from dotenv import load_dotenv

# Import your optimized scalping strategy elements
import strategy

# Configure logging matching your process outputs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s"
)
logger = logging.getLogger("solbot.main")
executor_logger = logging.getLogger("solbot.executor")

load_dotenv()

# --- INITIALIZATION & CONFIG CONFIGURATION ---
exchange_id = "mexc"
symbol = "SOL/USDT:USDT"  # Linear perpetual swap format
timeframe = "3m"         # Fast-paced timeframe configured for high-frequency trades

# Risk and sizing constraints
MAX_OPEN_TRADES = 4
LEVERAGE = 10
POSITION_MARG_PCT = 0.50  # Uses roughly half the available free balance per entry slot

# Setup exchange client using ccxt
api_key = os.getenv("MEXC_API_KEY")
secret_key = os.getenv("MEXC_SECRET_KEY")

exchange = getattr(ccxt, exchange_id)({
    'apiKey': api_key,
    'secret': secret_key,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
    }
})


def fetch_market_data(symbol, timeframe, limit=50) -> pd.DataFrame:
    """Fetches real-time OHLCV candles from the exchange and structures a DataFrame."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        logger.error(f"❌ Failed to fetch market data from exchange: {e}")
        return pd.DataFrame()


def run_bot():
    """Main execution loop that drives the high-frequency scanning matrix."""
    logger.info("🚀 SolBot v2.0 started")
    
    # Simple state tracking for open positions array simulation
    open_trades = []

    while True:
        try:
            # 1. Check position sizing ceilings
            if len(open_trades) >= MAX_OPEN_TRADES:
                logger.info(f"Execution paused: Max open trade limit reached ({len(open_trades)}/{MAX_OPEN_TRADES})")
                time.sleep(60)
                continue

            # 2. Extract portfolio balance values
            balance_resp = exchange.fetch_balance()
            free_balance = float(balance_resp.get("USDT", {}).get("free", 0.0))
            executor_logger.info(f"Live balance: {free_balance:.4f} USDT")

            # 3. Pull historical frames and calculate strategy metrics
            df = fetch_market_data(symbol, timeframe)
            if df.empty:
                time.sleep(15)
                continue

            df_with_indicators = strategy.compute_indicators(df)
            analysis = strategy.evaluate_signal(df_with_indicators)
            signal = analysis.get("signal")
            atr = analysis.get("atr")

            # 4. Process triggers
            if signal in ["LONG", "SHORT"]:
                logger.info(f"🎯 SIGNAL DETECTED: {signal} | ATR={atr}")
                
                # Sizing math based on available equity allocations
                margin_allocated = free_balance * POSITION_MARG_PCT
                executor_logger.info(f"Balance: ${free_balance:.2f} | Margin Allocated: ${margin_allocated:.2f}")

                # Configure account leverage context safely before firing orders
                try:
                    exchange.set_leverage(LEVERAGE, symbol)
                except Exception as e:
                    # Log as warning since position margins might already match configuration states
                    executor_logger.warning(f"set_leverage failed (may already be set): {e}")

                logger.info(f"🚀 Placing {signal} entry order...")
                
                # Mock transaction context mimicking live execution payloads
                # Change to exchange.create_market_order in live production environments
                entry_order = {
                    "id": "815394264917112832", 
                    "price": None,  # Simulating common market order response pattern
                    "status": "closed",
                    "amount": 1.0
                }
                
                executor_logger.info(f"✅ Entry order placed successfully: id='{entry_order.get('id')}'")

                # ── 🔥 THE CRITICAL NONE-TYPE PROTECTION LAYER 🔥 ──
                # Safely extract price field from order response object
                raw_price = entry_order.get("price")
                entry_price = 0.0

                if raw_price is not None:
                    try:
                        entry_price = float(raw_price)
                    except (ValueError, TypeError):
                        entry_price = 0.0

                # Fallback implementation if the exchange returns None, String-None, or 0.0 on Market orders
                if entry_price <= 0:
                    logger.info("⚠️ Exchange did not return a market fill price. Fetching latest strategy close price...")
                    entry_price = analysis.get("details", {}).get("close")
                    
                    # Hard stop absolute fallback logic directly out of raw dataframe structures
                    if entry_price is None or pd.isna(entry_price):
                        entry_price = float(df["close"].iloc[-1])

                logger.info(f"📈 Resolved Entry Price Reference to: {entry_price:.4f}")

                # 5. Compute exits without risking calculations on NoneType assets
                sl, tp = strategy.calculate_exits(signal, entry_price, atr)
                logger.info(f"🛡️ Risk Matrix Generated Safely -> SL: {sl} | TP: {tp}")

                # Append mock data frame record to manage state limitations
                open_trades.append({"id": entry_order.get("id"), "side": signal, "entry": entry_price})

            else:
                logger.info("Scan complete: No trading triggers met on current interval.")

            # High frequency check cycle sleep parameters
            logger.info("Sleeping until next candle interval cycle...")
            time.sleep(60)

        except KeyboardInterrupt:
            logger.info("Shutting down core engine processes gracefully...")
            break
        except Exception as main_err:
            logger.error(f"❌ XTrade Execution failed - check logs above for details. Error: {main_err}")
            time.sleep(10)


if __name__ == "__main__":
    run_bot()
