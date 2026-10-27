"""Web front end: JSON API plus a single static page."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Literal, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from lfs_tracker.auth import AuthConfig, install_auth
from lfs_tracker.commands import practice_commands, qualify_commands, start_race_commands
from lfs_tracker.events import EventManager
from lfs_tracker.settings import WELCOME_LINE_MAX, WELCOME_LINES, Settings
from lfs_tracker.tracker import Tracker

STATIC = Path(__file__).parent / "static"
RECONNECT_WAIT_S = 1.5  # LFS drops a connection with a wrong admin password right away


class Link(Protocol):
    connected: bool
    host_info: object

    async def restart(self, *, host: str, port: int, admin_password: str) -> None: ...

    async def send_commands(self, commands: list[str]) -> None: ...


class NewEvent(BaseModel):
    name: str = ""


class SettingsUpdate(BaseModel):
    lfs_host: str | None = Field(default=None, min_length=1, max_length=255)
    lfs_port: int | None = Field(default=None, ge=1, le=65535)
    admin_password: str | None = None  # None keeps the current one, "" clears it
    qual_minutes: int | None = Field(default=None, ge=1, le=180)
    race_laps: int | None = Field(default=None, ge=1, le=99)
    grid_order: Literal["fixed", "reverse", "random"] | None = None
    vote: Literal["keep", "yes", "no"] | None = None
    can_reset: Literal["keep", "yes", "no"] | None = None
    mid_race: Literal["keep", "yes", "no"] | None = None
    welcome_lines: list[Annotated[str, Field(max_length=WELCOME_LINE_MAX)]] | None = Field(
        default=None, min_length=WELCOME_LINES, max_length=WELCOME_LINES
    )

    @field_validator("lfs_host")
    @classmethod
    def strip_host(cls, value: str | None) -> str | None:
        if value is not None and not (value := value.strip()):
            raise ValueError("host must not be blank")
        return value


# Endpoints are `async def` so they run on the event loop, the same thread as the InSim
# handlers that change the data.
def create_app(
    tracker: Tracker,
    events: EventManager,
    settings: Settings,
    link: Link,
    auth: AuthConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="LFS Tracker")
    if auth is not None:
        install_auth(app, auth)

    @app.get("/api/me")
    async def me(request: Request) -> dict:
        # None when the page is not protected by a login
        return {"user": request.scope.get("session", {}).get("user")}

    def settings_view() -> dict:
        info = link.host_info
        return {
            "lfs_host": settings.lfs_host,
            "lfs_port": settings.lfs_port,
            "admin_password_set": bool(settings.admin_password),  # never send the password itself
            "qual_minutes": settings.qual_minutes,
            "race_laps": settings.race_laps,
            "grid_order": settings.grid_order,
            "vote": settings.vote,
            "can_reset": settings.can_reset,
            "mid_race": settings.mid_race,
            "welcome_lines": settings.welcome_lines,
            "lfs_connected": link.connected,
            "role": "unknown" if not info.known else "host" if info.is_host else "guest",
            "host_name": info.name,
            "command_results": list(getattr(info, "command_results", [])),
        }

    @app.get("/api/settings")
    async def get_settings() -> dict:
        return settings_view()

    @app.post("/api/settings")
    async def update_settings(body: SettingsUpdate) -> dict:
        connection = (settings.lfs_host, settings.lfs_port, settings.admin_password)
        settings.update(**body.model_dump())
        # The race settings are used only when a button is clicked: no reconnect for them.
        if connection != (settings.lfs_host, settings.lfs_port, settings.admin_password):
            await link.restart(
                host=settings.lfs_host,
                port=settings.lfs_port,
                admin_password=settings.admin_password,
            )
            await asyncio.sleep(RECONNECT_WAIT_S)
        return settings_view()

    async def send(commands: list[str]) -> dict:
        try:
            await link.send_commands(commands)
        except ConnectionError as error:
            raise HTTPException(503, str(error)) from error
        return {"sent": commands}

    @app.post("/api/practice")
    async def practice_now() -> dict:
        return await send(practice_commands())

    @app.post("/api/qualify")
    async def qualify_now() -> dict:
        return await send(qualify_commands(settings))

    @app.post("/api/start-race")
    async def start_race_now() -> dict:
        return await send(start_race_commands(settings))

    @app.get("/api/players")
    async def players() -> dict:
        return {"lfs_connected": link.connected, **tracker.snapshot()}

    @app.get("/api/events")
    async def list_events() -> dict:
        return {"mode": events.mode, "events": events.summaries()}

    @app.post("/api/events")
    async def start_event(body: NewEvent | None = None) -> dict:
        event = events.start_event(body.name if body else "")
        return events.event_view(event["id"])

    @app.post("/api/events/end")
    async def end_event() -> dict:
        events.end_event()
        return {"mode": events.mode, "events": events.summaries()}

    @app.get("/api/events/{event_id}")
    async def get_event(event_id: int) -> dict:
        view = events.event_view(event_id)
        if view is None:
            raise HTTPException(404, "no such event")
        return view

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
