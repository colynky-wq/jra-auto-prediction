"""Opt-in macOS launchd plist generator for local KeibaAI odds research bridge.

Default invocation is a dry run. --write only writes a LaunchAgent plist;
it does NOT load/enable it or fetch odds. The user must install an authorized
local data exporter and explicitly activate the agent on their own Mac.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import plistlib
import sys

LABEL = "local.keibaai.independent-odds-research"
PLIST_NAME = LABEL + ".plist"


def render_agent(repo_root, python_exec, home_dir, *, poll_seconds=30):
    root = Path(repo_root).expanduser().resolve()
    exe = Path(python_exec).expanduser().resolve()
    home = Path(home_dir).expanduser().resolve()
    script = root / "production" / "independent_odds_bridge.py"
    if not script.is_file():
        raise ValueError("KeibaAI bridge script not found under repository root")
    if not exe.is_file():
        raise ValueError("Python interpreter not found")
    if not isinstance(poll_seconds,int) or not 10 <= poll_seconds <= 3600:
        raise ValueError("poll_seconds must be between 10 and 3600")
    logs = home / "Library" / "Logs" / "KeibaAI"
    config = {
        "Label": LABEL,
        "ProgramArguments": [
            str(exe), str(script), "--root", str(root),
            "--watch", "--poll-seconds", str(poll_seconds),
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "odds_bridge.log"),
        "StandardErrorPath": str(logs / "odds_bridge.error.log"),
        "ThrottleInterval": 20,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    return plistlib.dumps(config, fmt=plistlib.FMT_XML, sort_keys=True)


def write_agent(repo_root, python_exec, home_dir, *, poll_seconds=30,
                overwrite=False):
    home = Path(home_dir).expanduser().resolve()
    plist = home / "Library" / "LaunchAgents" / PLIST_NAME
    if plist.exists() and not overwrite:
        raise FileExistsError("LaunchAgent already exists; use --replace explicitly")
    content = render_agent(repo_root, python_exec, home, poll_seconds=poll_seconds)
    plist.parent.mkdir(parents=True, exist_ok=True)
    (home / "Library" / "Logs" / "KeibaAI").mkdir(parents=True, exist_ok=True)
    # Only opt-in --write creates the plist. Use the macOS launchctl
    # commands printed by main to enable the agent; no hidden installation.
    if not overwrite:
        with plist.open("xb") as f:
            f.write(content)
    else:
        plist.write_bytes(content)
    plist.chmod(0o600)
    return plist


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--write", action="store_true",
                        help="write the plist to ~/Library/LaunchAgents")
    parser.add_argument("--replace", action="store_true",
                        help="permit replacing an existing plist (not loaded agent)")
    args = parser.parse_args()
    if sys.version_info < (3,11):
        parser.error("Python 3.11+ required")
    try:
        content = render_agent(args.repo_root,args.python,args.home,
                               poll_seconds=args.poll_seconds)
        target = (args.home.expanduser().resolve() / "Library" /
                  "LaunchAgents" / PLIST_NAME)
        if args.write:
            if sys.platform != "darwin":
                parser.error("--write is allowed only on macOS")
            target=write_agent(args.repo_root,args.python,args.home,
                               poll_seconds=args.poll_seconds,
                               overwrite=args.replace)
            print("Created:",target)
            print("No background agent started. To enable explicitly:")
            print(f"launchctl bootstrap gui/$(id -u) '{target}'")
            print("To stop:")
            print(f"launchctl bootout gui/$(id -u) '{target}'")
        else:
            print("DRY RUN ONLY; no file written and no agent started")
            print("Target:",target)
            print("Python:",args.python.expanduser().resolve())
            print("Plist bytes:",len(content))
            print("To create the plist on your Mac, use --write.")
    except (ValueError, OSError) as err:
        parser.error(str(err))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
