"""The LFS commands behind the QUALIFY NOW and START RACE NOW buttons."""

from __future__ import annotations

from lfs_tracker.settings import Settings


def option_commands(settings: Settings) -> list[str]:
    """Yes/no host options the user chose; "keep" ones are not sent."""
    options = (
        ("/vote", settings.vote),
        ("/canreset", settings.can_reset),
        ("/midrace", settings.mid_race),
    )
    return [f"{command} {value}" for command, value in options if value != "keep"]


def practice_commands() -> list[str]:
    # 0 laps is practice; resets are always allowed there
    return ["/laps=0", "/canreset yes", "/restart"]


def qualify_commands(settings: Settings) -> list[str]:
    return [
        f"/laps={settings.race_laps}",
        f"/qual {settings.qual_minutes}",
        *option_commands(settings),
        "/qualify",
    ]


def start_race_commands(settings: Settings) -> list[str]:
    # The grid order has to be set before /restart.
    return [
        f"/laps={settings.race_laps}",
        f"/start {settings.grid_order}",
        *option_commands(settings),
        "/restart",
    ]
