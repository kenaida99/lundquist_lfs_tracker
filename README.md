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

## Rules

- **Practice / qualifying:** the fastest lap per driver is kept. A lap in which the driver
  enters an insim circle is not recorded.
- **Race:** every 3 circle hits add 5 s. Final time = LFS total time + circle penalties + for
  each lap behind the leader, the race's fastest lap time.

## Tests

```
.venv\Scripts\python -m pytest
```
