"""Connects LFS InSim packets to a Tracker and an EventManager."""

from __future__ import annotations

from dataclasses import dataclass

from lfs_insim import InSim, received
from lfs_insim.packets import TINY, UCO
from lfs_insim.text import plain

from lfs_tracker.events import RACE, EventManager, Violation
from lfs_tracker.standings import CIRCLE_PENALTY_MS, CIRCLES_PER_PENALTY
from lfs_tracker.tracker import Tracker

PTYPE_AI = 2
RED = "^1"


@dataclass
class HostInfo:
    known: bool = False  # IS_ISM only arrives in multiplayer
    is_host: bool = False
    name: str = ""


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
    insim: InSim, tracker: Tracker, events: EventManager, host_info: HostInfo | None = None
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

    @insim.on.IS_ISM
    def multiplayer(packet: received.IS_ISM) -> None:
        host_info.known = True
        host_info.is_host = bool(packet.Host)
        host_info.name = plain(packet.HName)

    @insim.on.IS_NCN
    def new_connection(packet: received.IS_NCN) -> None:
        tracker.player_connected(packet.UCID, packet.UName, plain(packet.PName))

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
