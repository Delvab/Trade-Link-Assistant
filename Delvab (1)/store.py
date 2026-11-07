from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

DEFAULT_STATE: dict[str, Any] = {
    "auto_enabled": True,
    "initialized": False,
    "generated_day": "",
    "generated_count": 0,
}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self.data: dict[str, Any] = {
            "counter": 0,
            "vouches": [],
            "baselines": {},
            "state": dict(DEFAULT_STATE),
        }

    async def load(self) -> None:
        if not self.path.exists():
            await self.save()
            return

        raw = json.loads(self.path.read_text("utf-8"))
        self.data["counter"] = int(raw.get("counter", 0))
        self.data["vouches"] = list(raw.get("vouches", []))
        self.data["baselines"] = dict(raw.get("baselines", {}))
        self.data["state"] = {**DEFAULT_STATE, **dict(raw.get("state", {}))}

    def _save_now(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    async def save(self) -> None:
        async with self._lock:
            self._save_now()

    async def add_vouch(
        self,
        fields: dict[str, Any],
        *,
        source: str,
        not_before: float = 0,
        posted: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            self.data["counter"] += 1
            record = {
                "number": self.data["counter"],
                "qid": secrets.token_hex(6),
                "created_at": int(time.time()),
                "not_before": float(not_before or 0),
                "posted": posted,
                "channel_id": None,
                "message_id": None,
                "attempts": 0,
                "last_error": None,
                "abandoned": False,
                "source": source,
                **fields,
            }
            self.data["vouches"].append(record)
            self._save_now()
            return dict(record)

    async def mark_posted(
        self, number: int, channel_id: int, message_id: int
    ) -> None:
        async with self._lock:
            record = self._find(number)
            if record is None:
                return
            record["posted"] = True
            record["channel_id"] = channel_id
            record["message_id"] = message_id
            record["not_before"] = 0
            record["last_error"] = None
            self._save_now()

    async def record_failure(self, number: int, error: Exception) -> None:
        async with self._lock:
            record = self._find(number)
            if record is None:
                return
            record["attempts"] = int(record.get("attempts", 0)) + 1
            record["last_error"] = str(error)[:300]
            backoff = min(900, 60 * record["attempts"])
            record["not_before"] = time.time() + backoff
            self._save_now()

    def _find(self, number: int) -> dict[str, Any] | None:
        for record in self.data["vouches"]:
            if int(record.get("number", -1)) == int(number):
                return record
        return None

    def get(self, number: int) -> dict[str, Any] | None:
        record = self._find(number)
        return dict(record) if record else None

    def next_due(self, now: float) -> dict[str, Any] | None:
        pending = [
            record
            for record in self.data["vouches"]
            if not record.get("posted")
            and not record.get("abandoned")
            and float(record.get("not_before", 0)) <= now
        ]
        pending.sort(key=lambda item: (float(item.get("not_before", 0)), item["number"]))
        return dict(pending[0]) if pending else None

    def pending(self, limit: int = 25) -> list[dict[str, Any]]:
        items = [
            dict(record)
            for record in self.data["vouches"]
            if not record.get("posted") and not record.get("abandoned")
        ]
        items.sort(key=lambda item: (float(item.get("not_before", 0)), item["number"]))
        return items[:limit]

    def pending_count(self) -> int:
        return sum(
            1
            for record in self.data["vouches"]
            if not record.get("posted") and not record.get("abandoned")
        )

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        items = sorted(
            self.data["vouches"],
            key=lambda item: int(item.get("created_at", 0)),
            reverse=True,
        )
        return [dict(item) for item in items[:limit]]

    def stats_for(self, key: str) -> dict[str, Any]:
        entries = [
            record
            for record in self.data["vouches"]
            if record.get("posted")
            and (record.get("trader_key") == key or record.get("mm_key") == key)
        ]
        baseline = self.data["baselines"].get(key, {})
        base_count = int(baseline.get("count", 0))
        base_sum = float(baseline.get("rating_sum", 0.0))

        recorded_count = len(entries)
        recorded_sum = sum(int(record.get("rating", 0)) for record in entries)
        count = base_count + recorded_count
        total_sum = base_sum + recorded_sum

        return {
            "count": count,
            "avg": (total_sum / count) if count else 0.0,
            "recorded_count": recorded_count,
            "recorded_avg": (recorded_sum / recorded_count) if recorded_count else 0.0,
            "last_at": max(
                (int(record.get("created_at", 0)) for record in entries), default=0
            ),
        }

    async def ensure_baseline(
        self, key: str, count: int, rating: float
    ) -> None:
        async with self._lock:
            if key in self.data["baselines"]:
                return
            self.data["baselines"][key] = {
                "count": max(0, int(count)),
                "rating_sum": max(0.0, float(count) * float(rating)),
            }
            self._save_now()

    @property
    def auto_enabled(self) -> bool:
        return bool(self.data["state"].get("auto_enabled", True))

    async def initialize_auto(self, enabled: bool) -> None:
        async with self._lock:
            if self.data["state"].get("initialized"):
                return
            self.data["state"]["auto_enabled"] = bool(enabled)
            self.data["state"]["initialized"] = True
            self._save_now()

    async def set_auto(self, enabled: bool) -> None:
        async with self._lock:
            self.data["state"]["auto_enabled"] = bool(enabled)
            self.data["state"]["initialized"] = True
            self._save_now()

    async def reserve_generated(self, day: str, cap: int) -> bool:
        async with self._lock:
            state = self.data["state"]
            if state.get("generated_day") != day:
                state["generated_day"] = day
                state["generated_count"] = 0
            if cap > 0 and int(state.get("generated_count", 0)) >= cap:
                self._save_now()
                return False
            state["generated_count"] = int(state.get("generated_count", 0)) + 1
            self._save_now()
            return True

    async def remove(self, number: int) -> dict[str, Any] | None:
        async with self._lock:
            record = self._find(number)
            if record is None:
                return None
            self.data["vouches"].remove(record)
            self._save_now()
            return dict(record)
