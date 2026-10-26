"""Settings kept in a JSON file next to the race data, editable from the web page."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


DEFAULT_LFS_HOST = "127.0.0.1"
DEFAULT_LFS_PORT = 29999


class Settings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.admin_password = ""
        self.lfs_host = DEFAULT_LFS_HOST
        self.lfs_port = DEFAULT_LFS_PORT
        self._load()

    def update(
        self,
        *,
        lfs_host: str | None = None,
        lfs_port: int | None = None,
        admin_password: str | None = None,
    ) -> None:
        """Change the given settings (None = leave as is) and save."""
        if lfs_host is not None:
            self.lfs_host = lfs_host
        if lfs_port is not None:
            self.lfs_port = lfs_port
        if admin_password is not None:
            self.admin_password = admin_password
        self._save()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.admin_password = str(data.get("admin_password", ""))
            self.lfs_host = str(data.get("lfs_host", DEFAULT_LFS_HOST))
            self.lfs_port = int(data.get("lfs_port", DEFAULT_LFS_PORT))
        except (OSError, ValueError, AttributeError) as error:
            log.warning("could not read %s (%s); using defaults", self.path, error)

    def _save(self) -> None:
        if self.path is None:
            return
        data = {
            "lfs_host": self.lfs_host,
            "lfs_port": self.lfs_port,
            "admin_password": self.admin_password,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as error:
            log.warning("could not save %s: %s", self.path, error)
