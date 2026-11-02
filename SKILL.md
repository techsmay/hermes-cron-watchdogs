---
name: cron-watchdog-scripts
description: "Use when a cron tick script must alert only on real change."
version: 1.0.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cron, watchdog, scraping, state, silent-alerts]
---

# Cron Watchdog Scripts

The engineering contract for a `no_agent` cron job whose stdout *is* the alert: a stdlib script that
fetches a source, compares against stored state, and prints a short report only when something
genuinely new appeared. Zero model tokens per tick, and silence is the normal outcome.

## When to use

- Building or debugging a recurring watcher (price, listing, availability, deal sweep, release
  feed) that should cost nothing per run and make no noise on quiet days.
- A watch already exists and misbehaves: fires on every tick, fires once with the whole market, or a
  test run proved nothing.
- Turning a one-off "can you check X daily" into a scheduled script.

Not for: what to alert on and how to rank findings, scheduling timestamps and delivery targets, or
anything needing reasoning per run — that is a normal LLM cron job, not a watchdog.

## Always-on rules

- **Empty stdout silences the tick; anything non-empty is delivered verbatim.** No "nothing changed"
  line, no empty header, no whitespace-only output — a bare newline is not empty stdout.
- **Always exit 0.** A non-zero exit or a timeout raises an *error alert* to the user; an exception
  in the scraper must be swallowed into a quiet run or a deliberate one-line failure message.
- **One job carries one class of watch.** A slow multi-source sweep belongs in its own job at a lower
  cadence than a single-page check, so the fast alert is never delayed by the slow one.
- **The first run seeds and stays silent**, and re-baselining is a script flag, not a file deletion.
- **The test marker is evaluated before the silence gate** (step 4) — otherwise a "successful" test
  proves nothing about delivery.

## Procedure

### 1. Prove one fetch in the foreground first

Run the fetch once interactively and save the raw response to a scratch file before any parsing or
scheduling. Never schedule a job around an unproven URL shape — a wrong URL can return a *market*
page (200, plausible HTML, no target item) rather than an error, so the script looks healthy while
watching nothing.

### 2. Parse defensively, and against a fixture

- **Strip `<script>` and `<style>` blocks before flattening the markup.** Embedded JSON-LD carries
  the item title *and description*, and a description can contain its own prices ("für 50 € mehr") —
  flatten first and the price regex returns the description's number, turning a 1 050 € listing into
  the bargain of the day.
- **Bind patterns to a stable identifier** from the page (product/article id in the URL or an
  attribute), not to position or to the page's main element — a product page also renders
  marketplace offers of the same model.
- **Prefer a word-boundary keyword list plus a substring list.** `\bdock\b` does not match
  "Docking Station"; accessories and wanted-ads otherwise dominate a used-market sweep.
- Keep the parser in a function that takes the raw body and returns records, so the same fixture can
  be replayed offline after every change.

### 3. State file: seed silently, re-baseline by flag

- Records are keyed by the source's stable id, so a listing that re-appears is not reported twice;
  cap the map so it cannot grow forever.
- **First run (or a re-baseline) writes every current match into the seen map, prints nothing, and
  returns.** Otherwise the first tick treats the entire market as new and dumps it into one message.
- **Expose re-baselining as `--reset-baseline` inside the script.** `rm state.json` from the shell
  triggers the destructive-command approval every time.
- Add a read-only `--status` mode printing the current match set; it is how you inspect the watch
  without changing state or messaging the user.
- Write state atomically (`write tmp`, then `os.replace`) — a crash mid-write must not truncate it.

### 4. Silence gate and the test marker, in this order

```
lines = build_report(...)          # [] when there is nothing new
test = os.path.exists(TEST_MARKER)
if not lines and not test:
    save_seen(...); print("", end=""); return      # silent tick
if test:
    lines.insert(0, "TEST RUN - delivery works")
    if not lines[1:]: lines.append("(nothing new. Currently in window: N)")
    os.remove(TEST_MARKER)                         # consume it
print("\n".join(lines))
```

A marker honoured only on runs that already have something to report proves nothing: that run was
silent too. The test fire must produce a message **while nothing is new**, which is the case that
actually gets exercised on quiet days.

### 5. Install and prove it

Create the job (`no_agent=true`, `script=<name>.py`, `deliver=origin`, cadence sized to how fast the
source changes and how politely it may be polled), then fire it once manually and **read the
delivered text on disk** before telling the user it works. Also fire it a second time with no marker
and confirm the output is empty — that is the silence path.

