#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skeleton for a no_agent cron watchdog tick.

Contract: stdout *is* the alert. Empty stdout means silence. Always exit 0.

The order of the silence gate and the test marker is deliberate — read the
README (or SKILL.md) before changing it.

Run modes:
  python3 watchdog.py                    # normal tick (cron)
  python3 watchdog.py --status           # read-only: current matches
  python3 watchdog.py --reset-baseline   # re-seed state, prints nothing
  python3 watchdog.py --selftest         # synthetic item, exercises the alert path

State location: --state-dir PATH, else $WATCHDOG_STATE_DIR, else
~/.local/state/watchdog/<SLUG>/
"""

import json
import os
import re
import sys
import time
import urllib.request

# ── configure ────────────────────────────────────────────────────────────────
SLUG = "my-watch"

SOURCES = ["https://example.invalid/search?q=thing&page={p}"]
MAX_PAGES = 3
PAUSE = 0.8                 # seconds between paginated requests
MAX_ITEMS = 10
PRICE_MAX = 620             # alert window: report anything at or below this
HOT = 580                   # 'grab it' marker inside the report
LANG = "de-DE,de;q=0.9"
# ─────────────────────────────────────────────────────────────────────────────

ARGS = sys.argv[1:]


def _arg(name, default=None):
    if name in ARGS:
        i = ARGS.index(name)
        if i + 1 < len(ARGS):
            return ARGS[i + 1]
    return default


STATE_DIR = (
    _arg("--state-dir")
    or os.environ.get("WATCHDOG_STATE_DIR")
    or os.path.expanduser(f"~/.local/state/watchdog/{SLUG}")
)
STATE_FILE = os.path.join(STATE_DIR, "state.json")
TEST_MARKER = os.path.join(STATE_DIR, ".test")

STATUS_ONLY = "--status" in ARGS
RESET = "--reset-baseline" in ARGS
SELFTEST = "--selftest" in ARGS

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PRICE_RE = re.compile(
    r"(\d{1,3}(?:[.\s\u00a0\u202f'\u2019]\d{3})+|\d{1,4})(?:,(\d{2}))?\s*€")


def fetch(url):
    """Return (body, status). Never raises: a failed fetch must not kill the tick."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                              "Accept-Language": LANG})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", "replace"), r.status
    except Exception:                                     # noqa: BLE001
        return "", 0


def parse(body):
    """Return [{'id','title','price','url'}] for the target item only.

    Strip script/style first: embedded JSON-LD carries item descriptions that
    can contain their own prices, so flattening the raw document makes the
    price regex return the wrong number. Bind patterns to a stable per-item id
    (from the URL or an attribute), never to page position — a product page
    also renders other offers of the same model.
    """
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    out = []
    for item_id, chunk in re.findall(
            r'<article[^>]*data-id="(\d+)"[^>]*>(.*?)</article>', body, re.S):
        text = re.sub(r"<[^>]+>", " ", chunk)
        text = re.sub(r"\s+", " ", text).strip()
        m = PRICE_RE.search(text)
        if not m:
            continue
        whole = re.sub(r"[.\s\u00a0\u202f'\u2019]", "", m.group(1))
        price = float(whole) + (float("0." + m.group(2)) if m.group(2) else 0.0)
        out.append({"id": item_id, "title": text[:90], "price": price,
                    "url": "https://example.invalid/item/" + item_id})
    return out


def collect():
    """Parse every source/page. Stay polite: pause between pages."""
    found, errors = [], []
    for tpl in SOURCES:
        for page in range(1, MAX_PAGES + 1):
            body, status = fetch(tpl.format(p=page))
            if not body:
                errors.append(f"{tpl} (no response, status {status})")
                break
            batch = parse(body)
            if not batch:
                break
            found.extend(batch)
            if page < MAX_PAGES:
                time.sleep(PAUSE)
    # dedupe by id, then keep only what is inside the alert window
    best = {}
    for it in found:
        if it["price"] <= PRICE_MAX:
            best.setdefault(it["id"], it)
    return sorted(best.values(), key=lambda x: x["price"]), errors


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                     # noqa: BLE001
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)          # atomic: a crash cannot truncate state


def eur(v):
    return f"{v:,.0f} €".replace(",", ".")


def build_lines(new):
    if not new:
        return []
    lines = ["Watchdog: new matches", ""]
    for it in new[:MAX_ITEMS]:
        mark = " - grab it" if it["price"] <= HOT else ""
        lines.append(f"- {eur(it['price'])} | {it['title']}{mark}")
        lines.append(f"  {it['url']}")       # every entry carries its own link
    if len(new) > MAX_ITEMS:
        lines.append(f"... and {len(new) - MAX_ITEMS} more.")
    return lines


def selftest_items():
    """One synthetic item the alert is supposed to catch.

    A watch whose alert path has never fired once is untested, however long it
    has been scheduled. --selftest swaps the real fetch for this and skips
    state writes.
    """
    return [{"id": "selftest-1", "title": "synthetic item for the alert path",
             "price": HOT - 10, "url": "https://example.invalid/item/selftest"}]


def main():
    state = {} if RESET else load_state()
    seen = state.get("seen") or {}
    base_done = bool(state.get("baseline_done"))
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    if SELFTEST:
        matches, errors = selftest_items(), []
    else:
        matches, errors = collect()

    new = [m for m in matches if m["id"] not in seen]

    if STATUS_ONLY:
        print(json.dumps({
            "runs": state.get("runs", 0),
            "baseline_done": base_done,
            "current": [f"{m['price']} EUR {m['title'][:60]}" for m in matches],
            "errors": errors,
        }, ensure_ascii=False, indent=1))
        return

    def commit(lines):
        if not SELFTEST:                 # never let a test run move the baseline
            for m in matches:
                seen[m["id"]] = {"t": ts, "p": m["price"]}
            if len(seen) > 900:          # cap so the map cannot grow forever
                for k in list(seen)[:len(seen) - 900]:
                    seen.pop(k, None)
            state.update({"seen": seen, "baseline_done": True,
                          "runs": state.get("runs", 0) + 1, "last_run": ts})
            save_state(state)
        if lines:
            print("\n".join(lines))
        else:
            # Silent tick: emit literally nothing. A bare newline is not empty
            # stdout and can be delivered as a blank message.
            print("", end="")

    if not base_done and not SELFTEST:
        # First run after setup or --reset-baseline: seed the seen map and stay
        # quiet, otherwise this tick reports the entire market as new.
        commit([])
        return

    lines = build_lines(new)

    if lines:
        if errors:
            lines += ["", "Fetch problems: " + "; ".join(errors[:3])]
        commit(lines)
        return

    # Nothing new. The test marker is checked HERE, before going silent — a
    # marker honoured only on runs that already have something to report
    # proves nothing, because that run would have spoken anyway.
    if os.path.exists(TEST_MARKER) and not SELFTEST:
        try:
            os.remove(TEST_MARKER)
        except OSError:
            pass
        commit(["Watchdog test run - delivery works.", "",
                f"(nothing new. Currently in window: {len(matches)})" if not errors
                else "(nothing new, but: " + "; ".join(errors[:2]) + ")"])
        return

    commit([])


if __name__ == "__main__":
    main()
