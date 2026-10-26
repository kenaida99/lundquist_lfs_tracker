"""Owns the InSim connection so it can be restarted (e.g. after the admin password changes)."""

from __future__ import annotations

import asyncio

from lfs_insim import InSim

from lfs_tracker.events import EventManager
from lfs_tracker.insim_handlers import HostInfo, register
from lfs_tracker.tracker import Tracker

COMMAND_GAP_S = 0.3  # between commands, so LFS handles each before the next


class InSimLink:
    def __init__(
        self,
        tracker: Tracker,
        events: EventManager,
        *,
        host: str,
        port: int,
        admin_password: str,
    ) -> None:
        self._tracker = tracker
        self._events = events
        self._host = host
        self._port = port
        self.host_info = HostInfo()
        self._insim: InSim | None = None
        self._admin_password = admin_password

    @property
    def connected(self) -> bool:
        return self._insim is not None and self._insim.connected

    async def start(self) -> None:
        self.host_info = HostInfo()
        insim = InSim(
            name="lfs_tracker",
            admin_password=self._admin_password,
            host=self._host,
            port=self._port,
        )
        register(insim, self._tracker, self._events, self.host_info)
        self._insim = insim
        await insim.__aenter__()

    async def stop(self) -> None:
        insim, self._insim = self._insim, None
        if insim is not None:
            await insim.__aexit__(None, None, None)

    async def restart(self, *, host: str, port: int, admin_password: str) -> None:
        await self.stop()
        self._host, self._port, self._admin_password = host, port, admin_password
        await self.start()

    async def send_commands(self, commands: list[str]) -> None:
        """Send LFS commands as if typed in LFS, one after the other.

        Raises ConnectionError when not connected.
        """
        if self._insim is None or not self._insim.connected:
            raise ConnectionError("not connected to LFS")
        for i, command in enumerate(commands):
            if i:
                await asyncio.sleep(COMMAND_GAP_S)
            self._insim.command(command)
