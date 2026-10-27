# LFS Tracker

Connects to Live for Speed over InSim (using [`lfs-insim`](https://pypi.org/project/lfs-insim/))
and serves a web page showing:

- who is connected, who is on track and who is in the garage (with UCID / PLID);
- insim circle entries;
- race **events**: practice fastest laps, a qualifying leaderboard and any number of races, with
  circle-hit penalties and lap-down adjustments in the final standings.

Drivers get a red in-game message (`IS_MTC`) when they hit an insim circle. This only works
when connected to the **host's** InSim, not as a guest.

## Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m lfs_tracker
```

Open http://127.0.0.1:8000. In LFS (or the dedicated host) enable InSim, e.g. `/insim=29999`.
The host address, port and admin password can be changed from the **Settings** button.

Options: `--lfs-host`, `--lfs-port`, `--admin-password`, `--web-host`, `--web-port`,
`--data-file`. Race results and settings are stored in `data/` (not committed).

## Web login

Without a login the page is open to anyone who can reach it, so the tracker only starts on
`127.0.0.1`; with any other `--web-host` it refuses to start. To protect the page set these
environment variables (e.g. in a systemd `EnvironmentFile`) and start the tracker:

| Variable | Meaning |
| --- | --- |
| `WEB_USERNAME`, `WEB_PASSWORD` | the one account that may sign in (password: 8+ characters) |
| `PUBLIC_URL` | set to `https://...` when served over https, so the cookie is marked secure |
| `SESSION_SECRET` | optional; otherwise one is generated and kept in `data/session_secret` |

Everything (page and API) then needs a sign-in. Sessions last 7 days and end when the username or
password changes. After 5 wrong passwords an address is locked out for 5 minutes. Serve it over
HTTPS (e.g. a Caddy reverse proxy to `127.0.0.1:8000`), as the password is otherwise sent in
clear text.

## Race control

The page has **SET PRACTICE MODE**, **QUALIFY NOW** and **START RACE NOW** buttons (each asks for confirmation). The
values come from ⚙ Settings (qualifying minutes, race laps, grid order: fixed / reverse /
random) and are sent as LFS commands:

- SET PRACTICE MODE: `/laps=0`, `/canreset yes`, `/restart`
- QUALIFY NOW: `/laps=<laps>`, `/qual <minutes>`, the yes/no options below, `/qualify`
  (commands are sent 200 ms apart)
- START RACE NOW: `/laps=<laps>`, `/start <fixed|reverse|random>`, the yes/no options, `/restart`

Yes/no options (each can also be "Don't change", the default, which sends nothing):
`/vote` (everyone can vote to restart), `/canreset` and `/midrace` (cars may join after the
race has started).

Commands only work when connected to the host's InSim (or as an admin).

## Rules

- **Practice / qualifying:** the fastest lap per driver is kept. A lap in which the driver
  enters an insim circle is not recorded.
- **Race:** every 3 circle hits add 5 s. Final time = LFS total time + circle penalties + for
  each lap behind the leader, the race's fastest lap time.

## Tests

```
.venv\Scripts\python -m pytest
```
