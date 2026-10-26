import asyncio
import json

from lfs_insim import InSim
from lfs_insim.packets import (
    CONF,
    IS_LAP,
    IS_MTC,
    IS_NCN,
    IS_NPL,
    IS_RES,
    IS_RST,
    IS_STA,
    IS_UCO,
    UCO,
    ObjectInfo,
)
from lfs_insim.testing import FakeLFS

from lfs_tracker.events import PRACTICE, QUALIFYING, RACE, EventManager, Violation
from lfs_tracker.insim_handlers import register
from lfs_tracker.tracker import Tracker


def result(events, username, total, best, laps, position, confirm=0, penalty_s=0):
    events.result(
        username=username,
        nickname=username.title(),
        car="XFG",
        total_ms=total,
        best_ms=best,
        laps=laps,
        position=position,
        confirm=confirm,
        lfs_penalty_s=penalty_s,
        stops=0,
    )


def test_nothing_recorded_without_an_event():
    events = EventManager()
    events.lap("alice", "Alice", 60000)
    assert events.events == []


def test_practice_keeps_only_fastest_lap():
    events = EventManager()
    events.start_event("Night")
    for ms in (62000, 60500, 61000):
        events.lap("alice", "Alice", ms)
    view = events.event_view(1)
    assert [(r["username"], r["best_ms"]) for r in view["practice"]] == [("alice", 60500)]


def test_practice_lap_with_circle_hit_is_not_recorded():
    events = EventManager()
    events.start_event()
    events.lap("alice", "Alice", 61000)
    events.circle_hit("alice", "Alice")
    events.lap("alice", "Alice", 59000)
    assert events.event_view(1)["practice"][0]["best_ms"] == 61000


def test_violation_messages():
    from lfs_tracker.events import PRACTICE, Violation
    from lfs_tracker.insim_handlers import violation_message

    assert violation_message(Violation(PRACTICE)) == (
        "^1Track violation occurred - lap time will not be recorded"
    )
    assert violation_message(Violation(QUALIFYING)).startswith("^1Track violation occurred")
    assert violation_message(Violation(RACE, 1)) == "^1Track cut - 2 more = 5 second penalty"
    assert violation_message(Violation(RACE, 2)) == "^1Track cut - 1 more = 5 second penalty"
    assert violation_message(Violation(RACE, 3)) == (
        "^1Track cut - 5 second penalty added, 3 more = another penalty"
    )
    assert violation_message(Violation(RACE, 4)) == "^1Track cut - 2 more = 5 second penalty"


def test_circle_hit_without_event_gives_no_message():
    events = EventManager()
    assert events.circle_hit("alice", "Alice") is None


def test_driver_is_messaged_on_circle_hit():
    async def scenario():
        tracker, events = Tracker(), EventManager()
        events.start_event()
        async with FakeLFS() as lfs:
            insim = InSim(port=lfs.port)
            register(insim, tracker, events)
            async with insim:
                await lfs.wait_connected()
                await lfs.send(IS_NCN(UCID=7, UName="alice", PName="Alice"))
                await lfs.send(IS_NPL(PLID=5, UCID=7, PName="Alice"))
                circle = IS_UCO(PLID=5, UCOAction=UCO.CIRCLE_ENTER, Info=ObjectInfo())
                await lfs.send(circle)
                await lfs.send(IS_STA(RaceInProg=1))
                await lfs.send(IS_RST(RaceLaps=3))
                await lfs.send(circle)
                await asyncio.sleep(0.3)
                sent = [(p.UCID, p.Text) for p in lfs.received if isinstance(p, IS_MTC)]
                assert sent == [
                    (7, "^1Track violation occurred - lap time will not be recorded"),
                    (7, "^1Track cut - 2 more = 5 second penalty"),
                ]

    asyncio.run(scenario())


def test_practice_is_race_in_progress_with_zero_laps():
    # What a real server reports while practising: RaceInProg 1, RaceLaps 0 (RST: 0 laps, 0 mins)
    events = EventManager()
    events.start_event()
    events.state_changed(1, 0)
    events.race_started(0, 0, requested=True)
    assert events.mode == PRACTICE
    assert events.circle_hit("alice", "Alice") == Violation(PRACTICE)
    events.lap("alice", "Alice", 59000)  # invalid: the circle was hit in this lap
    events.lap("alice", "Alice", 60000)
    assert events.event_view(1)["practice"][0]["best_ms"] == 60000
    assert events.active["races"] == []


def test_mode_follows_rst_laps_then_sta():
    events = EventManager()
    events.state_changed(1, 5)  # no IS_RST seen: IS_STA's laps decide
    assert events.mode == RACE
    events.race_started(0, 0, requested=True)  # the session's own IS_RST wins
    assert events.mode == PRACTICE
    events.state_changed(2, 5)  # qualifying, whatever the race laps setting says
    assert events.mode == QUALIFYING
    events.state_changed(1, 5)
    events.race_started(5, 0)  # a race starts
    assert events.mode == RACE
    events.state_changed(0, 5)  # race over
    assert events.mode == PRACTICE


