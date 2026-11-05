# Armed desktop shell

A thin Electron shell (`main.js`, `preload.js`) that floats the Armed card page over
whatever you are working on. It never takes the keyboard: the window is
non-focusable, click-through except over STOP and card buttons, and shown with
`showInactive()`. It adds a menu-bar icon whose dot follows the arm state. The page
(`/card`) owns every state; this shell owns the window and the runtime process.
See `docs/UI.md` sections 2 and 7.

## Run

```bash
cd desktop && npm install && npm start
```

`npm start -- --corner br --armed-args "--emg synthetic"` passes flags through.

If nothing answers `GET /api/state` on the port, the shell starts `armed run --port <port>`
(from `.venv/bin/armed` if present, else `armed` on PATH) and waits up to 30 s, with a
dialog if it never comes up. If the runtime is already running it attaches and never
ends it. If the child it started crashes it restarts it after 2 s, and gives up with a
dialog after 3 failures in a row.

| Flag | Env | Default |
|---|---|---|
| `--port N` | `ARMED_PORT` | `server.port` in `configs/armed.yaml` (8080) |
| `--corner tr\|tl\|br\|bl` | | `tr` (primary display, 16 px margin) |
| `--film` | | off; keeps the card on screen with an idle panel between cards (for filming) |
| `--armed-args "..."` | `ARMED_ARGS` | none; extra args after `armed run` (do not repeat `--port`) |
| | `ARMED_CMD` | the armed executable to spawn |

The window loads `http://127.0.0.1:<port>/card`, or `/web/card/index.html` when
`/card` answers 404 (before the runtime route exists). Navigation away from that
origin and `window.open` are blocked; the console opens in the system browser.

## Tray

The dot is green for ready/moving, grey for disarmed, red for stopped/fault, amber
when the runtime is unreachable. Menu: Arm / Disarm (after a stop it reads "Reset
(then Arm)" and only resets, so re-arming stays a separate deliberate click), Stop,
Open console, Quit. Arm, Disarm and Reset POSTs carry the session token read from
`<meta name="armed-token">` on `/`; Stop needs none. **Quit stops the arm first**
(`POST /api/stop`), then ends only a runtime this shell started.

## What the page can call

`preload.js` exposes `window.armedHost` with `setVisible(bool)`, `setInteractive(bool)`,
`stop()` and `openConsole()`. The page should call `setInteractive(true)` only while
the pointer is over STOP or a card button.

## If the shell is not ready for a take

Open `http://127.0.0.1:<port>/card?mode=far` full-screen on a second display beside
the arm. It is the same page and shoots the same way.

## `armed desktop` (for the runtime owner to paste into `armed/cli.py`)

```python
def cmd_desktop(args: argparse.Namespace) -> int:
    import shutil
    import subprocess

    npx = shutil.which("npx")
    if npx is None:
        print("Node.js is required: install it, then run `cd desktop && npm install`.")
        return 2
    shell = ROOT / "desktop"
    if not (shell / "node_modules").exists():
        print("Run `cd desktop && npm install` once first.")
        return 2
    cmd = [npx, "electron", str(shell), *(["--armed-args", args.armed_args] if args.armed_args else [])]
    if args.corner:
        cmd += ["--corner", args.corner]
    return subprocess.call(cmd, cwd=ROOT)

# in main(), next to the other sub-parsers:
    desk = sub.add_parser("desktop", help="floating card window and tray icon (Electron)")
    desk.add_argument("--corner", choices=["tr", "tl", "br", "bl"])
    desk.add_argument("--armed-args", help='extra args for "armed run", e.g. "--emg synthetic"')
    desk.set_defaults(func=cmd_desktop)
```

## Later

Tauri or a native `NSPanel` can replace this shell with no change to the page: the
contract is only `/card`, the four `armedHost` calls and the runtime API.
