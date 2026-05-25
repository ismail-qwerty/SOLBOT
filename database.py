# ─────────────────────────────────────────────
#  SolBot v2.0 — Database Layer (SQLite + WAL)
# ─────────────────────────────────────────────

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")


def get_conn() -> sqlite3.Connection:
    """
    Return a thread-safe SQLite connection.
    - timeout=20  : retry for 20 s before raising 'database is locked'
    - WAL mode    : allows concurrent reads while bot writes
    - check_same_thread=False : safe for Flask threaded server
    """
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create all tables if they don't already exist."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            side                TEXT NOT NULL,          -- LONG | SHORT
            entry_price         REAL NOT NULL,
            exit_price          REAL,
            sl_price            REAL NOT NULL,
            tp_price            REAL NOT NULL,
            atr_at_entry        REAL NOT NULL,
            position_size_usdt  REAL NOT NULL,
            leverage            INTEGER NOT NULL,
            pnl_usdt            REAL,
            result              TEXT,                   -- WIN | LOSS | OPEN
            open_time           TEXT NOT NULL,
            close_time          TEXT,
            duration_seconds    INTEGER,
            mexc_entry_order_id TEXT,
            mexc_sl_order_id    TEXT,
            mexc_tp_order_id    TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Seed default state rows
    defaults = {
        "status":              "stopped",
        "daily_loss_usdt":     "0.0",
        "daily_loss_date":     "",
        "consecutive_losses":  "0",
        "pause_until":         "",
        "peak_balance":        "0.0",
        "start_balance":       "0.0",
        "is_emergency_stopped": "false",
    }
    for k, v in defaults.items():
        c.execute(
            "INSERT OR IGNORE INTO bot_state (key, value) VALUES (?, ?)",
            (k, v)
        )

    conn.commit()
    conn.close()


# ── State helpers ─────────────────────────────

def get_state(key: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM bot_state WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else ""


def set_state(key: str, value) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()
    conn.close()


# ── Trade helpers ─────────────────────────────

def open_trade(
    side: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    atr: float,
    position_size_usdt: float,
    leverage: int,
    entry_order_id: str = "",
    sl_order_id: str = "",
    tp_order_id: str = "",
) -> int:
    """Insert a new open trade and return its row id."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (
            side, entry_price, sl_price, tp_price, atr_at_entry,
            position_size_usdt, leverage, result, open_time,
            mexc_entry_order_id, mexc_sl_order_id, mexc_tp_order_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        side, entry_price, sl_price, tp_price, atr,
        position_size_usdt, leverage, "OPEN",
        datetime.utcnow().isoformat(),
        entry_order_id, sl_order_id, tp_order_id,
    ))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id: int, exit_price: float, pnl_usdt: float, result: str) -> None:
    """Mark a trade as closed with its exit data."""
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    # Fetch open_time to compute duration
    row = conn.execute(
        "SELECT open_time FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    duration = None
    if row:
        try:
            opened = datetime.fromisoformat(row["open_time"])
            duration = int((datetime.utcnow() - opened).total_seconds())
        except Exception:
            pass

    conn.execute("""
        UPDATE trades
        SET exit_price=?, pnl_usdt=?, result=?, close_time=?, duration_seconds=?
        WHERE id=?
    """, (exit_price, pnl_usdt, result, now, duration, trade_id))
    conn.commit()
    conn.close()


def get_open_trades() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE result = 'OPEN' ORDER BY open_time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_history(limit: int = 100) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE result != 'OPEN' ORDER BY close_time DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE result != 'OPEN'"
    ).fetchall()
    conn.close()

    trades = [dict(r) for r in rows]
    total  = len(trades)
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]

    win_rate   = (len(wins) / total * 100) if total else 0
    avg_win    = (sum(t["pnl_usdt"] for t in wins)   / len(wins))   if wins   else 0
    avg_loss   = (sum(t["pnl_usdt"] for t in losses) / len(losses)) if losses else 0
    total_pnl  = sum(t["pnl_usdt"] for t in trades)
    best_trade = max((t["pnl_usdt"] for t in trades), default=0)
    worst_trade= min((t["pnl_usdt"] for t in trades), default=0)

    # Streak
    streak = 0
    if trades:
        last_result = trades[0]["result"]
        for t in trades:
            if t["result"] == last_result:
                streak += 1
            else:
                break
        streak_str = f"{streak} {'W' if last_result == 'WIN' else 'L'}"
    else:
        streak_str = "—"

    return {
        "total_trades": total,
        "win_rate":     round(win_rate, 1),
        "avg_win":      round(avg_win, 4),
        "avg_loss":     round(avg_loss, 4),
        "total_pnl":    round(total_pnl, 4),
        "best_trade":   round(best_trade, 4),
        "worst_trade":  round(worst_trade, 4),
        "streak":       streak_str,
    }
