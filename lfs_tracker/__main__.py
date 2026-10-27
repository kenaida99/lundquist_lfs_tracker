"""Run the InSim client and the web server together in one event loop.

    python -m lfs_tracker [--lfs-host 127.0.0.1] [--lfs-port 29999] [--admin-password PW]
                          [--web-host 127.0.0.1] [--web-port 8000] [--data-file PATH]
                          [--public-url URL]   (web login: WEB_USERNAME and WEB_PASSWORD
                                                in the environment)

In LFS, type /insim=29999 first (or start the host with InSim enabled).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

import uvicorn

from lfs_tracker.auth import MIN_PASSWORD_LENGTH, AuthConfig, load_session_secret
from lfs_tracker.connection import InSimLink
from lfs_tracker.events import EventManager
from lfs_tracker.settings import Settings
from lfs_tracker.tracker import Tracker
from lfs_tracker.web import create_app

DEFAULT_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "events.json"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

log = logging.getLogger(__name__)


def build_auth(args: argparse.Namespace, data_dir: Path) -> AuthConfig | None:
    """Login settings, or None to serve the page without a login (this computer only)."""
    if not (args.web_username or args.web_password):
        if args.web_host not in LOOPBACK_HOSTS:
            raise SystemExit(
                "Refusing to serve the page to the network without a login. Set WEB_USERNAME and "
                "WEB_PASSWORD (see the README), or use --web-host 127.0.0.1."
            )
        log.warning("no web login configured: the page is open to anyone on this computer")
        return None
    if not (args.web_username and args.web_password):
        raise SystemExit("The web login needs both WEB_USERNAME and WEB_PASSWORD.")
    if len(args.web_password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"WEB_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters.")
    return AuthConfig(
        username=args.web_username,
        password=args.web_password,
        session_secret=load_session_secret(data_dir / "session_secret"),
        secure_cookies=args.public_url.lower().startswith("https://"),
    )


async def serve(args: argparse.Namespace) -> None:
    data_file = Path(args.data_file)
    auth = build_auth(args, data_file.parent)
    tracker = Tracker()
    events = EventManager(data_file)
    settings = Settings(data_file.parent / "settings.json")
    # Command-line values win until they are changed on the web page.
    if args.admin_password:
        settings.admin_password = args.admin_password
    if args.lfs_host:
        settings.lfs_host = args.lfs_host
    if args.lfs_port:
        settings.lfs_port = args.lfs_port
    link = InSimLink(
        tracker,
        events,
        host=settings.lfs_host,
        port=settings.lfs_port,
        admin_password=settings.admin_password,
        settings=settings,
    )
    app = create_app(tracker, events, settings, link, auth)
    server = uvicorn.Server(uvicorn.Config(app, host=args.web_host, port=args.web_port))
    await link.start()
    try:
        await server.serve()  # returns on Ctrl+C
    finally:
        await link.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Track LFS players via InSim and show them on the web.")
    parser.add_argument("--lfs-host", help="LFS / host address (default: saved setting)")
    parser.add_argument("--lfs-port", type=int, help="InSim port (default: saved setting)")
    parser.add_argument("--admin-password", default="")
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8000)
    parser.add_argument("--data-file", default=str(DEFAULT_DATA_FILE), help="race events JSON file")
    env = os.environ.get
    parser.add_argument("--web-username", default=env("WEB_USERNAME", ""), help="or env WEB_USERNAME")
    parser.add_argument("--web-password", default=env("WEB_PASSWORD", ""), help="or env WEB_PASSWORD (preferred)")
    parser.add_argument(
        "--public-url", default=env("PUBLIC_URL", ""), help="https://... when served over https (makes the cookie secure), or env PUBLIC_URL"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
