# ─────────────────────────────────────────────
#  SolBot v2.0 — Risk Manager
# ─────────────────────────────────────────────

import logging
from datetime import datetime, timezone, timedelta
from database import get_state, set_state
from config import load_config

logger = logging.getLogger("solbot.risk")


# ── Daily loss tracker ────────────────────────

def reset_daily_loss_if_new_day() -> None:
    """Reset daily loss counter at UTC midnight."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stored_date = get_state("daily_loss_date")
    if stored_date != today:
        set_state("daily_loss_usdt", "0.0")
        set_state("daily_loss_date", today)
        logger.info(f"Daily loss counter reset for {today}")


def record_trade_loss(loss_usdt: float) -> None:
    """Accumulate a loss into today's daily loss total."""
    reset_daily_loss_if_new_day()
    current = float(get_state("daily_loss_usdt") or "0.0")
    set_state("daily_loss_usdt", str(current + abs(loss_usdt)))


def is_daily_loss_breached(balance: float) -> bool:
    """Return True if today's losses exceed the configured daily loss %."""
    reset_daily_loss_if_new_day()
    cfg = load_config()
    daily_loss = float(get_state("daily_loss_usdt") or "0.0")
    limit = balance * cfg["max_daily_loss_pct"]
    if daily_loss >= limit:
        logger.warning(
            f"⛔ Daily loss limit hit: lost ${daily_loss:.4f} vs limit ${limit:.4f}"
        )
        return True
    return False


# ── Drawdown tracker ──────────────────────────

def update_peak_balance(balance: float) -> None:
    """Update the all-time peak balance used for drawdown calculation."""
    peak = float(get_state("peak_balance") or "0.0")
    if balance > peak:
        set_state("peak_balance", str(balance))


def is_max_drawdown_breached(balance: float) -> bool:
    """Return True if drawdown from peak exceeds the configured maximum."""
    cfg = load_config()
    peak = float(get_state("peak_balance") or str(balance))
    if peak == 0:
        return False
    drawdown_pct = (peak - balance) / peak
    if drawdown_pct >= cfg["max_drawdown_pct"]:
        logger.warning(
            f"⛔ Max drawdown hit: {drawdown_pct*100:.1f}% from peak ${peak:.4f}"
        )
        return True
    return False


# ── Consecutive loss pause ────────────────────

def record_loss_streak() -> None:
    """Increment consecutive loss counter; trigger 1-hour pause at 3."""
    current = int(get_state("consecutive_losses") or "0")
    current += 1
    set_state("consecutive_losses", str(current))
    logger.info(f"Consecutive losses: {current}")
    if current >= 3:
        resume_at = datetime.now(timezone.utc) + timedelta(hours=1)
        set_state("pause_until", resume_at.isoformat())
        set_state("consecutive_losses", "0")
        logger.warning(
            f"⏸ 3 consecutive losses — bot paused until {resume_at.strftime('%H:%M UTC')}"
        )


def reset_loss_streak() -> None:
    """Reset consecutive loss counter after a win."""
    set_state("consecutive_losses", "0")


def is_paused() -> bool:
    """Return True if the bot is inside a timed pause window."""
    pause_until_str = get_state("pause_until")
    if not pause_until_str:
        return False
    try:
        pause_until = datetime.fromisoformat(pause_until_str)
        if pause_until.tzinfo is None:
            pause_until = pause_until.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now < pause_until:
            remaining = int((pause_until - now).total_seconds() / 60)
            logger.info(f"⏸ Bot paused — {remaining} minutes remaining")
            return True
        else:
            set_state("pause_until", "")
            logger.info("▶ Pause window expired — resuming")
            return False
    except Exception:
        return False


# ── Position sizing ───────────────────────────

def calculate_position_size(balance: float) -> float:
    """
    Ensures safe executable contract margin sizes for smaller wallets.
    Leaves a 5% baseline calculation buffer for exchange taker fees.
    """
    cfg = load_config()
    position_percentage = cfg["position_pct"]
    
    # Force dynamic protection for thin capital frames
    if balance <= 12.0:
        position_percentage = 0.58  # Yields a reliable ~$5.50 entry margin base
        
    raw = balance * position_percentage
    safe = raw * 0.95
    return round(safe, 4)


# ── Master gate ───────────────────────────────

def can_trade(balance: float) -> tuple[bool, str]:
    """
    Single call to check ALL risk rules before placing a trade.
    Returns (allowed: bool, reason: str).
    """
    if get_state("is_emergency_stopped") == "true":
        return False, "Emergency stop active"

    if get_state("status") != "running":
        return False, "Bot is not running"

    if is_paused():
        return False, "Bot is in timed pause after 3 consecutive losses"

    if is_daily_loss_breached(balance):
        return False, "Daily loss limit reached"

    if is_max_drawdown_breached(balance):
        set_state("status", "stopped")
        return False, "Max drawdown reached — manual restart required"

    return True, "OK"


# ── Post-trade update ─────────────────────────

def post_trade_update(pnl_usdt: float, balance: float) -> None:
    """Call after every trade close to update all counters."""
    update_peak_balance(balance)
    if pnl_usdt < 0:
        record_trade_loss(pnl_usdt)
        record_loss_streak()
    else:
        reset_loss_streak()