def test_reply_to_our_own_rst_request_does_not_start_a_race():
    events = EventManager()
    events.start_event()
    events.state_changed(1, 8)
    events.race_started(8, 0, requested=True)
    assert events.active["races"] == []
    events.circle_hit("alice", "Alice")  # but a race is in progress: it gets created lazily
    assert len(events.active["races"]) == 1


def test_race_rst_while_idle_waits_for_state():
    events = EventManager()
    events.start_event()
    events.race_started(5, 0)  # RaceInProg 0: nothing running yet
    assert events.active["races"] == []
    events.state_changed(1, 5)
    assert len(events.active["races"]) == 1


def test_qualifying_lap_with_circle_hit_is_invalid():
    events = EventManager()
    events.start_event()
    events.state_changed(QUALIFYING)
    events.lap("alice", "Alice", 61000)
    events.circle_hit("alice", "Alice")
    events.lap("alice", "Alice", 59000)  # fastest, but the circle invalidates it
    events.lap("alice", "Alice", 60000)  # the next lap is clean again
    events.lap("bob", "Bob", 60500)
    board = events.event_view(1)["qualifying"]
    assert [(r["username"], r["best_ms"], r["gap_ms"]) for r in board] == [
        ("alice", 60000, 0),
        ("bob", 60500, 500),
    ]
    assert events.event_view(1)["practice"] == []


def test_rst_before_or_after_sta_starts_one_race_each_time():
    events = EventManager()
    events.start_event()
    events.race_started(race_laps=10, qual_mins=0)  # RST first
    events.state_changed(RACE)
    assert len(events.active["races"]) == 1
    events.state_changed(RACE)  # unrelated STA changes do nothing
    assert len(events.active["races"]) == 1

    events.race_started(10, 0)  # restart while racing
    assert len(events.active["races"]) == 2

    events.state_changed(PRACTICE)
    events.state_changed(RACE)  # STA first, then RST
    assert len(events.active["races"]) == 2
    events.race_started(10, 0)
    assert [r["number"] for r in events.active["races"]] == [1, 2, 3]


def test_quali_to_race_with_stale_state():
    events = EventManager()
    events.start_event()
    events.state_changed(QUALIFYING)
    events.race_started(race_laps=5, qual_mins=0)  # STA still says qualifying
    assert events.active["races"] == []
    events.state_changed(RACE)
    assert len(events.active["races"]) == 1


def test_qualifying_start_does_not_create_a_race_and_resets_lap_hits():
    events = EventManager()
    events.start_event()
    events.circle_hit("alice", "Alice")  # practice hit, must not spoil the first quali lap
    events.state_changed(QUALIFYING)
    events.race_started(0, 10)
    events.lap("alice", "Alice", 60000)
    assert events.active["races"] == []
    assert len(events.event_view(1)["qualifying"]) == 1


def test_race_circle_penalties_every_three_hits():
    events = EventManager()
    events.start_event()
    events.state_changed(RACE)
    events.race_started(3, 0)
    for _ in range(2):
        events.circle_hit("alice", "Alice")
    for _ in range(3):
        events.circle_hit("bob", "Bob")
    for _ in range(6):
        events.circle_hit("cat", "Cat")
    for _ in range(7):
        events.circle_hit("dan", "Dan")
    for name, pos in (("alice", 0), ("bob", 1), ("cat", 2), ("dan", 3)):
        result(events, name, 100000, 30000, 3, pos)
    rows = {r["username"]: r for r in events.event_view(1)["races"][0]["standings"]}
    assert [rows[u]["circle_penalties"] for u in ("alice", "bob", "cat", "dan")] == [0, 1, 2, 2]
    assert [rows[u]["circle_penalty_ms"] for u in ("alice", "bob", "cat", "dan")] == [
        0,
        5000,
        10000,
        10000,
    ]
    assert rows["cat"]["adjusted_ms"] == 110000


