"""Settings kept in a JSON file next to the race data, editable from the web page."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


DEFAULT_LFS_HOST = "127.0.0.1"
DEFAULT_LFS_PORT = 29999
GRID_ORDERS = ("fixed", "reverse", "random")
# Host options that are sent with the buttons: "keep" leaves whatever LFS currently has.
CHOICES = ("keep", "yes", "no")


class Settings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.admin_password = ""
        self.lfs_host = DEFAULT_LFS_HOST
        self.lfs_port = DEFAULT_LFS_PORT
        self.qual_minutes = 10
        self.race_laps = 5
        self.grid_order = "fixed"  # one of GRID_ORDERS
        self.vote = "keep"  # /vote: may everyone vote to restart, not only admins
        self.can_reset = "keep"  # /canreset
        self.mid_race = "keep"  # /midrace: may cars join after the race has started
        self._load()

    def update(
        self,
        *,
        lfs_host: str | None = None,
        lfs_port: int | None = None,
        admin_password: str | None = None,
        qual_minutes: int | None = None,
        race_laps: int | None = None,
        grid_order: str | None = None,
        vote: str | None = None,
        can_reset: str | None = None,
        mid_race: str | None = None,
    ) -> None:
        """Change the given settings (None = leave as is) and save."""
        if lfs_host is not None:
            self.lfs_host = lfs_host
        if lfs_port is not None:
            self.lfs_port = lfs_port
        if admin_password is not None:
            self.admin_password = admin_password
        if qual_minutes is not None:
            self.qual_minutes = qual_minutes
        if race_laps is not None:
            self.race_laps = race_laps
        if grid_order is not None:
            if grid_order not in GRID_ORDERS:
                raise ValueError(f"grid order must be one of {GRID_ORDERS}")
            self.grid_order = grid_order
        for name, value in (("vote", vote), ("can_reset", can_reset), ("mid_race", mid_race)):
            if value is not None:
                if value not in CHOICES:
                    raise ValueError(f"{name} must be one of {CHOICES}")
                setattr(self, name, value)
        self._save()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.admin_password = str(data.get("admin_password", ""))
            self.lfs_host = str(data.get("lfs_host", DEFAULT_LFS_HOST))
            self.lfs_port = int(data.get("lfs_port", DEFAULT_LFS_PORT))
            self.qual_minutes = int(data.get("qual_minutes", self.qual_minutes))
            self.race_laps = int(data.get("race_laps", self.race_laps))
            if data.get("grid_order") in GRID_ORDERS:
                self.grid_order = data["grid_order"]
            for name in ("vote", "can_reset", "mid_race"):
                if data.get(name) in CHOICES:
                    setattr(self, name, data[name])
        except (OSError, ValueError, AttributeError) as error:
            log.warning("could not read %s (%s); using defaults", self.path, error)

    def _save(self) -> None:
        if self.path is None:
            return
        data = {
            "lfs_host": self.lfs_host,
            "lfs_port": self.lfs_port,
            "admin_password": self.admin_password,
            "qual_minutes": self.qual_minutes,
            "race_laps": self.race_laps,
            "grid_order": self.grid_order,
            "vote": self.vote,
            "can_reset": self.can_reset,
            "mid_race": self.mid_race,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as error:
            log.warning("could not save %s: %s", self.path, error)
