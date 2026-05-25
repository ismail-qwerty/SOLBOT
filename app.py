# ─────────────────────────────────────────────
#  SolBot v2.0 — Flask Dashboard Server
# ─────────────────────────────────────────────

import subprocess
import sys
import os
import logging
from flask import Flask, render_template, jsonify, request

from database import (
    init_db, get_state, set_state,
    get_open_trades, get_trade_history, get_stats,
)
from config import load_config, save_config

logger = logging.getLogger("solbot.app")

app = Flask(__name__, template_folder="templates")

# ── Lazy import executor to avoid ccxt import at startup ──
def _fetch_live_price() -> float:
    try:
        from executor import fetch_ticker_price
        cfg = load_config()
        return fetch_ticker_price(cfg["symbol"])
    except Exception:
        return 0.0


def _fetch_live_balance() -> float:
    try:
        from executor import fetch_balance
        return fetch_balance()
    except Exception:
        return 0.0


# ── Pages ──────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


# ── API endpoints ──────────────────────────────

@app.route("/api/status")
def api_status():
    """Return all live dashboard data in one call."""
    price   = _fetch_live_price()
    balance = _fetch_live_balance()
    stats   = get_stats()
    open_t  = get_open_trades()
    history = get_trade_history(100)
    cfg     = load_config()

    # Enrich open trades with current P&L
    for t in open_t:
        entry = t["entry_price"]
        side  = t["side"]
        if price > 0:
            notional = t["position_size_usdt"] * cfg["leverage"]
            if side == "LONG":
                pct = (price - entry) / entry
            else:
                pct = (entry - price) / entry
            t["unrealized_pnl"] = round(notional * pct, 4)
            t["current_price"]  = price
        else:
            t["unrealized_pnl"] = None
            t["current_price"]  = None

    # Today's PnL
    from database import get_conn
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn  = get_conn()
    today_trades = conn.execute(
        "SELECT pnl_usdt FROM trades WHERE result != 'OPEN' AND close_time LIKE ?",
        (f"{today}%",)
    ).fetchall()
    conn.close()
    today_pnl = sum(r["pnl_usdt"] for r in today_trades)

    return jsonify({
        "bot_status":       get_state("status"),
        "is_emergency":     get_state("is_emergency_stopped"),
        "sol_price":        price,
        "balance":          round(balance, 4),
        "today_pnl":        round(today_pnl, 4),
        "today_pnl_pct":    round((today_pnl / balance * 100) if balance else 0, 2),
        "daily_loss":       float(get_state("daily_loss_usdt") or "0"),
        "consecutive_losses": int(get_state("consecutive_losses") or "0"),
        "pause_until":      get_state("pause_until"),
        "stats":            stats,
        "open_trades":      open_t,
        "trade_history":    history,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    """Signal the bot process to start."""
    if get_state("status") == "running":
        return jsonify({"ok": False, "msg": "Bot already running"})
    set_state("status", "running")
    set_state("is_emergency_stopped", "false")
    # PM2 manages the bot.py process; this just sets the DB flag.
    # If running manually, bot.py reads the flag on its loop.
    logger.info("Dashboard: START requested")
    return jsonify({"ok": True, "msg": "Bot started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Signal the bot to stop after current trade closes."""
    set_state("status", "stopped")
    logger.info("Dashboard: STOP requested")
    return jsonify({"ok": True, "msg": "Bot will stop after current trade closes"})


@app.route("/api/emergency_stop", methods=["POST"])
def api_emergency_stop():
    """Trigger emergency stop — close everything immediately."""
    try:
        from bot import emergency_stop
        emergency_stop()
        return jsonify({"ok": True, "msg": "Emergency stop executed"})
    except Exception as e:
        set_state("status", "stopped")
        set_state("is_emergency_stopped", "true")
        return jsonify({"ok": True, "msg": f"Status set to stopped: {e}"})


@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = load_config()
    # Never expose secrets to frontend directly
    safe = {k: v for k, v in cfg.items() if k not in ("api_key", "api_secret")}
    safe["api_key_set"]    = bool(cfg.get("api_key"))
    safe["api_secret_set"] = bool(cfg.get("api_secret"))
    return jsonify(safe)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json(force=True)
    allowed = {
        "api_key", "api_secret", "leverage",
        "position_pct", "max_daily_loss_pct", "max_drawdown_pct",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    # Convert numeric fields
    for num_field in ("leverage", "position_pct", "max_daily_loss_pct", "max_drawdown_pct"):
        if num_field in updates:
            try:
                updates[num_field] = float(updates[num_field])
            except (ValueError, TypeError):
                pass
    save_config(updates)
    return jsonify({"ok": True, "msg": "Settings saved"})


# ── Entry point ────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