def test_standings_add_fastest_lap_per_lap_down_and_penalties():
    events = EventManager()
    events.start_event()
    events.state_changed(RACE)
    events.race_started(5, 0)
    result(events, "alice", 300000, 58000, 5, 0)  # winner
    result(events, "bob", 301000, 57000, 5, 1)  # fastest lap of the race
    result(events, "cat", 250000, 59000, 4, 2)  # one lap down: +57 s
    result(events, "dan", 0, 0, 2, 3)  # did not finish
    result(events, "eve", 280000, 60000, 5, 4, confirm=CONF.DISQ)
    for _ in range(3):
        events.circle_hit("alice", "Alice")  # +5 s -> 305 s, behind bob
    standings = events.event_view(1)["races"][0]["standings"]
    assert [(s["username"], s["position"], s["status"]) for s in standings] == [
        ("bob", 1, "FIN"),
        ("alice", 2, "FIN"),
        ("cat", 3, "FIN"),
        ("dan", None, "DNF"),
        ("eve", None, "DQ"),
    ]
    by = {s["username"]: s for s in standings}
    assert by["alice"]["adjusted_ms"] == 305000
    assert by["cat"]["laps_down"] == 1 and by["cat"]["lap_deficit_ms"] == 57000
    assert by["cat"]["adjusted_ms"] == 307000


def test_result_in_qualifying_is_ignored_and_not_in_table_skipped():
    events = EventManager()
    events.start_event()
    events.state_changed(QUALIFYING)
    result(events, "alice", 1, 1, 1, 0)
    events.state_changed(RACE)
    events.race_started(3, 0)
    result(events, "bob", 1, 1, 1, 255)
    assert events.active["races"][0]["results"] == {}


def test_event_started_mid_race_creates_race_lazily():
    events = EventManager()
    events.state_changed(RACE)
    events.start_event()
    result(events, "alice", 100000, 30000, 3, 0)
    assert len(events.active["races"]) == 1


def test_new_event_ends_previous_and_data_persists(tmp_path):
    path = tmp_path / "data" / "events.json"
    events = EventManager(path)
    events.start_event("One")
    events.lap("alice", "Alice", 60000)
    events.start_event("Two")
    assert [e["ended_at"] is None for e in events.events] == [False, True]

    reloaded = EventManager(path)
    assert [e["name"] for e in reloaded.events] == ["One", "Two"]
    assert reloaded.active["name"] == "Two"
    assert reloaded.event_view(1)["practice"][0]["best_ms"] == 60000
    assert json.loads(path.read_text())["events"][0]["practice"]["alice"]["best_ms"] == 60000


def test_corrupt_file_is_set_aside(tmp_path):
    path = tmp_path / "events.json"
    path.write_text("{not json")
    events = EventManager(path)
    assert events.events == []
    assert (tmp_path / "events.corrupt").exists()


def test_event_api(tmp_path):
    from fastapi.testclient import TestClient

    from lfs_tracker.settings import Settings
    from lfs_tracker.web import create_app
    from tests.test_tracker import FakeLink

    events = EventManager(tmp_path / "events.json")
    client = TestClient(create_app(Tracker(), events, Settings(), FakeLink()))
    assert client.get("/api/events").json()["events"] == []
    created = client.post("/api/events", json={"name": "Sunday"}).json()
    assert created["name"] == "Sunday" and created["active"]
    assert client.post("/api/events").json()["name"] == "Event 2"  # no body is fine
    listing = client.get("/api/events").json()["events"]
    assert [(e["id"], e["active"]) for e in listing] == [(2, True), (1, False)]
    assert client.get("/api/events/1").json()["name"] == "Sunday"
    assert client.get("/api/events/99").status_code == 404
    client.post("/api/events/end")
    assert not any(e["active"] for e in client.get("/api/events").json()["events"])


def test_packets_end_to_end():
    async def scenario():
        tracker, events = Tracker(), EventManager()
        events.start_event("Live")
        async with FakeLFS() as lfs:
            insim = InSim(port=lfs.port)
            register(insim, tracker, events)
            async with insim:
                await lfs.wait_connected()
                await lfs.send(IS_NCN(UCID=1, UName="alice", PName="^1Alice"))
                await lfs.send(IS_NPL(PLID=5, UCID=1, PName="Alice"))
                await lfs.send(IS_LAP(PLID=5, LTime=61000, LapsDone=1))  # practice
                await lfs.send(IS_STA(RaceInProg=2))
                await lfs.send(IS_LAP(PLID=5, LTime=60000, LapsDone=2))  # qualifying
                await lfs.send(IS_STA(RaceInProg=1))
                await lfs.send(IS_RST(RaceLaps=3))
                circle = IS_UCO(PLID=5, UCOAction=UCO.CIRCLE_ENTER, Info=ObjectInfo(Heading=1))
                await lfs.send(circle, circle, circle)
                await lfs.send(IS_RES(PLID=5, UName="alice", PName="Alice", TTime=90000,
                                      BTime=29000, LapsDone=3, ResultNum=0))
                await asyncio.sleep(0.3)
                view = events.event_view(1)
                assert view["practice"][0]["best_ms"] == 61000
                assert view["qualifying"][0]["best_ms"] == 60000
                [race] = view["races"]
                assert race["race_laps"] == "3"
                [row] = race["standings"]
                assert (row["circle_hits"], row["adjusted_ms"]) == (3, 95000)

    asyncio.run(scenario())
