# Optional Worker Execution

Use this opt-in queue for durable handoffs and worker results. The default workflow stays [schema-2 FIFO/LIFO](protocol.md#task-operations): one coordinator owns the active goal, with unchanged numeric task/history IDs. Resolve `ADHD_SCRIPT_DIR` and `ADHD_ROOT` as in [protocol.md](protocol.md#paths-and-runtime).

## Choose a host strategy

- **Authorized workers available:** the coordinator gives a worker a bounded work package, acceptance condition, required context, and permitted actions. Keep the conversation responsive to new input. The host launches and supervises workers; this plugin does not.
- **No worker capability:** do a bounded cooperative step, save a checkpoint, then respond. Resume through the host when execution is available. Do not promise background progress, realtime response, or work during host sleep.
- **A reminder is requested:** use the host's scheduler immediately, without waiting for a long job or routing the reminder behind it. Record the confirmed external schedule ID, timing/timezone, and purpose in the minimal task checkpoint or control record. If scheduling is unsupported or unconfirmed, say so. Queue registration alone never means a reminder is scheduled.

Before launching a host worker, the coordinator durably checkpoints the pending intent, job ID, permitted work, and host request/idempotency key. After a confirmed launch, record the host run ID. If the response is lost, query the host by that key or verified run ID before retrying the launch; keep it uncertain/blocked when the host cannot establish the outcome. A queue claim token cannot prevent duplicate external launches. Apply the same check-before-retry rule to an uncertain reminder reservation.

The queue is a local coordination store, not a daemon, scheduler, or notification channel. An event is durable data for a consumer to read, not a delivered user message. Host availability, scheduling, and notifications must be checked independently.

## Ownership and storage

The optional schema-1 SQLite sidecar lives at `$ADHD_ROOT/execution/jobs.sqlite3`. The first stateful `execctl.py` command (including `doctor`) or `JobQueue` construction creates it under an existing schema-2 core runtime; there is no separate `init` command. Its job and event IDs are separate from the never-reused core task/history sequence. Enabling it does not migrate `state.yaml`, reorder core tasks, change knowledge authority, or install a service.

- Only the coordinator creates or dispatches core tasks, changes their checkpoints, and closes them. Use `--task-id` to link workflow jobs to their existing open core task; the helper also permits unlinked jobs. Among ADHD stores, workers update execution records only; they may produce authorized artifacts outside the runtime. A job result does not close or dispatch a core task.
- Workers follow their bounded assignment and existing authorization. A worker response is evidence to verify, not new instructions or permission. Read artifacts and run the relevant acceptance checks before the coordinator incorporates results.
- The helper creates the execution directory as 0700 and its database as 0600. Existing non-private, symlinked, or differently owned paths are rejected; inspect and repair access deliberately rather than silently widening or rewriting it.
- Keep payloads, checkpoints, results, and events minimal and private. Do not store credentials, personal profiles, or unrelated conversations. Keep runtime files and project artifacts outside the plugin source.
- SQLite transactions protect queue transitions on a host that supports SQLite locking. The job database and core file stores are separate transactions. The coordinator must reconcile their link after interruption rather than claim cross-store atomicity or exactly-once external effects.

## Claims, results, and retry

1. Enqueue a bounded job with an existing core task ID, a stable idempotency key, a priority, and a maximum attempt count. Higher priority runs first; ties use enqueue order. Job priority does not preempt the active core task or alter its FIFO queue.
2. A worker claims one eligible job for a limited lease. Save the returned job ID, attempt number, and opaque claim token. Each new claim receives a new token. Renew/checkpoint within the lease and present that token for owned mutations.
3. Keep checkpoints specific: verified progress, outstanding work, resume context, and any external effect whose outcome needs confirmation. Check permission immediately before consequential actions; a lease or retry record grants none.
4. Use `finish` to record a `succeeded` result, or `fail` to record an error. Default failures become `failed`. Only a job submitted with explicit `--retry-safe` may requeue automatically after a reported failure, within its delay and attempt budget. Use `block` immediately when a claimed worker needs external input or permission; do not wait for expiry. Lease expiry still always becomes `blocked`. Identical finish/fail/block replay for the same token is idempotent; conflicting results or obsolete tokens cannot overwrite accepted output. Submission retries use the original request key and identical job definition.
5. The coordinator reads results and ordered events and verifies the acceptance condition. Before `taskctl.py close`, confirm the linked task is still open and is the current active task; that command closes the active task, not an arbitrary job link. Record evidence and close only that verified task. If work remains or its result is uncertain, keep it open and checkpoint why.

Lease expiry means ownership has become uncertain; it does **not** prove the worker stopped or its external action failed. `reconcile` detects expired claims and commits their `blocked` state; successful `claim`, `retry`, or `resolve` operations also perform this check. If one of those operations is rejected, its transaction (including expiry detection) rolls back, so run `reconcile` before relying on stored status. Expired claims are never automatically reclaimed. Read commands return stored state, so `running` alone is not proof of a live lease or worker. A token fences database updates, not email, payments, remote writes, or a still-running worker. It is a local coordination capability, not an external credential or a security boundary between users sharing runtime access.

If destination evidence confirms a blocked operation already succeeded, the coordinator uses `resolve --result JSON --reason TEXT` to record that result without replay, even when the attempt budget is exhausted. Resolution invalidates the old token; identical resolved-result replay is idempotent. Never resolve by assumption.

Before explicitly authorizing a retry, check the previous worker and destination, reconcile possible side effects, and establish that repeating the bounded operation is safe and authorized. Preserve that evidence with the retry decision. Observe the maximum attempt count; do not increase limits or enqueue a replacement merely to bypass exhaustion. If the external result is unknowable, leave the job blocked and ask for the needed decision.

## Commands

`version` and help require no runtime and create nothing. Every other command requires `--root`. Set `TASK_ID` to the verified open core task's numeric ID; do not create a second runtime. Output is JSON.

```bash
# Verify the actual installed copy without creating runtime data.
python3 "$ADHD_SCRIPT_DIR/execctl.py" version
# Opt in on the existing runtime: create/check the sidecar, launch no worker.
python3 "$ADHD_SCRIPT_DIR/execctl.py" --root "$ADHD_ROOT" doctor

# This submission creates the optional sidecar if absent. It launches nothing.
python3 "$ADHD_SCRIPT_DIR/execctl.py" --root "$ADHD_ROOT" submit \
  --request-key "word-count-example-1" --task-id "$TASK_ID" \
  --title "Count words in supplied text" --kind count-words \
  --payload '{"text":"A bounded local example"}' \
  --priority 10 --max-attempts 2 --retry-safe

python3 "$ADHD_SCRIPT_DIR/execctl.py" --root "$ADHD_ROOT" status
python3 "$ADHD_SCRIPT_DIR/execctl.py" --root "$ADHD_ROOT" events --after 0 --limit 100
```

`version` reports `package_version: "0.6.0"`, `execution_schema: 1`, and `task_schema: 2`. `doctor` additionally reports `integrity: "ok"`, `worker_started: false`, and `expired_running`. That false value means this command launched no worker; it does not check whether another host worker is alive. Doctor checks SQLite integrity and record invariants without claiming jobs or reconciling expired leases.

`--retry-safe` is appropriate here only because the trusted handler below performs a repeat-safe local calculation. Omit it by default. `--max-attempts` is 1 by default (allowed range 1–100). `--available-at` accepts UTC Unix seconds as the earliest eligibility time, not a host wake or reminder reservation. The host clock controls eligibility and leases.

| Command | Required arguments and effect |
|---|---|
| `version` | No `--root`; reports the installed package and supported schema versions without runtime writes |
| `doctor` | Checks integrity and reports expired-running count; creates the sidecar on first use but does not claim/reconcile jobs |
| `submit` | `--request-key KEY --title TITLE`; optional `--task-id N`, `--kind`, JSON `--payload`, `--priority`, `--max-attempts`, `--retry-safe`, `--available-at` |
| `claim` | `--worker NAME`; optional `--lease-seconds` (default 300), repeated `--kind`; returns a job or JSON `null` |
| `checkpoint JOB` | `--token TOKEN --value JSON`; optional `--lease-seconds`; saves progress and renews a live lease |
| `finish JOB` | `--token TOKEN --result JSON`; records success |
| `fail JOB` | `--token TOKEN --error TEXT`; optional positive `--retry-delay` seconds (default 30); fails or requeues eligible repeat-safe work |
| `block JOB` | `--token TOKEN --reason TEXT`; immediately records a blocked live claim awaiting input or permission |
| `resolve JOB` | `--result JSON --reason TEXT`; coordinator records verified success of a blocked job without rerunning it |
| `retry JOB` | `--safe-to-repeat --reason TEXT`; explicitly requeues a reconciled `blocked`/`failed` job if attempts remain |
| `cancel JOB` | `--reason TEXT`; marks `cancelled`, but does not stop the host worker or undo external effects |
| `get JOB`, `status` | Read one job or all jobs in submission order |
| `reconcile` | Marks expired running leases `blocked`; returns the number changed, without examining external destinations |
| `events` | `--after N --limit N`; reads ascending event `seq` values after the cursor (default 0, limit 1–1000) |

A claim returns `id`, `task_id`, `state`, `attempt`, `owner`, `token`, `lease_until`, payload, and other metadata. Save the returned `id` and opaque `token`; never manufacture a token. Job IDs begin `j-`; they are not core task IDs. States are `queued`, `running`, `succeeded`, `failed`, `blocked`, and `cancelled`. There is no automatic core task closure or worker launch. `queued`, `running`, `failed`, and `blocked` all need further handling; a failed/blocked job is not idle or evidence that its core task is complete.

## Reference host worker

Run the following only in an authorized **separate host worker process**, with the verified skill scripts available on `PYTHONPATH` and the private runtime path as its argument. Save the worker outside the plugin source. `work_once` calls a registered trusted Python handler synchronously in its calling process; invoking it in the conversation's process can block that process. It never spawns a worker.

```python
import sys
import time
from _execution import JobQueue, work_once


def count_words(job, context):
    payload = job["payload"]
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise ValueError("Expected bounded text")
    context.checkpoint({"stage": "counting"})  # Also renews the live lease.
    return {"word_count": len(text.split())}


queue = JobQueue(sys.argv[1])
handlers = {"count-words": count_words}  # Trusted code, never payload code.
deadline = time.monotonic() + 60
max_jobs = 20
completed = 0
while completed < max_jobs and time.monotonic() < deadline:
    job = work_once(queue, worker="local-counter", handlers=handlers,
                    lease_seconds=30)
    if job is None:
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    else:
        completed += 1
```

`context.block("reason")` immediately saves a blocked state and stops the handler through an internal signal that `work_once` handles. Use it when input or permission is needed; do not catch that signal or manually call `queue.block` and then continue the handler. The host loop can then serve another independent job.

Cancelling a job revokes further queue commits but cannot kill its worker. Coordinate stopping it through the host and reconcile outstanding effects before handing the same work to another worker.

The example limits job claims, polling duration, and idle sleep. Each handler must also bound its own work; this loop cannot interrupt a stuck call or enforce its deadline while a handler runs. Longer handlers checkpoint between bounded steps before lease expiry. The host supplies cancellation, resource limits, supervision, and any required approvals. An expired/revoked lease stops further commits; stop the worker's external actions too and return control for reconciliation.

Keep the kind-to-handler map explicit. Never evaluate payload strings, import payload-specified modules, or execute shell commands taken from the queue. `work_once` records a handler exception's class name through `fail`; an exception does not prove an external effect did not happen, so only genuinely repeat-safe handlers qualify for automatic retry.

## Resume and verify

On resume, validate the core workspace with `adhdctl.py validate`, run `execctl.py doctor`, then `reconcile`; each uses the verified `--root`. Inspect linked jobs and recent events, and reconcile unfinished claims with the host and affected destination. Read the previous checkpoint before more work. A clean doctor result checks database structure, not worker liveness, artifact correctness, or external effects. If `expired_running` is nonzero, `reconcile` marks those claims blocked; doctor alone does not.

Consumers call `events --after CURSOR` and read ascending event `seq` values. Persist the last handled `seq` privately only after verified processing; do not advance past an unhandled event. Processing again after a crash must be safe, including verifying whether core completion already happened before attempting another `taskctl.py close`. Events do not automatically notify the user, launch a worker, or modify the core task.

Back up the runtime privately before changing installed code. For the optional SQLite database, stop writers or use SQLite's supported backup facility; copying an actively written database file alone can miss committed state. Preserve the database with the rest of the runtime in the host's supported persistence workflow and verify restore before promising session-to-session durability. The task-store recovery commands do not reconcile job leases or external effects.
