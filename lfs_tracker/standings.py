"""Final race standings: LFS total time + insim-circle penalties + lap-deficit time."""

from __future__ import annotations

from typing import Any

from lfs_insim.packets import CONF

CIRCLES_PER_PENALTY = 3
CIRCLE_PENALTY_MS = 5000


def circle_penalty_count(hits: int) -> int:
    return hits // CIRCLES_PER_PENALTY


def compute_standings(race: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one row per result, best first.

    A finisher's adjusted time is: LFS total time (which already holds LFS's own penalties)
    + 5 s per full group of 3 circle hits
    + (leader's laps - their laps) * the race's fastest lap, so lapped drivers sort after the
    drivers on the lead lap.
    """
    results = [dict(r) for r in race["results"].values()]  # copies: "status" is added below
    hits_by_user = {u: h["hits"] for u, h in race["circle_hits"].items()}

    for r in results:
        if r["confirm"] & CONF.DISQ:
            r["status"] = "DQ"
        elif r["total_ms"] <= 0:
            r["status"] = "DNF"
        else:
            r["status"] = "FIN"

    finishers = [r for r in results if r["status"] == "FIN"]
    leader = next((r for r in finishers if r["position"] == 0), None)
    leader_laps = leader["laps"] if leader else max((r["laps"] for r in finishers), default=0)
    best_laps = [r["best_ms"] for r in finishers if r["best_ms"] > 0]
    fastest_lap = min(best_laps, default=0)

    rows: list[dict[str, Any]] = []
    for r in results:
        hits = hits_by_user.get(r["username"], 0)
        penalties = circle_penalty_count(hits)
        row = {
            "username": r["username"],
            "nickname": r["nickname"],
            "car": r["car"],
            "status": r["status"],
            "laps": r["laps"],
            "total_ms": r["total_ms"],
            "best_ms": r["best_ms"],
            "lfs_penalty_s": r["lfs_penalty_s"],
            "circle_hits": hits,
            "circle_penalties": penalties,
            "circle_penalty_ms": penalties * CIRCLE_PENALTY_MS,
            "laps_down": 0,
            "lap_deficit_ms": 0,
            "adjusted_ms": None,
            "position": None,
        }
        if r["status"] == "FIN":
            laps_down = max(0, leader_laps - r["laps"])
            row["laps_down"] = laps_down
            row["lap_deficit_ms"] = laps_down * fastest_lap
            row["adjusted_ms"] = (
                r["total_ms"] + row["lap_deficit_ms"] + row["circle_penalty_ms"]
            )
        rows.append(row)

    order = {"FIN": 0, "DNF": 1, "DQ": 2}
    rows.sort(
        key=lambda r: (
            order[r["status"]],
            r["adjusted_ms"] if r["adjusted_ms"] is not None else -r["laps"],
            r["username"],
        )
    )
    position = 0
    for row in rows:
        if row["status"] == "FIN":
            position += 1
            row["position"] = position
    return rows
