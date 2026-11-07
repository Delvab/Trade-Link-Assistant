from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROOF_DIR = DATA_DIR / "proofs"
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "prefix": ".",
    "guild_id": 0,
    "vouch_channel_id": 0,
    "staff_role_ids": [],
    "trader_role_ids": [],
    "whitelist_role_ids": [],
    "whitelist_user_ids": [],
    "owner_ids": [],
    "ticket_category_ids": [],
    "middleman": {
        "id": 0,
        "name": "Kaizo",
        "baseline_vouches": 240,
        "baseline_rating": 4.93,
    },
    "links": {"ticket": "", "fallback_thumbnail": ""},
    "auto": {
        "enabled": True,
        "first_delay_min_seconds": 120,
        "first_delay_max_seconds": 300,
        "interval_min_seconds": 2700,
        "interval_max_seconds": 7200,
        "daily_cap": 10,
    },
    "close": {
        "vouch_delay_min_seconds": 60,
        "vouch_delay_max_seconds": 180,
        "delete_channel": True,
        "delete_after_seconds": 15,
    },
}


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalise(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg["prefix"] = str(cfg.get("prefix") or ".")[:5]

    for key in ("guild_id", "vouch_channel_id"):
        cfg[key] = _as_int(cfg.get(key))

    for key in (
        "staff_role_ids",
        "trader_role_ids",
        "whitelist_role_ids",
        "whitelist_user_ids",
        "owner_ids",
        "ticket_category_ids",
    ):
        values = cfg.get(key) or []
        cfg[key] = [_as_int(item) for item in values if _as_int(item)]

    mm = cfg.setdefault("middleman", {})
    mm["id"] = _as_int(mm.get("id"))
    mm["name"] = str(mm.get("name") or "Middleman")
    mm["baseline_vouches"] = _as_int(mm.get("baseline_vouches"))
    mm["baseline_rating"] = _as_float(mm.get("baseline_rating"), 5.0)

    auto = cfg.setdefault("auto", {})
    auto["enabled"] = bool(auto.get("enabled", True))
    for key in (
        "first_delay_min_seconds",
        "first_delay_max_seconds",
        "interval_min_seconds",
        "interval_max_seconds",
        "daily_cap",
    ):
        auto[key] = _as_int(auto.get(key))

    close = cfg.setdefault("close", {})
    for key in (
        "vouch_delay_min_seconds",
        "vouch_delay_max_seconds",
        "delete_after_seconds",
    ):
        close[key] = _as_int(close.get(key))
    close["delete_channel"] = bool(close.get("delete_channel", True))

    return cfg


def load_config() -> dict[str, Any]:
    _load_env(BASE_DIR / ".env")
    if not CONFIG_PATH.exists():
        return _normalise(json.loads(json.dumps(DEFAULTS)))
    raw = json.loads(CONFIG_PATH.read_text("utf-8"))
    return _normalise(_merge(json.loads(json.dumps(DEFAULTS)), raw))


CONFIG = load_config()
TOKEN = (os.environ.get("DISCORD_TOKEN") or os.environ.get("TOKEN") or "").strip()


def save_config(cfg: dict[str, Any]) -> None:
    normalized = _normalise(json.loads(json.dumps(cfg)))
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(normalized, indent=2), "utf-8")
    os.replace(tmp, CONFIG_PATH)
