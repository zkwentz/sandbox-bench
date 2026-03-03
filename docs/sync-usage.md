# DevVM Sync Script

Syncs `~/Development/sandbox-bench` to/from your devvm (set `DEVVM_HOST` in `.env.meta`) using SSH multiplexing — **one Duo auth**, reused for all subsequent commands for up to 1 hour.

## Commands

| Command | What it does |
|---|---|
| `./sync.sh push` | Overwrites the devvm with your entire local project (rsync `--delete`) |
| `./sync.sh pull` | Brings back only changed files from the devvm to local (no `--delete`, so local-only files are safe) |
| `./sync.sh ssh` | Opens a shell on the devvm, cd'd into the project |
| `./sync.sh connect` | Pre-warms the SSH connection without doing anything else |
| `./sync.sh disconnect` | Tears down the persistent connection |
| `./sync.sh status` | Checks if a connection is active |

## Typical Workflow

```bash
./sync.sh push          # upload everything to devvm (1 Duo auth)
./sync.sh ssh           # work on devvm — no extra auth needed
# ... do your work ...
# exit the shell
./sync.sh pull          # bring back changes — still no auth
./sync.sh disconnect    # clean up when done
```

## Details

- **Push** uses `--delete` so the remote exactly mirrors local.
- **Pull** omits `--delete` so it only adds/updates changed files without removing anything local-only.
- Both exclude `.git/`, `__pycache__/`, `.env`, `venv/`, and `.claude/`.
- The persistent connection stays alive for 1 hour (`ControlPersist=3600`) with keepalives every 60 seconds.
- Socket files are stored in `~/.ssh/sockets/`.
