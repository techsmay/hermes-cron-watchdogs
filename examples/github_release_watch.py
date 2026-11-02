#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watchdog example: a new release for a GitHub repo.

Uses the GitHub REST API (no scraping, no token needed for public repos — the
unauthenticated limit is 60 requests/hour, plenty for a few ticks a day). Set
GITHUB_TOKEN if you watch many repos or need a higher limit.

Alerts once per new tag, not once per tick: the tag id from the last alert is
stored, so a flapping or re-published release does not repeat.

Usage:
  python3 github_release_watch.py --repo psf/requests
  python3 github_release_watch.py --repo psf/requests --include-prerelease
  python3 github_release_watch.py --status
  python3 github_release_watch.py --reset-baseline
  python3 github_release_watch.py --selftest
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

SLUG = "github-release"

ARGS = sys.argv[1:]


def arg(name, default=None):
    if name in ARGS:
        i = ARGS.index(name)
        if i + 1 < len(ARGS):
            return ARGS[i + 1]
    return default


REPO = arg("--repo")
if not REPO and "--selftest" not in ARGS:
    print("usage: github_release_watch.py --repo owner/name [--include-prerelease]",
          file=sys.stderr)
    sys.exit(0)

INCLUDE_PRE = "--include-prerelease" in ARGS
SLUG = (REPO or "selftest").replace("/", "-")

STATE_DIR = (arg("--state-dir") or os.environ.get("WATCHDOG_STATE_DIR")
             or os.path.expanduser(f"~/.local/state/watchdog/github-{SLUG}"))
STATE_FILE = os.path.join(STATE_DIR, "state.json")
TEST_MARKER = os.path.join(STATE_DIR, ".test")


def latest_release():
    """-> {'id','tag','name','url','published_at'} or None."""
    url = f"https://api.github.com/repos/{REPO}/releases"
    headers = {"User-Agent": "watchdog/1.0", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # 404 = repo gone or renamed, 403 = rate limited. Either way: stay quiet.
        print(f"# fetch failed: HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:                                # noqa: BLE001
        print(f"# fetch failed: {e}", file=sys.stderr)
        return None

    for rel in data if isinstance(data, list) else []:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not INCLUDE_PRE:
            continue
        return {
            "id": str(rel.get("id") or rel.get("tag_name") or ""),
            "tag": rel.get("tag_name") or "?",
            "name": rel.get("name") or rel.get("tag_name") or "?",
            "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
            "published_at": (rel.get("published_at") or "")[:10],
        }
    return None


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


def report(rel):
    return (f"New release for {REPO or 'owner/name'}: **{rel['tag']}**"
            + (f" — {rel['name']}" if rel["name"] != rel["tag"] else "")
            + f" ({rel['published_at']})\n{rel['url']}")


def main():
    state = load_state()

    if "--reset-baseline" in ARGS:
        for key in ("last_id", "last_tag"):
            state.pop(key, None)
        save_state(state)

    if "--selftest" in ARGS:
        fake = {"id": "0", "tag": "v99.99.99", "name": "synthetic release",
                "url": "https://example.invalid/release", "published_at": "2099-01-01"}
        print("SELFTEST (not a real alert):\n" + report(fake))
        return 0

    rel = latest_release()
    if rel is None:                       # failed fetch: stay silent, keep state
        print("", end="")
        return 0

    if "--status" in ARGS:
        print(f"repo: {REPO}\nlatest: {rel['tag']} ({rel['published_at']})\n"
              f"last alerted: {state.get('last_tag', '(nothing yet)')}\n"
              f"baseline: {'seeded' if state.get('last_id') else '(not seeded)'}")
        return 0

    if "last_id" not in state:            # first run: seed only, stay quiet
        state.update({"last_id": rel["id"], "last_tag": rel["tag"]})
        save_state(state)
        print("", end="")
        return 0

    lines = []
    if rel["id"] != state["last_id"]:
        lines = report(rel).split("\n")
        state.update({"last_id": rel["id"], "last_tag": rel["tag"]})
    save_state(state)

    if os.path.exists(TEST_MARKER):       # marker BEFORE the silence gate
        lines.insert(0, "Watchdog test run - delivery works.")
        if len(lines) == 1:
            lines.append(f"(nothing new. Latest right now: {rel['tag']})")
        try:
            os.remove(TEST_MARKER)
        except OSError:
            pass

    print("\n".join(lines) if lines else "", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
