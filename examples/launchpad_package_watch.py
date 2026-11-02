#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watchdog example: has release X shipped a new version of package Y yet?

Uses the Launchpad API instead of the CVE page. ubuntu.com/security/CVE-... shows
"work in progress in <version>" for months, and the version named there is the
target, not a download. Launchpad reports what is actually published, per pocket.

Only the pockets you name count. `Proposed` is the test repo and must not alarm —
that is exactly how a watch stays quiet during the days between proposal and
release.

Usage:
  python3 launchpad_package_watch.py --series noble --source linux
  python3 launchpad_package_watch.py --series noble --source linux --pockets Security,Updates
  python3 launchpad_package_watch.py --status
  python3 launchpad_package_watch.py --reset-baseline
  python3 launchpad_package_watch.py --selftest
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

SLUG = "launchpad-package"
DEFAULT_SERIES = "noble"
DEFAULT_SOURCE = "linux"
DEFAULT_POCKETS = ("Security", "Updates")

# Pockets that mean "this is really out". Everything else is a staging area.
ALERTING_POCKETS = {"Security", "Updates"}

ARGS = sys.argv[1:]


def arg(name, default=None):
    if name in ARGS:
        i = ARGS.index(name)
        if i + 1 < len(ARGS):
            return ARGS[i + 1]
    return default


SERIES = arg("--series", DEFAULT_SERIES)
SOURCE = arg("--source", DEFAULT_SOURCE)
POCKETS = tuple(p.strip() for p in arg("--pockets", ",".join(DEFAULT_POCKETS)).split(",") if p.strip())

for _p in POCKETS:
    if _p not in ALERTING_POCKETS:
        print(f"refusing: pocket {_p!r} is not an alerting pocket "
              f"(known: {', '.join(sorted(ALERTING_POCKETS))}). Proposed/Build are "
              f"staging and would alarm on things that are not released yet.",
              file=sys.stderr)
        sys.exit(0)

STATE_DIR = (arg("--state-dir") or os.environ.get("WATCHDOG_STATE_DIR")
             or os.path.expanduser(f"~/.local/state/watchdog/{SLUG}"))
STATE_FILE = os.path.join(STATE_DIR, "state.json")
TEST_MARKER = os.path.join(STATE_DIR, ".test")

API = ("https://api.launchpad.net/1.0/ubuntu/+archive/primary"
       "?ws.op=getPublishedSources&source_name=" + urllib.parse.quote(SOURCE) +
       "&distro_series=" + urllib.parse.quote(f"/ubuntu/{SERIES}") +
       "&status=Published&exact_match=true")


def vkey(v):
    """'6.8.0-149.149' -> (6, 8, 0, 149, 149). None if unparseable."""
    try:
        main, _, abi = v.partition("-")
        parts = [int(x) for x in main.split(".")] + [int(abi.split(".")[0])]
        if len(parts) < 5:
            parts += [0] * (5 - len(parts))
        return tuple(parts[:5])
    except (ValueError, IndexError):
        return None


def fetch_versions():
    """-> [(vkey, version, pocket, date)] for the watched pockets."""
    req = urllib.request.Request(API, headers={"User-Agent": "watchdog/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.load(r)
    out = []
    for e in data.get("entries", []):
        if e.get("pocket") not in POCKETS or e.get("status") != "Published":
            continue
        v = e.get("source_package_version", "").split("~")[0]
        k = vkey(v)
        if k is None:
            continue
        out.append((k, v, e["pocket"], (e.get("date_published") or "")[:10]))
    return out


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                     # noqa: BLE001
        return {}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def report(newest, is_kernel):
    _, v, pocket, date = newest
    lines = [f"New {SOURCE} for {SERIES}: **{v}** ({pocket}, {date})."]
    if is_kernel:
        lines += [
            "",
            "On the machine itself:",
            "```",
            "sudo apt update && sudo apt full-upgrade -y",
            "sudo reboot",
            "```",
            "Check `uname -r` after the reboot — it has to show the new version.",
            "A container patches nothing kernel-related; the kernel comes from the host.",
        ]
    else:
        lines += ["", f"Upgrade with `apt install {SOURCE}` after `apt update`."]
    return "\n".join(lines)


def main():
    state = load_state()

    if "--reset-baseline" in ARGS:
        state.pop("baseline", None)
        state.pop("alerted", None)
        save_state(state)

    if "--selftest" in ARGS:
        # Exercise the alert path with a made-up version. No state write.
        fake = ((99, 99, 99, 99, 99), "99.99.99-99.99", "Security", "2099-01-01")
        print("SELFTEST (not a real alert):\n" + report(fake, "linux" in SOURCE))
        return 0

    try:
        versions = fetch_versions()
    except Exception:                    # network/API failure: stay silent, keep state
        print("", end="")
        return 0

    if not versions:
        print("", end="")
        return 0

    newest = max(versions, key=lambda x: x[0])
    k_now, vstr, pocket, date = newest

    if "--status" in ARGS:
        print(f"highest publication for {SERIES} {SOURCE} in {', '.join(POCKETS)}: "
              f"{vstr} ({pocket}, {date})")
        print(f"baseline in state: {state.get('baseline', '(none - first run seeds)')}")
        print(f"already alerted: {state.get('alerted', '(nothing yet)')}")
        return 0

    if "baseline" not in state:           # first run: seed only, stay quiet
        state["baseline"] = vstr
        save_state(state)
        print("", end="")
        return 0

    lines = []
    if k_now > vkey(state["baseline"]) and state.get("alerted") != vstr:
        lines = report(newest, "linux" in SOURCE).split("\n")
        state["alerted"] = vstr
        state["baseline"] = vstr
    else:
        cur = vkey(state["baseline"]) or (0,)
        state["baseline"] = max(vstr, state["baseline"],
                                key=lambda s: vkey(s) or (0,)) if k_now > cur else state["baseline"]

    save_state(state)

    test = os.path.exists(TEST_MARKER)    # marker BEFORE the silence gate
    if test:
        lines.insert(0, "Watchdog test run - delivery works.")
        if len(lines) == 1:
            lines.append(f"(nothing new. Published right now: {vstr})")
        try:
            os.remove(TEST_MARKER)
        except OSError:
            pass

    print("\n".join(lines) if lines else "", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
