"""Connects LFS InSim packets to a Tracker and an EventManager."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from lfs_insim import InSim, received
from lfs_insim.packets import BFN, INST, ISB, TINY, UCO
from lfs_insim.text import plain

from lfs_tracker.events import RACE, EventManager, Violation
from lfs_tracker.settings import WELCOME_LINES, Settings
from lfs_tracker.standings import CIRCLE_PENALTY_MS, CIRCLES_PER_PENALTY
from lfs_tracker.tracker import Tracker

PTYPE_AI = 2
RED = "^1"

# Welcome board layout (InSim button coordinates, 0-200)
BOARD_L, BOARD_T, BOARD_W, BOARD_H = 34, 30, 132, 120
LINE_H = 9
LINES_T = BOARD_T + 6
CLOSE_W, CLOSE_H = 36, 12
BACKGROUND_IDS = (0, 1)  # two dark buttons stacked, so the text is readable on any scene
FIRST_LINE_ID = 2
CLOSE_ID = FIRST_LINE_ID + WELCOME_LINES
BOARD_REQI = 200  # ReqI of the board's buttons, to tell clicks on them from other InSim apps'


def welcome_buttons(ucid: int, lines: list[str]) -> list[dict]:
    """The IS_BTN fields that make up the welcome board for one connection."""
    common = {"ReqI": BOARD_REQI, "UCID": ucid, "Inst": INST.ALWAYS_ON}  # visible in every LFS screen
    buttons = [
        {**common, "ClickID": click_id, "BStyle": ISB.DARK, "L": BOARD_L, "T": BOARD_T,
         "W": BOARD_W, "H": BOARD_H}
        for click_id in BACKGROUND_IDS
    ]
    for i, line in enumerate(lines[:WELCOME_LINES]):
        if line.strip():
            buttons.append(
                {**common, "ClickID": FIRST_LINE_ID + i, "BStyle": ISB.LEFT, "L": BOARD_L + 2,
                 "T": LINES_T + i * LINE_H, "W": BOARD_W - 4, "H": LINE_H, "Text": line}
            )
    buttons.append(
        {**common, "ClickID": CLOSE_ID, "BStyle": ISB.CLICK | ISB.LIGHT,
         "L": BOARD_L + (BOARD_W - CLOSE_W) // 2, "T": BOARD_T + BOARD_H - CLOSE_H - 4,
         "W": CLOSE_W, "H": CLOSE_H, "Text": "Close"}
    )
    return buttons


@dataclass
class HostInfo:
    known: bool = False  # IS_ISM only arrives in multiplayer
    is_host: bool = False
    name: str = ""
    # LFS's reports (IS_ACR) on commands we sent; only the host sends them
    command_results: deque[dict] = field(default_factory=lambda: deque(maxlen=10))


def violation_message(violation: Violation) -> str:
    if violation.mode != RACE:
        return f"{RED}Track violation occurred - lap time will not be recorded"
    more = CIRCLES_PER_PENALTY - violation.race_hits % CIRCLES_PER_PENALTY
    if more == CIRCLES_PER_PENALTY:
        return (
            f"{RED}Track cut - {CIRCLE_PENALTY_MS // 1000} second penalty added, "
            f"{more} more = another penalty"
        )
    return f"{RED}Track cut - {more} more = {CIRCLE_PENALTY_MS // 1000} second penalty"


def register(
    insim: InSim,
    tracker: Tracker,
    events: EventManager,
    host_info: HostInfo | None = None,
    settings: Settings | None = None,
) -> None:
    host_info = host_info or HostInfo()

    @insim.on_connect
    def sync() -> None:
        # LFS may have restarted, and it replays NCN/NPL for everyone already present.
        tracker.clear()
        insim.send.IS_TINY(ReqI=1, SubT=TINY.NCN)
        insim.send.IS_TINY(ReqI=1, SubT=TINY.NPL)
        insim.send.IS_TINY(ReqI=1, SubT=TINY.SST)  # answered with IS_STA
        insim.send.IS_TINY(ReqI=1, SubT=TINY.RST)  # answered with IS_RST: the session's laps
        insim.send.IS_TINY(ReqI=1, SubT=TINY.ISM)

    @insim.on.IS_ACR
    def command_report(packet: received.IS_ACR) -> None:
        if packet.UCID != 0:  # only our own commands run as the host
            return
        outcome = {1: "accepted", 2: "rejected", 3: "unknown command"}.get(packet.Result, "?")
        host_info.command_results.append({"text": plain(packet.Text), "result": outcome})

    @insim.on.IS_ISM
    def multiplayer(packet: received.IS_ISM) -> None:
        host_info.known = True
        host_info.is_host = bool(packet.Host)
        host_info.name = plain(packet.HName)

    @insim.on.IS_NCN
    def new_connection(packet: received.IS_NCN) -> None:
        tracker.player_connected(packet.UCID, packet.UName, plain(packet.PName))
        # ReqI != 0 is the replay of whoever was already there; UCID 0 is the host itself.
        if settings is not None and packet.ReqI == 0 and packet.UCID != 0 and settings.welcome_enabled:
            for button in welcome_buttons(packet.UCID, settings.welcome_lines):
                insim.send.IS_BTN(**button)

    @insim.on.IS_BTC
    def board_clicked(packet: received.IS_BTC) -> None:
        if packet.ReqI == BOARD_REQI and packet.ClickID == CLOSE_ID:
            insim.send.IS_BFN(SubT=BFN.DEL_BTN, UCID=packet.UCID, ClickID=BACKGROUND_IDS[0], ClickMax=CLOSE_ID)

    @insim.on.IS_CPR
    def renamed(packet: received.IS_CPR) -> None:
        tracker.player_renamed(packet.UCID, plain(packet.PName))

    @insim.on.IS_CNL
    def connection_left(packet: received.IS_CNL) -> None:
        tracker.player_left(packet.UCID)

    @insim.on.IS_NPL
    def joined_track(packet: received.IS_NPL) -> None:
        if packet.PType & PTYPE_AI:
            return
        tracker.joined_track(packet.PLID, packet.UCID)
        if (player := tracker.player_for_plid(packet.PLID)) is not None:
            events.player_on_track(player.username)

    @insim.on.IS_PLP
    def to_garage(packet: received.IS_PLP) -> None:
        tracker.left_track(packet.PLID)

    @insim.on.IS_PLL
    def left_race(packet: received.IS_PLL) -> None:
        tracker.left_track(packet.PLID)

    @insim.on.IS_UCO
    def circle(packet: received.IS_UCO) -> None:
        if packet.UCOAction != UCO.CIRCLE_ENTER:
            return
        info = packet.Info
        entry = tracker.circle_entered(
            packet.PLID,
            packet.Time,
            info.Heading,  # for a circle, Heading holds the circle's index
            info.X / 16,
            info.Y / 16,
        )
        if entry is not None and (player := tracker.player_for_plid(packet.PLID)) is not None:
            violation = events.circle_hit(entry.username, player.nickname)
            if violation is not None:
                insim.send.IS_MTC(UCID=player.ucid, Text=violation_message(violation))

    @insim.on.IS_STA
    def state(packet: received.IS_STA) -> None:
        events.state_changed(packet.RaceInProg, packet.RaceLaps)

    @insim.on.IS_RST
    def race_start(packet: received.IS_RST) -> None:
        events.race_started(packet.RaceLaps, packet.QualMins, requested=bool(packet.ReqI))

    @insim.on.IS_LAP
    def lap(packet: received.IS_LAP) -> None:
        if (player := tracker.player_for_plid(packet.PLID)) is not None:
            events.lap(player.username, player.nickname, packet.LTime)

    @insim.on.IS_RES
    def result(packet: received.IS_RES) -> None:
        events.result(
            username=packet.UName,
            nickname=plain(packet.PName),
            car=packet.CName,
            total_ms=packet.TTime,
            best_ms=packet.BTime,
            laps=packet.LapsDone,
            position=packet.ResultNum,
            confirm=packet.Confirm,
            lfs_penalty_s=packet.PSeconds,
            stops=packet.NumStops,
        )
