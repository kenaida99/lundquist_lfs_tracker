import asyncio
import json

from fastapi.testclient import TestClient
from lfs_insim import InSim
from lfs_insim.packets import IS_ISM, IS_RST, IS_STA, TINY
from lfs_insim.testing import FakeLFS

from lfs_tracker.connection import InSimLink
from lfs_tracker.events import EventManager
from lfs_tracker.insim_handlers import HostInfo
from lfs_tracker.settings import Settings
from lfs_tracker.tracker import Tracker
from lfs_tracker.web import create_app
from tests.test_tracker import FakeLink


def test_settings_persist_and_password_is_never_returned(tmp_path, monkeypatch):
    monkeypatch.setattr("lfs_tracker.web.RECONNECT_WAIT_S", 0)
    path = tmp_path / "settings.json"
    settings, link = Settings(path), FakeLink()
    client = TestClient(create_app(Tracker(), EventManager(), settings, link))

    first = client.get("/api/settings").json()
    assert first["admin_password_set"] is False
    assert (first["lfs_host"], first["lfs_port"]) == ("127.0.0.1", 29999)

    reply = client.post("/api/settings", json={"admin_password": "s3cret"}).json()
    assert reply["admin_password_set"] is True
    assert "s3cret" not in json.dumps(reply) and "s3cret" not in client.get("/api/settings").text
    assert link.restarts == [("127.0.0.1", 29999, "s3cret")]
    assert Settings(path).admin_password == "s3cret"

    # host and port change; the password is kept when not sent
    client.post("/api/settings", json={"lfs_host": " 192.168.1.20 ", "lfs_port": 30000})
    assert link.restarts[-1] == ("192.168.1.20", 30000, "s3cret")
    saved = Settings(path)
    assert (saved.lfs_host, saved.lfs_port, saved.admin_password) == (
        "192.168.1.20",
        30000,
        "s3cret",
    )

    client.post("/api/settings", json={"admin_password": ""})
    assert link.restarts[-1] == ("192.168.1.20", 30000, "")
    assert Settings(path).admin_password == ""


def test_invalid_host_or_port_is_rejected(tmp_path):
    link = FakeLink()
    client = TestClient(create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), link))
    for body in ({"lfs_host": "  "}, {"lfs_host": ""}, {"lfs_port": 0}, {"lfs_port": 70000}):
        assert client.post("/api/settings", json=body).status_code == 422
    assert link.restarts == []


def test_role_is_reported():
    link = FakeLink()
    link.host_info = HostInfo(known=True, is_host=False, name="My Server")
    client = TestClient(create_app(Tracker(), EventManager(), Settings(), link))
    body = client.get("/api/settings").json()
    assert (body["role"], body["host_name"]) == ("guest", "My Server")


def test_link_restart_reconnects_to_another_host_with_the_new_password():
    async def scenario():
        async with FakeLFS() as lfs, FakeLFS(admin="pw") as other:  # `other` checks the password
            lfs.replies[TINY.ISM] = [IS_ISM(Host=0, HName="^1Race")]
            lfs.replies[TINY.SST] = [IS_STA(RaceInProg=1, RaceLaps=5, QualMins=5)]
            lfs.replies[TINY.RST] = [IS_RST(RaceLaps=0, QualMins=0)]  # like a practice session
            other.replies[TINY.ISM] = [IS_ISM(Host=1, HName="Mine")]
            events = EventManager()
            events.start_event()
            link = InSimLink(
                Tracker(), events, host="127.0.0.1", port=lfs.port, admin_password=""
            )
            await link.start()
            await lfs.wait_connected()
            await asyncio.sleep(0.3)
            assert link.connected
            assert events.mode == 0  # the state and session requested on connect were applied
            assert events.active["races"] == []
            assert (link.host_info.known, link.host_info.is_host, link.host_info.name) == (
                True,
                False,
                "Race",
            )
            await link.restart(host="127.0.0.1", port=other.port, admin_password="pw")
            await other.wait_connected()
            await asyncio.sleep(0.3)
            assert other.connections == 1 and not lfs.connected
            assert link._insim._isi.Admin == "pw"
            assert (link.host_info.is_host, link.host_info.name) == (True, "Mine")
            await link.stop()
            assert not link.connected

    asyncio.run(scenario())
