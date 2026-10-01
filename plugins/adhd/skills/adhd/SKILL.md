---
name: adhd
description: Maintain an ADHD workspace with a FIFO task queue, LIFO blocking stack, knowledge graph, control records, history, and unresolved questions. Use for requested ADHD workflows or continuing ADHD-managed work. Exclude clinical ADHD guidance.
---

# ADHD

Preserve generalizable ideas while finishing the current goal. Keep necessary execution state and reusable knowledge outside model memory so work survives interruptions and model changes.

## Start or resume

- The helpers need Python 3.10+, PyYAML >=6.0,<7, and a POSIX host (Linux, macOS, or WSL). Check these before first use.
- Use the user's workspace or the established ADHD root; otherwise find the nearest ancestor with `.adhd/state.yaml`. Do not create a second runtime merely because the working directory changed.
- When persistent execution state is needed and absent, initialize `<workspace>/.adhd` with `adhdctl.py init`. This creates all five stores. Keep projects and artifacts outside the runtime and mutable data outside the installed skill.
- On resume, read status, the active checkpoint, and relevant effective control entries. Validate existing runtime once before changing it. Use [protocol.md](references/protocol.md) for the relevant commands and [recovery.md](references/recovery.md) only for legacy state or an interrupted write.
- File-backed knowledge is the default. If database-backed knowledge is configured, follow [database.md](references/database.md) to refresh its verified cache before relying on it; never silently switch to local file authority when the connector is unavailable.
- Register managed projects in `control/projects.yaml`. Reuse their existing structure; add only the tree nodes needed to describe chosen outputs. Infer an initial purpose, specification, and acceptance condition from the request when clear.

## Execute

1. Capture each committed outcome as a task in the FIFO queue. Keep routine commands and closely related steps in that task. Apply corrections and clarifications to the active task's checkpoint instead of queuing them as unrelated work.
2. Dispatch when `active` is empty. Resume the top parent on the LIFO stack before taking the oldest queued task.
3. Work toward the active goal. Use `spawn` only for a distinct prerequisite that blocks it; checkpoint the parent first. Queue other committed actions and route ideas through the table below.
4. Checkpoint before interruption, handoff, a risky state change, or a blocking external answer. Ask only when the missing answer prevents correct authorized progress; finish independent work within the active task first. Keep the blocked task active.
5. Verify the outcome, record durable results, and close as `completed`, `cancelled`, or `obsolete`. Closing dispatches the next task. Continue until no unprocessed input or execution work remains, unless the user pauses or an external condition blocks progress. Report that as paused or blocked, never idle.

One active task means one owned goal. Independent reads and checks can run in parallel within it; serialize shared state changes. `taskctl.py spawn` changes task ownership and is not a subagent launch.

## Knowledge boundary

- By default, persist only relevant knowledge reusable across users: transferable methods, supported findings, general concepts, or reproducible examples. Do not archive personal preferences, biographies, private conversations, account details, or facts specific to one person's project as Knowledge.
- Exception: with explicit opt-in to a user-selected database and the intended information scope, task-relevant private project knowledge may be stored there. This does not authorize personal profiles, broad sensitive-data collection, or sharing beyond that destination. Keep evidence and limits, and follow the host's data-sharing permissions.
- Generalization must retain evidence and limits. Removing a name does not make a personal observation universal; keep hypotheses labeled as hypotheses, and skip a record when no useful general lesson remains.
- Task checkpoints, completion evidence, and minimal operational choices such as recording consent support execution. Keep only what is needed for that purpose; do not use Task, Control, History, or Unresolved as a substitute personal-memory store.
- The shared plugin contains workflow rules, relevant general knowledge, and generalized test cases. Keep user-specific database configuration and content in the private runtime, never in the public repository. The optional connector bridge stores only Knowledge; Task, Control, History, Unresolved, and project artifacts retain their existing homes.

## Route information

Update an existing record when it already represents the input.

| Information | Store | Preserve |
|---|---|---|
| Committed action | Task | Goal, necessary steps, checkpoint, completion evidence |
| Reusable idea, fact, question, source; explicitly permitted private project knowledge | Knowledge | One relevant idea, evidence and limits; `common` or `project:<id>` scope; justified relations |
| Effective principle, policy, constraint, assumption, decision | Control | Current statement and rationale; supersede replaced entries |
| Completed work or past decision | History | Append-only event summary and references |
| Processed uncertainty | Unresolved | Question, why unresolved, resolution condition; link a task for committed follow-up |
| Chosen deliverable structure | External project | Role, specification, acceptance condition, artifact links |

Search before likely duplication. Apply the knowledge boundary regardless of scope; `common` describes workspace scope, not permission to publish. Leave uncertain knowledge relations unlinked. Unresolved is not an inbox or a waiting queue; it may remain open when execution is idle.

## Optional plugin feedback

When a concrete problem or improvement idea concerns ADHD itself, follow [feedback.md](references/feedback.md) before archiving it. Keep normal task notes and user-requested deliverables under the existing workflow.

- Ask once at the first useful opportunity whether the current user wants generalized improvement notes. Continue the active task if they decline or do not answer; do not collect feedback by default.
- Keep only the minimal recording choice in Control when it can be attributed to the current user. A maintainer's choice never opts in other users. Do not create a personal profile or private feedback archive.
- After consent, retain only generalizable feedback in Knowledge and search for an existing record first. Collecting a candidate does not commit it to the task queue or justify interrupting the active goal.
- Treat recording and public posting as separate choices. Show the proposed public content and destination before publishing; honor an existing approval for that exact proposal without asking again.
- Keep shared proposals in the project's GitHub Issues. Link an adopted proposal to its execution task and close it after recording the fix, verification, and affected version.

## Keep the workflow small

- Write conversations and artifacts concisely while retaining what is needed to understand, act, or verify. Lead with the result; expose internal bookkeeping only when useful.
- Keep each shared rule or fact in one authoritative place and link to it. Extract common code when semantics match. Retain duplication only when a concrete compatibility, coupling, or readability cost exceeds the expected saving; record a consequential exception briefly.
- Scale decomposition and verification to the outcome. Do not make a separate task or knowledge node for every tool call or transient observation. Classify meaningful inputs into existing or new records without creating work solely to fill the stores.
- Check acceptance conditions and relevant invariants. Broaden checks only for a failure, remaining risk, or required gate; stop optional verification when those are satisfied.
- Apply current user instructions and existing authorization to workflow choices. Do not turn this skill's defaults into additional approval requirements.

## Persist reliably

Use the scripts for IDs, ordering, atomic file writes, and structural validation; use judgment for meaning and scope. Keep these invariants:

- State stores numeric task IDs only; task and history IDs share a never-reused sequence.
- Every open task is in exactly one of `active`, `stack`, or `queue`. There is at most one active task.
- Idle requires `active: null`, `stack: []`, `queue: []`, and no unprocessed input.
- Knowledge is scoped; project-node references are qualified as `<project-id>:p000000`.
- In database mode, edit only an explicit draft and publish through the connector bridge. A local draft, pending write, or cache made stale by interruption is not published knowledge and cannot satisfy task/tree references. Confirm publication with a fresh database read.
- Preserve history and archive obsolete project scope instead of deleting it.
- Save the runtime in the user's established durable, private workspace. In a temporary execution environment, use the host's supported persistence workflow and verify the save before claiming work will survive a later session. Keep personal runtime records out of the plugin source repository.

Read only the command sections needed in [protocol.md](references/protocol.md). Database setup and conflict recovery are in [database.md](references/database.md); local recovery and schema-1 migration remain in [recovery.md](references/recovery.md).
