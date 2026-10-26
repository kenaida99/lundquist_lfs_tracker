import asyncio
import json
import time

from fastapi.testclient import TestClient
from lfs_insim import InSim
from lfs_insim.packets import IS_ACR, IS_BFN, IS_BTC, IS_BTN, IS_ISM, IS_MST, IS_NCN, IS_RST, IS_STA, INST, TINY
from lfs_insim.testing import FakeLFS

from lfs_tracker.connection import InSimLink
from lfs_tracker.events import EventManager
from lfs_tracker.insim_handlers import CLOSE_ID, HostInfo, register
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


def test_race_settings_persist_without_reconnecting(tmp_path):
    path = tmp_path / "settings.json"
    link = FakeLink()
    client = TestClient(create_app(Tracker(), EventManager(), Settings(path), link))

    body = client.get("/api/settings").json()
    assert (body["qual_minutes"], body["race_laps"], body["grid_order"]) == (10, 5, "fixed")

    reply = client.post(
        "/api/settings", json={"qual_minutes": 20, "race_laps": 8, "grid_order": "reverse"}
    ).json()
    assert (reply["qual_minutes"], reply["race_laps"], reply["grid_order"]) == (20, 8, "reverse")
    assert link.restarts == []  # nothing about the connection changed
    saved = Settings(path)
    assert (saved.qual_minutes, saved.race_laps, saved.grid_order) == (20, 8, "reverse")

    # an unchanged connection setting does not reconnect either
    client.post("/api/settings", json={"lfs_port": 29999})
    assert link.restarts == []


def test_yes_no_options_are_sent_with_the_buttons_and_persist(tmp_path):
    path = tmp_path / "s.json"
    link = FakeLink()
    client = TestClient(create_app(Tracker(), EventManager(), Settings(path), link))
    body = client.get("/api/settings").json()
    assert (body["vote"], body["can_reset"], body["mid_race"]) == ("keep", "keep", "keep")

    client.post("/api/settings", json={"vote": "yes", "can_reset": "no", "mid_race": "yes"})
    assert link.restarts == []
    saved = Settings(path)
    assert (saved.vote, saved.can_reset, saved.mid_race) == ("yes", "no", "yes")

    options = ["/vote yes", "/canreset no", "/midrace yes"]
    assert client.post("/api/qualify").json()["sent"] == [
        "/laps=5",
        "/qual 10",
        *options,
        "/qualify",
    ]
    assert client.post("/api/start-race").json()["sent"] == [
        "/laps=5",
        "/start fixed",
        *options,  # still before /restart
        "/restart",
    ]

    client.post("/api/settings", json={"vote": "keep", "mid_race": "no"})
    assert client.post("/api/qualify").json()["sent"] == [
        "/laps=5",
        "/qual 10",
        "/canreset no",
        "/midrace no",
        "/qualify",
    ]
    for body in ({"vote": "maybe"}, {"can_reset": ""}, {"mid_race": True}):
        assert client.post("/api/settings", json=body).status_code == 422


def test_invalid_race_settings_are_rejected(tmp_path):
    client = TestClient(create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), FakeLink()))
    for body in (
        {"qual_minutes": 0},
        {"qual_minutes": 181},
        {"race_laps": 0},
        {"race_laps": 100},
        {"grid_order": "sideways"},
    ):
        assert client.post("/api/settings", json=body).status_code == 422


def test_qualify_and_start_race_buttons_send_the_commands(tmp_path):
    link = FakeLink()
    client = TestClient(create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), link))
    client.post(
        "/api/settings", json={"qual_minutes": 20, "race_laps": 5, "grid_order": "random"}
    )
    assert client.post("/api/qualify").json() == {"sent": ["/laps=5", "/qual 20", "/qualify"]}
    assert client.post("/api/start-race").json() == {
        "sent": ["/laps=5", "/start random", "/restart"]  # grid order before /restart
    }
    assert link.commands == [
        ["/laps=5", "/qual 20", "/qualify"],
        ["/laps=5", "/start random", "/restart"],
    ]


def test_practice_button_sends_laps_0_canreset_and_restart(tmp_path):
    link = FakeLink()
    client = TestClient(create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), link))
    client.post("/api/settings", json={"can_reset": "no", "race_laps": 8})  # does not affect it
    assert client.post("/api/practice").json() == {"sent": ["/laps=0", "/canreset yes", "/restart"]}
    assert link.commands == [["/laps=0", "/canreset yes", "/restart"]]


def test_buttons_report_when_not_connected(tmp_path):
    link = FakeLink()
    link.connected = False
    client = TestClient(create_app(Tracker(), EventManager(), Settings(tmp_path / "s.json"), link))
    for url in ("/api/practice", "/api/qualify", "/api/start-race"):
        reply = client.post(url)
        assert reply.status_code == 503 and "not connected" in reply.json()["detail"]