**Exercise the alert path, not just delivery.** The test marker proves the tick can deliver; it says
nothing about whether the parser and the report line are right. Give the script a `--selftest` flag
that swaps the real fetch for one synthetic item the alert is supposed to catch (plus a sibling flag
for each extra stage, e.g. `--selftest-remind` for the "N days before" branch), skip state writes
while it runs, and read the printed line.

### 6. Dated sources: anchor years, staleness, one alert per occasion

Anything that lists *upcoming* dated events (announcement feeds, event calendars, release schedules)
needs three guards on top of the id-keyed state:

- **A yearless date is ambiguous.** Parse it against the current year, then only roll it into next
  year when the gap to today is nearly a full year (announcements made in December for January). A
  date 45–300 days in the past is a stale entry still sitting in the feed, not an upcoming event —
  drop it, or the first run after a quiet month resurrects last season's programme as new.
- **Key the alert on the occasion, not the post.** One event typically produces several posts
  (teaser, lineup, day-of reminder) on different ids. Store the event under its normalized date,
  merge later posts into that record, and alert once; a per-post key sends the user three messages
  about one party.
- **A "remind N days before" stage needs its own consumed flag** per event, or the reminder repeats
  on every tick inside the window.

### 7. "Has it been released yet?" — use the Launchpad API, not the CVE page

For a Debian/Ubuntu package the CVE page is a trap: it shows *work in progress in <version>* for
months, and the version named there is the target, not a download. The authoritative, greppable
source is Launchpad:

```
https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedSources\
  &source_name=<pkg>&distro_series=/ubuntu/<series>&status=Published&exact_match=true
```

Count only `pocket` in `Security`/`Updates` — `Proposed` and `Build` are staging repos and must not
alarm (that is how a watch stays quiet for the days between the two).
`getPublishedBinaries&binary_name=<binary>&exact_match=true` answers whether one specific binary
exists at all. If the package is a kernel, remind the reader of the two steps an alert implies:
`apt full-upgrade` **and** a reboot, verified with `uname -r` — a container patches nothing
kernel-related, its kernel comes from the host.

## Pitfalls

- **The test marker after the silence gate.** The tick returns silently before the marker is ever
  seen, the marker stays on disk, and the "delivery verified" claim is false. This is the most
  repeated bug in this class of script — check the ordering every time you touch the gate.
- **First tick reports the whole market.** No baseline seeding, or a widened search window without
  re-seeding.
- **Values parsed out of the JSON-LD description** instead of the rendered card (step 2). Symptom:
  absurdly low numbers attached to real listings.
- **Wanted ads (Suche/Gesucht) rank as the cheapest hits** in any used-market sweep. Match a bare
  `suche`/`gesucht` in the *title* with a single carve-out for marketing copy ("auf der Suche nach"),
  and never test the description — genuine listings write prose there.
- **A converted time that uses today's clock offset.** Converting a stated time ("10PM ET") into the
  user's zone with the *current* date's offsets is an hour wrong whenever the event falls on the
  other side of a daylight-saving change. Build the stamp from the event's own date:
  `datetime.combine(event_date, time(h, m), tzinfo=SRC_TZ).astimezone(USER_TZ)`.
- **A page that loads in a browser returns 403 to the scraper**, or vice versa. Test the exact fetch
  path the script will use, and if a source blocks the host, say so plainly instead of shipping a
  watch that will always be empty.
- **An error page overwrites last-known-good state.** On a failed fetch keep the old observation and
  stay silent; only a successful parse may update state.
- **Reporting a total you did not count.** Aggregate with code (dedupe, sort, count) and check the
  number against what was collected before it goes into the message.
- **No link on the entry.** A watchdog that names a price without its URL forces the user to search
  again, which is the work the watch was supposed to remove.

## Verification

- [ ] One foreground fetch succeeded and its raw output was saved before scheduling.
- [ ] Parser replays a saved fixture offline; value and identity come from the right place.
- [ ] First run seeded silently; `--status` works; `--reset-baseline` re-seeds without a delete.
- [ ] Test marker checked before the silence gate, and consumed after the test fire.
- [ ] Test fire delivered a message with nothing new; the next marker-less run was empty.
- [ ] `--selftest` prints the alert line without touching state.
- [ ] Every delivered line carries its own URL, and any comparison price names the config it refers to.

## Templates

- `watchdog.py` — the full skeleton: fetch, parse, state, silence gate, test marker,
  `--status`/`--reset-baseline`/`--selftest`. Copy and adapt the parser.
- `examples/` — two runnable watches on public APIs.
