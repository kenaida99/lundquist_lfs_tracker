import asyncio

from fastapi.testclient import TestClient
from lfs_insim import InSim
from lfs_insim.packets import (
    IS_CNL,
    IS_NCN,
    IS_NPL,
    IS_PLL,
    IS_PLP,
    IS_UCO,
    TINY,
    UCO,
    ObjectInfo,
)
from lfs_insim.testing import FakeLFS

from lfs_tracker.events import EventManager
from lfs_tracker.insim_handlers import HostInfo, register
from lfs_tracker.settings import Settings
from lfs_tracker.tracker import Tracker
from lfs_tracker.web import create_app


class FakeLink:
    connected = True
    host_info = HostInfo()

    def __init__(self):
        self.restarts = []
        self.commands = []

    async def restart(self, *, host, port, admin_password):
        self.restarts.append((host, port, admin_password))

    async def send_commands(self, commands):
        if not self.connected:
            raise ConnectionError("not connected to LFS")
        self.commands.append(commands)


async def settle() -> None:
    await asyncio.sleep(0.2)


def names(snapshot, key):
    return [p["username"] for p in snapshot[key]]


def test_full_flow():
    async def scenario():
        tracker = Tracker()
        async with FakeLFS() as lfs:
            lfs.replies[TINY.NCN] = [
                IS_NCN(UCID=0, UName="", PName="host", Total=2),
                IS_NCN(UCID=3, UName="alice", PName="^1Alice", Total=2),
            ]
            lfs.replies[TINY.NPL] = [IS_NPL(PLID=7, UCID=3, PName="Alice")]
            insim = InSim(port=lfs.port)
            register(insim, tracker, EventManager())
            async with insim:
                await lfs.wait_connected()
                await settle()
                snap = tracker.snapshot()
                assert names(snap, "on_track") == ["alice"]
                assert snap["on_track"][0]["plid"] == 7
                assert snap["on_track"][0]["nickname"] == "Alice"

                await lfs.send(IS_NCN(UCID=4, UName="bob", PName="Bob"))
                await settle()
                assert names(tracker.snapshot(), "in_garage") == ["bob"]

                await lfs.send(IS_NPL(PLID=9, UCID=4, PName="Bob"))
                await lfs.send(
                    IS_UCO(
                        PLID=9,
                        UCOAction=UCO.CIRCLE_ENTER,
                        Time=1234,
                        Info=ObjectInfo(X=16 * 10, Y=-16 * 5, Heading=2),
                    )
                )
                await lfs.send(IS_UCO(PLID=9, UCOAction=UCO.CIRCLE_LEAVE, Info=ObjectInfo()))
                await settle()
                snap = tracker.snapshot()
                assert names(snap, "on_track") == ["alice", "bob"]
                assert len(snap["circle_log"]) == 1
                entry = snap["circle_log"][0]
                assert (entry["username"], entry["circle"], entry["x"], entry["y"]) == (
                    "bob",
                    2,
                    10,
                    -5,
                )

                await lfs.send(IS_PLP(PLID=9))
                await lfs.send(IS_PLL(PLID=7))
                await settle()
                snap = tracker.snapshot()
                assert snap["on_track"] == []
                assert names(snap, "in_garage") == ["alice", "bob"]
                assert all(p["plid"] is None for p in snap["in_garage"])

                await lfs.send(IS_NPL(PLID=11, UCID=4, PName="Bob"))
                await settle()
                assert tracker.snapshot()["on_track"][0]["plid"] == 11

                await lfs.send(IS_CNL(UCID=4), IS_CNL(UCID=3))
                await settle()
                snap = tracker.snapshot()
                assert snap["on_track"] == [] and snap["in_garage"] == []
                assert len(snap["circle_log"]) == 1  # the log outlives the player

    asyncio.run(scenario())


def test_ai_players_ignored():
    async def scenario():
        tracker = Tracker()
        async with FakeLFS() as lfs:
            insim = InSim(port=lfs.port)
            register(insim, tracker, EventManager())
            async with insim:
                await lfs.wait_connected()
                await lfs.send(IS_NCN(UCID=1, UName="alice", PName="Alice"))
                await lfs.send(IS_NPL(PLID=5, UCID=1, PType=2, PName="AI"))
                await settle()
                assert names(tracker.snapshot(), "in_garage") == ["alice"]

    asyncio.run(scenario())


def test_web_api_and_page():
    tracker = Tracker()
    tracker.player_connected(1, "alice", "Alice")
    tracker.joined_track(4, 1)
    client = TestClient(create_app(tracker, EventManager(), Settings(), FakeLink()))
    data = client.get("/api/players").json()
    assert data["lfs_connected"] is True
    assert [p["plid"] for p in data["on_track"]] == [4]
    assert client.get("/").status_code == 200