def test_link_sends_commands_to_lfs_and_logs_the_replies(monkeypatch):
    monkeypatch.setattr("lfs_tracker.connection.COMMAND_GAP_S", 0.01)

    async def scenario():
        async with FakeLFS() as lfs:
            link = InSimLink(
                Tracker(), EventManager(), host="127.0.0.1", port=lfs.port, admin_password=""
            )
            await link.start()
            await lfs.wait_connected()
            await asyncio.sleep(0.2)
            await link.send_commands(["/laps=5", "/start fixed", "/restart"])
            await asyncio.sleep(0.2)
            assert [p.Msg for p in lfs.received if isinstance(p, IS_MST)] == [
                "/laps=5",
                "/start fixed",
                "/restart",
            ]
            await lfs.send(IS_ACR(UCID=0, Result=1, Text="/restart"))
            await lfs.send(IS_ACR(UCID=0, Result=3, Text="/nope"))
            await lfs.send(IS_ACR(UCID=4, Result=1, Text="/kick bob"))  # someone else's command
            await asyncio.sleep(0.2)
            assert list(link.host_info.command_results) == [
                {"text": "/restart", "result": "accepted"},
                {"text": "/nope", "result": "unknown command"},
            ]
            await link.stop()
            try:
                await link.send_commands(["/restart"])
            except ConnectionError:
                pass
            else:
                raise AssertionError("expected ConnectionError")

    asyncio.run(scenario())


def test_commands_are_sent_200ms_apart():
    async def scenario():
        async with FakeLFS() as lfs:
            link = InSimLink(
                Tracker(), EventManager(), host="127.0.0.1", port=lfs.port, admin_password=""
            )
            await link.start()
            await lfs.wait_connected()
            await asyncio.sleep(0.2)
            started = time.monotonic()
            await link.send_commands(["/laps=5", "/qual 10", "/qualify"])
            elapsed = time.monotonic() - started
            assert 0.4 <= elapsed < 0.6  # two 200 ms gaps
            await link.stop()

    asyncio.run(scenario())


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


def test_welcome_lines_persist_and_are_validated(tmp_path):
    path = tmp_path / "s.json"
    client = TestClient(create_app(Tracker(), EventManager(), Settings(path), FakeLink()))
    assert client.get("/api/settings").json()["welcome_lines"] == [""] * 10

    lines = ["^2Welcome to ^3the server", "", "^1No contact", *[""] * 7]
    assert client.post("/api/settings", json={"welcome_lines": lines}).json()["welcome_lines"] == lines
    assert Settings(path).welcome_lines == lines
    assert Settings(path).welcome_enabled

    for bad in (lines[:9], [*lines, "x"], ["x" * 81, *[""] * 9]):
        assert client.post("/api/settings", json={"welcome_lines": bad}).status_code == 422
    assert Settings(path).welcome_lines == lines


def test_welcome_board_is_sent_to_new_connections_only():
    async def scenario():
        settings = Settings()
        settings.update(welcome_lines=["^2Hello", "", "^1Rules", *[""] * 7])
        async with FakeLFS() as lfs:
            lfs.replies[TINY.NCN] = [IS_NCN(ReqI=1, UCID=2, UName="old", PName="Old")]
            insim = InSim(port=lfs.port)
            register(insim, Tracker(), EventManager(), settings=settings)
            async with insim:
                await lfs.wait_connected()
                await lfs.send(IS_NCN(UCID=0, UName="", PName="host"))  # the host itself
                await lfs.send(IS_NCN(UCID=5, UName="new", PName="New"))
                await asyncio.sleep(0.3)
                buttons = [p for p in lfs.received if isinstance(p, IS_BTN)]
                assert {b.UCID for b in buttons} == {5}  # not the replayed UCID 2, not the host
                assert [b.Text for b in buttons if b.Text] == ["^2Hello", "^1Rules", "Close"]
                assert len(buttons) == 5  # 2 backgrounds, 2 filled lines, close
                assert all(b.Inst & INST.ALWAYS_ON for b in buttons)  # joining drivers are not on track

                await lfs.send(IS_BTC(ReqI=200, UCID=5, ClickID=CLOSE_ID))
                await asyncio.sleep(0.3)
                deletes = [p for p in lfs.received if isinstance(p, IS_BFN)]
                assert [(d.UCID, d.ClickID, d.ClickMax) for d in deletes] == [(5, 0, CLOSE_ID)]

    asyncio.run(scenario())


def test_no_welcome_board_when_all_lines_are_blank():
    async def scenario():
        async with FakeLFS() as lfs:
            insim = InSim(port=lfs.port)
            register(insim, Tracker(), EventManager(), settings=Settings())
            async with insim:
                await lfs.wait_connected()
                await lfs.send(IS_NCN(UCID=5, UName="new", PName="New"))
                await asyncio.sleep(0.3)
                assert not [p for p in lfs.received if isinstance(p, IS_BTN)]

    asyncio.run(scenario())
