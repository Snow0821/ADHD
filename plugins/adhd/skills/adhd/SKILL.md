---
name: adhd
description: Maintain an ADHD workspace with a FIFO task queue, LIFO blocking stack, knowledge graph, control records, history, and unresolved questions. Use for requested ADHD workflows or continuing ADHD-managed work. Exclude clinical ADHD guidance.
---

# ADHD

Preserve ideas while finishing the current goal. Keep execution state and reusable knowledge outside model memory so work survives interruptions and model changes.

## Start or resume

- The helpers need Python 3.10+, PyYAML, and a POSIX host (Linux, macOS, or WSL). Check these before first use; dependency versions are in the plugin's `requirements.txt`.
- Use the user's workspace or the established ADHD root; otherwise find the nearest ancestor with `.adhd/state.yaml`. Do not create a second runtime merely because the working directory changed.
- When persistent execution state is needed and absent, initialize `<workspace>/.adhd` with `adhdctl.py init`. This creates all five stores. Keep projects and artifacts outside the runtime and mutable data outside the installed skill.
- On resume, read status, the active checkpoint, and relevant effective control entries. Validate existing runtime once before changing it. Use [protocol.md](references/protocol.md) for the relevant commands and [recovery.md](references/recovery.md) only for legacy state or an interrupted write.
- Register managed projects in `control/projects.yaml`. Reuse their existing structure; add only the tree nodes needed to describe chosen outputs. Infer an initial purpose, specification, and acceptance condition from the request when clear.

## Execute

1. Capture each committed outcome as a task in the FIFO queue. Keep routine commands and closely related steps in that task. Apply corrections and clarifications to the active task's checkpoint instead of queuing them as unrelated work.
2. Dispatch when `active` is empty. Resume the top parent on the LIFO stack before taking the oldest queued task.
3. Work toward the active goal. Use `spawn` only for a distinct prerequisite that blocks it; checkpoint the parent first. Queue other committed actions and route ideas through the table below.
4. Checkpoint before interruption, handoff, a risky state change, or a blocking external answer. Ask only when the missing answer prevents correct authorized progress; finish independent work within the active task first. Keep the blocked task active.
5. Verify the outcome, record durable results, and close as `completed`, `cancelled`, or `obsolete`. Closing dispatches the next task. Continue until no unprocessed input or execution work remains, unless the user pauses or an external condition blocks progress. Report that as paused or blocked, never idle.

One active task means one owned goal. Independent reads and checks can run in parallel within it; serialize shared state changes. `taskctl.py spawn` changes task ownership and is not a subagent launch.

## Route information

Update an existing record when it already represents the input.

| Information | Store | Preserve |
|---|---|---|
| Committed action | Task | Goal, necessary steps, checkpoint, completion evidence |
| Durable idea, fact, question, source | Knowledge | One core idea; `common` or `project:<id>` scope; justified relations |
| Effective principle, policy, constraint, assumption, decision | Control | Current statement and rationale; supersede replaced entries |
| Completed work or past decision | History | Append-only event summary and references |
| Processed uncertainty | Unresolved | Question, why unresolved, resolution condition; link a task for committed follow-up |
| Chosen deliverable structure | External project | Role, specification, acceptance condition, artifact links |

Search before likely duplication. Leave uncertain knowledge relations unlinked. Unresolved is not an inbox or a waiting queue; it may remain open when execution is idle.

## Optional plugin feedback

When a concrete problem or improvement idea concerns ADHD itself, follow [feedback.md](references/feedback.md) before archiving it. Keep normal task notes and user-requested deliverables under the existing workflow.

- Ask once at the first useful opportunity whether this user wants no collection, private records, or proposals for sharing. Continue the active task if they decline or do not answer; do not collect feedback by default.
- Keep the user's choice in their private Control records. Reuse only a choice belonging to the current user; the maintainer's preference is not a default for other users.
- Store opted-in feedback in Knowledge. Search for an existing record first. Collecting a candidate does not commit it to the task queue or justify interrupting the active goal.
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
- Preserve history and archive obsolete project scope instead of deleting it.
- Save the runtime in the user's established durable, private workspace. In a temporary execution environment, use the host's supported persistence workflow and verify the save before claiming work will survive a later session. Keep personal runtime records out of the plugin source repository.

Read only the command sections needed in [protocol.md](references/protocol.md). Recovery and schema-1 migration remain available in [recovery.md](references/recovery.md).
