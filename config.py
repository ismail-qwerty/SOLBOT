# ─────────────────────────────────────────────
#  SolBot v2.0 — Configuration
# ─────────────────────────────────────────────

import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "bot_config.json")

DEFAULTS = {
    "api_key":          "",
    "api_secret":       "",
    "leverage":         10,
    "position_pct":     0.50,
    "max_daily_loss_pct": 0.05,
    "max_drawdown_pct": 0.15,
    "symbol":           "SOL/USDT:USDT",
    "timeframe":        "15m",
    "candle_limit":     250,
}


def load_config() -> dict:
    """Load config from JSON file, merging with defaults."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            stored = json.load(f)
        cfg = {**DEFAULTS, **stored}
    else:
        cfg = DEFAULTS.copy()
    return cfg


def save_config(updates: dict) -> None:
    """Persist updated config to JSON file."""
    current = load_config()
    current.update(updates)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2)
