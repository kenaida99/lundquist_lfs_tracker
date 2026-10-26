"""In-memory record of connected players, who is on track, and insim circle entries."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

MAX_CIRCLE_ENTRIES = 1000
MAX_ENTRIES_PER_PLAYER = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class CircleEntry:
    username: str
    ucid: int
    plid: int
    circle: int
    lfs_time_ms: int
    x: float
    y: float
    at: str = field(default_factory=_now)


@dataclass
class Player:
    ucid: int
    username: str
    nickname: str
    plid: int | None = None  # None while in the garage; a new value each time they join the track
    on_track_since: str | None = None
    circle_entries: deque[CircleEntry] = field(
        default_factory=lambda: deque(maxlen=MAX_ENTRIES_PER_PLAYER)
    )

    @property
    def on_track(self) -> bool:
        return self.plid is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ucid": self.ucid,
            "username": self.username,
            "nickname": self.nickname,
            "plid": self.plid,
            "on_track_since": self.on_track_since,
            "circle_entries": [asdict(e) for e in reversed(self.circle_entries)],
        }


class Tracker:
    def __init__(self) -> None:
        self._players: dict[int, Player] = {}  # by UCID
        self._by_plid: dict[int, int] = {}  # PLID -> UCID
        self.circle_log: deque[CircleEntry] = deque(maxlen=MAX_CIRCLE_ENTRIES)

    def clear(self) -> None:
        """Forget every player (the connection to LFS was reset). The circle log is kept."""
        self._players.clear()
        self._by_plid.clear()

    def player_connected(self, ucid: int, username: str, nickname: str) -> None:
        if ucid == 0 and not username:
            return  # the host of a dedicated server is not a player
        old = self._players.get(ucid)
        if old is not None:
            self._release_plid(old)
        self._players[ucid] = Player(ucid=ucid, username=username, nickname=nickname)

    def player_renamed(self, ucid: int, nickname: str) -> None:
        if (player := self._players.get(ucid)) is not None:
            player.nickname = nickname

    def player_left(self, ucid: int) -> None:
        """The connection left the server: remove them completely."""
        if (player := self._players.pop(ucid, None)) is not None:
            self._release_plid(player)

    def joined_track(self, plid: int, ucid: int) -> None:
        if (player := self._players.get(ucid)) is None:
            return
        self._release_plid(player)
        # LFS may reuse a PLID: drop any stale mapping to another connection
        if (other := self._players.get(self._by_plid.get(plid, -1))) is not None:
            self._release_plid(other)
        player.plid = plid
        player.on_track_since = _now()
        self._by_plid[plid] = ucid

    def left_track(self, plid: int) -> None:
        """The driver went back to the garage (pits) or to spectate: clear their PLID."""
        if (player := self._player_by_plid(plid)) is not None:
            self._release_plid(player)

    def circle_entered(
        self, plid: int, lfs_time_ms: int, circle: int, x: float, y: float
    ) -> CircleEntry | None:
        if (player := self._player_by_plid(plid)) is None:
            return None
        entry = CircleEntry(
            username=player.username,
            ucid=player.ucid,
            plid=plid,
            circle=circle,
            lfs_time_ms=lfs_time_ms,
            x=x,
            y=y,
        )
        player.circle_entries.append(entry)
        self.circle_log.append(entry)
        return entry

    def player_for_plid(self, plid: int) -> Player | None:
        return self._player_by_plid(plid)

    def snapshot(self, log_limit: int = 100) -> dict[str, Any]:
        players = sorted(self._players.values(), key=lambda p: p.username.lower())
        return {
            "on_track": [p.to_dict() for p in players if p.on_track],
            "in_garage": [p.to_dict() for p in players if not p.on_track],
            "circle_log": [asdict(e) for e in list(self.circle_log)[::-1][:log_limit]],
        }

    def _player_by_plid(self, plid: int) -> Player | None:
        return self._players.get(self._by_plid.get(plid, -1))

    def _release_plid(self, player: Player) -> None:
        if player.plid is not None and self._by_plid.get(player.plid) == player.ucid:
            del self._by_plid[player.plid]
        player.plid = None
        player.on_track_since = None
