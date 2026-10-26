"""Race events: practice bests, qualifying leaderboard and races, stored in a JSON file.

An event is started by the user. While one is active:
  - practice (RaceInProg 0): the fastest lap per driver is kept (from IS_LAP);
  - qualifying (RaceInProg 2): the fastest *valid* lap per driver is kept; a lap during which the
    driver entered an insim circle is invalid;
  - race (RaceInProg 1): every IS_RST starts a new race; IS_RES results and circle hits are kept,
    and the final standings are computed when read (see standings.py).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lfs_tracker.standings import compute_standings

log = logging.getLogger(__name__)

PRACTICE, RACE, QUALIFYING = 0, 1, 2
RST_STATE_WINDOW_S = 5.0  # how long an IS_RST waits for the IS_STA that confirms the new mode
NOT_IN_TABLE = 255


@dataclass(frozen=True)
class Violation:
    mode: int
    race_hits: int = 0  # circle hits so far in this race (race mode only)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EventManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._in_prog = 0  # IS_STA RaceInProg
        self._sta_laps: int | None = None  # IS_STA RaceLaps
        self._rst_laps: int | None = None  # RaceLaps of the last IS_RST
        self._pending_start: tuple[int, float] | None = None  # (race laps, when)
        self._lap_hits: dict[str, int] = {}  # username -> circle hits since their last lap
        self._load()

    # ---- events -------------------------------------------------------------------------

    @property
    def active(self) -> dict[str, Any] | None:
        if self.events and self.events[-1]["ended_at"] is None:
            return self.events[-1]
        return None

    def start_event(self, name: str = "") -> dict[str, Any]:
        self.end_event(save=False)
        number = max((e["id"] for e in self.events), default=0) + 1
        event = {
            "id": number,
            "name": name.strip() or f"Event {number}",
            "started_at": _now(),
            "ended_at": None,
            "practice": {},
            "qualifying": {},
            "races": [],
        }
        self.events.append(event)
        self._lap_hits.clear()
        self._save()
        return event

    def end_event(self, save: bool = True) -> None:
        if (event := self.active) is not None:
            event["ended_at"] = _now()
            if save:
                self._save()

    # ---- session tracking (IS_STA / IS_RST) ---------------------------------------------

    @property
    def mode(self) -> int:
        """PRACTICE, RACE or QUALIFYING.

        LFS reports practice as RaceInProg 1 with 0 laps (RaceInProg 0 is "no race running"), so
        for RaceInProg 1 the lap count decides: the one from the last IS_RST, else from IS_STA.
        """
        if self._in_prog == QUALIFYING:
            return QUALIFYING
        if self._in_prog == PRACTICE:
            return PRACTICE
        laps = self._rst_laps if self._rst_laps is not None else self._sta_laps
        return PRACTICE if laps == 0 else RACE

    def state_changed(self, race_in_prog: int, race_laps: int | None = None) -> None:
        """IS_STA: RaceInProg is 0 (no race), 1 (race or practice) or 2 (qualifying)."""
        previous, self._in_prog = self._in_prog, race_in_prog
        if race_laps is not None:
            self._sta_laps = race_laps
        if self._pending_start is None:
            return
        laps, at = self._pending_start
        if time.monotonic() - at > RST_STATE_WINDOW_S:
            self._pending_start = None
        elif race_in_prog == 1 and previous != 1:
            self._pending_start = None
            self._start_race(laps)

    def race_started(self, race_laps: int, qual_mins: int, requested: bool = False) -> None:
        """IS_RST: a session starts. RaceLaps > 0 is a race; 0 is practice or qualifying.

        `requested`: the packet answers our own request (on connect), so nothing starts.
        LFS may send it before or after the IS_STA carrying the new RaceInProg, so a race
        start waits (briefly) for that IS_STA when the race is not in progress yet.
        """
        self._rst_laps = race_laps
        if requested:
            return
        if race_laps == 0:
            self._pending_start = None
            self._lap_hits.clear()
        elif self._in_prog == 1:
            self._pending_start = None
            self._start_race(race_laps)
        else:
            self._pending_start = (race_laps, time.monotonic())

    def _start_race(self, race_laps: int) -> None:
        self._lap_hits.clear()
        if (event := self.active) is not None:
            self._new_race(event, race_laps)

    def _new_race(self, event: dict[str, Any], race_laps: int = 0) -> dict[str, Any]:
        race = {
            "number": len(event["races"]) + 1,
            "started_at": _now(),
            "race_laps": race_laps,
            "results": {},
            "circle_hits": {},
        }
        event["races"].append(race)
        self._save()
        return race

    def _current_race(self) -> dict[str, Any] | None:
        event = self.active
        if event is None:
            return None
        if event["races"]:
            return event["races"][-1]
        if self.mode == RACE:  # the event was started while a race was already running
            return self._new_race(event)
        return None

    # ---- data from the game -------------------------------------------------------------

    def player_on_track(self, username: str) -> None:
        self._lap_hits.pop(username, None)

    def lap(self, username: str, nickname: str, lap_ms: int) -> None:
        """IS_LAP: a completed lap."""
        hits = self._lap_hits.pop(username, 0)
        event = self.active
        if event is None or lap_ms <= 0:
            return
        if self.mode == PRACTICE:
            table = event["practice"]
        elif self.mode == QUALIFYING:
            table = event["qualifying"]
        else:
            return
        if hits:  # an insim circle was entered during this lap
            return
        best = table.get(username)
        if best is None or lap_ms < best["best_ms"]:
            table[username] = {"nickname": nickname, "best_ms": lap_ms, "at": _now()}
        elif best["nickname"] != nickname:
            best["nickname"] = nickname
        self._save()

    def circle_hit(self, username: str, nickname: str) -> Violation | None:
        """IS_UCO circle entry. Returns what the driver should be told, if an event is active."""
        self._lap_hits[username] = self._lap_hits.get(username, 0) + 1
        if self.active is None:
            return None
        if self.mode != RACE:
            return Violation(self.mode)
        if (race := self._current_race()) is None:
            return None
        entry = race["circle_hits"].setdefault(username, {"nickname": nickname, "hits": 0})
        entry["hits"] += 1
        entry["nickname"] = nickname
        self._save()
        return Violation(RACE, entry["hits"])

    def result(
        self,
        *,
        username: str,
        nickname: str,
        car: str,
        total_ms: int,
        best_ms: int,
        laps: int,
        position: int,
        confirm: int,
        lfs_penalty_s: int,
        stops: int,
    ) -> None:
        """IS_RES: a race result (qualifying uses laps instead, see module docstring)."""
        if self.mode == QUALIFYING or position == NOT_IN_TABLE or not username:
            return
        if (race := self._current_race()) is None:
            return
        race["results"][username] = {
            "username": username,
            "nickname": nickname,
            "car": car,
            "total_ms": total_ms,
            "best_ms": best_ms,
            "laps": laps,
            "position": position,
            "confirm": confirm,
            "lfs_penalty_s": lfs_penalty_s,
            "stops": stops,
        }
        self._save()

    # ---- reading ------------------------------------------------------------------------

    def summaries(self) -> list[dict[str, Any]]:
        active = self.active
        return [
            {
                "id": e["id"],
                "name": e["name"],
                "started_at": e["started_at"],
                "ended_at": e["ended_at"],
                "active": e is active,
                "races": len(e["races"]),
            }
            for e in reversed(self.events)
        ]

    def event_view(self, event_id: int) -> dict[str, Any] | None:
        event = next((e for e in self.events if e["id"] == event_id), None)
        if event is None:
            return None
        return {
            "id": event["id"],
            "name": event["name"],
            "started_at": event["started_at"],
            "ended_at": event["ended_at"],
            "active": event is self.active,
            "practice": _leaderboard(event["practice"]),
            "qualifying": _leaderboard(event["qualifying"]),
            "races": [
                {
                    "number": r["number"],
                    "started_at": r["started_at"],
                    "race_laps": decode_race_laps(r["race_laps"]),
                    "standings": compute_standings(r),
                }
                for r in event["races"]
            ],
        }

    # ---- persistence --------------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            self.events = json.loads(self.path.read_text(encoding="utf-8"))["events"]
        except (OSError, ValueError, KeyError) as error:
            backup = self.path.with_suffix(".corrupt")
            log.warning("could not read %s (%s); moving it to %s", self.path, error, backup)
            os.replace(self.path, backup)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps({"events": self.events}, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as error:
            log.warning("could not save %s: %s", self.path, error)


def decode_race_laps(value: int) -> str:
    """LFS RaceLaps byte: 1-99 laps, 100-190 = 100 + 10 * (n - 100) laps, 191+ = hours."""
    if value == 0:
        return ""
    if value < 100:
        return str(value)
    if value < 191:
        return str(100 + (value - 100) * 10)
    return f"{value - 190} h"


def _leaderboard(table: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(table.items(), key=lambda kv: kv[1]["best_ms"])
    fastest = rows[0][1]["best_ms"] if rows else 0
    return [
        {
            "position": i,
            "username": username,
            "nickname": row["nickname"],
            "best_ms": row["best_ms"],
            "gap_ms": row["best_ms"] - fastest,
        }
        for i, (username, row) in enumerate(rows, start=1)
    ]
