# hermes-cron-watchdogs

Silent-until-it-matters cron scripts for [Hermes Agent](https://hermes-agent.nousresearch.com/docs) —
stdlib Python, zero model tokens per tick, and no message at all on a quiet day.

Built for Hermes' `no_agent` cron jobs, where **the script's stdout is the alert**: empty stdout
means "say nothing", anything non-empty is delivered verbatim to the chat. That single contract is
what makes a watchdog free to run every 15 minutes and still be welcome.

## The five rules

1. **Empty stdout silences the tick.** No "nothing changed" line, no header, no whitespace-only
   output — a bare newline is not empty and can arrive as a blank message.
2. **Always exit 0.** A non-zero exit or a timeout raises an *error alert*. A failed fetch is
   swallowed into a quiet run, never left to crash the tick.
3. **The first run seeds and stays silent.** Otherwise the first tick treats the entire market as
   new and dumps it into one message.
4. **The test marker is checked before the silence gate.** A marker honoured only on runs that
   already had something to report proves nothing — that run would have spoken anyway.
5. **One job carries one class of watch.** A slow multi-source sweep belongs in its own job at a
   lower cadence, so a fast alert is never delayed by a slow one.

## Quickstart

```bash
python3 watchdog.py --selftest          # exercises the alert path, no state write
python3 watchdog.py                     # 1st run: seeds state, prints nothing
touch ~/.local/state/watchdog/my-watch/.test
python3 watchdog.py                     # test message while nothing is new
python3 watchdog.py --status            # read-only: what is currently in the window
python3 watchdog.py --reset-baseline    # re-seed (no need to delete state.json)
```

State lives in `--state-dir PATH`, else `$WATCHDOG_STATE_DIR`, else
`~/.local/state/watchdog/<slug>/`, written atomically (`os.replace`), keyed by the source's stable
per-item id and capped so the map cannot grow forever.

## Modes every script has

| Flag | Behaviour |
| --- | --- |
| *(none)* | normal tick — cron calls this |
| `--status` | read-only dump of the current match set; changes no state, sends nothing |
| `--reset-baseline` | re-seed on the next run. Use this instead of `rm state.json`, which trips the destructive-command approval prompt every time |
| `--selftest` | replace the real fetch with one synthetic item the alert should catch, skip state writes, print the line |

`--selftest` is not decoration: a watch whose alert path has never fired once is untested, however
long it has been scheduled. It proves the *report* is right; the `.test` marker proves *delivery* is.

## Wiring it into Hermes

```bash
hermes cron add \
  --no-agent \
  --script ~/watches/my_watch.py \
  --schedule "*/30 * * * *" \
  --deliver origin
```

Then **fire it once and read the delivered text on disk** (`cron/output/<job_id>/<ts>.md`) before
believing it works — and fire it a second time to confirm the silence path really is empty.

## Examples

Both are self-contained, runnable, and use public APIs — no scraping, no key:

- **`examples/github_release_watch.py`** — new release for a repo.
  `python3 examples/github_release_watch.py --repo psf/requests`
- **`examples/launchpad_package_watch.py`** — has release *X* shipped a new version of package *Y* yet.
  `python3 examples/launchpad_package_watch.py --series noble --source linux`

The second one is worth reading even if you never watch packages: it shows why the Launchpad API is
the right source for "is it out yet" (a CVE page shows *work in progress in <version>* for months,
and that version is the target, not a download), and why only the `Security`/`Updates` pockets may
alarm — `Proposed` is the staging repo, and treating it as released destroys the whole point of the
watch. The script refuses a staging pocket rather than quietly alarming on nothing.

## SKILL.md

`SKILL.md` is the same contract written as a Hermes skill — drop it into your skills directory if you
want the agent to follow this pattern when it builds watches for you, instead of rediscovering it.

## License

MIT
