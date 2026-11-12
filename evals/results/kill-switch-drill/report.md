# Kill-switch drill (offline, scripted model)

Run 2026-11-12 04:39 UTC at 1eee54d36775. Every model call takes a simulated 50 ms; a second thread writes the stop at a random moment in the case. No API calls. Live, the call in flight is a real model call, bounded at 60 s per attempt and two attempts (`agents/base.py` `default_client`), so live time-to-stop is up to about two minutes in the worst case and typically one call (seconds), not these milliseconds.

| Workflow | Agent stopped | Trials | Stopped | It had answered before the stop | Stop written after the case ended | Stop not noticed | Its calls after stop | Other agents' calls after stop | Through without review after stop | Time to stop, median / p95 / max (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| claims | `claims_supervisor` | 20 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0.0257 / 0.0558 / 0.2164 |
| claims | `intake` | 20 | 20 | 11 | 0 | 0 | 0 | 0 | 0 | 0.0271 / 0.0636 / 0.065 |
| desk | `complaint_supervisor` | 20 | 18 | 18 | 2 | 0 | 0 | 0 | 0 | 0.0338 / 0.0558 / 0.062 |
| desk | `complaint_triage` | 20 | 20 | 16 | 0 | 0 | 0 | 0 | 0 | 0.0281 / 0.0601 / 0.0631 |
| leave | `leave_supervisor` | 20 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0.0308 / 0.056 / 0.058 |
| leave | `leave_intake` | 20 | 20 | 9 | 0 | 0 | 0 | 0 | 0 | 0.0283 / 0.0643 / 0.0643 |

- **Gated:** no call by the stopped agent after its stop, nothing through without review, no stop written in time and never noticed, and at least 75% of trials stopped, so a broken switch fails the drill.
- **Other agents' calls after stop:** a stop on an agent the case already used ends it at the next step; a stop on an agent not used yet takes effect when it is next needed, so the case's other agents can make a few calls first (an intake call and a supervisor turn, here).
- **Its calls after stop** counts calls that began after the stop file was written; a call whose check ran during the few milliseconds of the write is counted apart in the results (`calls_during_the_write`).
- **Time to stop:** from the stop starting to be written to the case returning: the call in flight, any calls before the stopped agent is next needed, and the loop's overhead. **Stop not noticed** (gated at 0) is a stop on disk followed by a call with no refusal; a stop that landed after the case's last check is counted apart (`stop_after_last_check` in the results).
