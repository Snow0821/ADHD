# Optional ADHD feedback

Use this procedure for observations about the ADHD plugin. It does not change the handling of ordinary project knowledge, required task diagnostics, or an explicitly requested deliverable. Do not run an unsolicited feedback review after every task.

## User preference

Use an established durable, private runtime and a stable user key verified from the active host/account context. Do not use the repository owner, a display name, or a different participant as evidence of the current user's identity. Do not move private feedback into a shared runtime; if a private destination is unavailable, continue the main task and arrange one only when collection is requested.

Find active Control entries whose `sources` include both `adhd:feedback-preference` and `user:<verified-key>`. Read the statement, not just the title. Conflicting choices or an unverified user are not collection permission.

When there is no applicable choice, ask at the first meaningful discovery:

> ADHD 사용 중 발견한 문제나 개선 아이디어를 모아둘까요?

| Choice | Stored mode | Behavior |
| --- | --- | --- |
| 기록 안 함 | `off` | Do not create or append plugin-feedback records. |
| 개인 보관 | `private` | Record feedback in this user's private Knowledge. |
| 공유 제안 | `share` | Keep private records and prepare proposals for separate public review. |
| No answer / later | `deferred` | Do not collect; continue the task without repeating the question. |

An explicit existing user preference can satisfy this choice without another question. Approval to implement this feature, installation of the plugin, or permission to record privately does not select public posting.

Save only the preference when asking is dismissed; do not save the discovered problem or idea as an unanswered question, draft, task, or hidden feedback log. For a known user, `deferred` prevents repeated prompts until the user reopens the setting. If identity or private storage is unavailable, remember the dismissal for this conversation and do not invent a durable save.

Use the existing Control helper, with a statement containing `mode: off|private|share|deferred` and the user's actual choice as the rationale:

```bash
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" control-create \
  "ADHD feedback preference" --kind policy \
  --statement "mode: private" --rationale "User chose private collection" \
  --source adhd:feedback-preference --source "user:$ADHD_USER_KEY"
```

The command is an example, not a default to execute. Set `ADHD_USER_KEY` only from verified context. When the user changes the setting, create the new entry with `--supersedes` referring only to their prior preference. Switching off stops future feedback writes; it does not delete existing records or public issues. Handle removal only when requested, and do not claim a public copy was removed by changing a local setting.

## Private capture

Before every feedback write, check the current user's effective preference. Search their existing feedback before creating a node; update an existing occurrence when it describes the same underlying problem or proposal. Keep private feedback out of public repository commits and issue search queries.

Use `graphctl.py` as described in [protocol.md](protocol.md#knowledge-operations): `claim` for an observed problem and `idea` for a proposal. Add sources `adhd:feedback`, `user:<verified-key>`, and the observed plugin version or commit. Use an existing registered ADHD project scope when available, otherwise `common` in the user's private runtime. Do not create a project tree just to record feedback.

Keep one compact record:

| Field | Content |
| --- | --- |
| Title and type | One problem or idea; mark an unverified report as unverified. |
| Context | Situation and version/environment relevant to the observation. |
| Observation | Expected versus actual behavior for a problem; friction and expected benefit for an idea. |
| Evidence | Minimal example or useful source; separate facts from suspected causes. |
| Next check | A useful verification or reconsideration condition, if known. |

Use the latest verified observation; do not infer the installed version from GitHub's latest release. Recording an idea does not require knowing the solution. Do not create placeholder evidence or a task merely because a candidate exists.

## Public proposal

For `share`, prepare a concise public proposal and show its title, body, and destination (`Snow0821/ADHD` GitHub Issues) to the user. Use generalized situations and minimal reproduction examples; omit private conversations, names, paths, account identifiers, and secrets unless the user specifically requests appropriate public information. Explain that the proposal will be publicly visible.

Publish only after the user has authorized that reviewed proposal and destination. If the exact proposal is already approved, proceed without another confirmation. A `share` preference alone is permission to prepare proposals, not unattended publication. If the user changes their choice or withdraws approval before posting, use their current instruction.

Use the connected GitHub capability to inspect existing open and closed issues using only public-safe terms. Add new evidence to a matching issue instead of creating a duplicate, but include the intended issue/comment destination in the public review. If an issue appears after review, prepare the matching update before posting. Do not send feedback through email or another service as a fallback.

If GitHub is unavailable or posting fails, retain the authorized private record and mark submission as pending with the actual reason. Do not report a submission until its returned URL is verified. After an ambiguous failure, check whether the proposal was created before retrying; do not repeat an uncertain write blindly.

After publication, link the verified issue URL from the private record. GitHub owns the shared proposal's subsequent public discussion; retain private evidence only for its distinct private purpose rather than mirroring the whole issue.

## Adoption and closure

Continue the user's current goal after capture. Use a blocking task only when the defect prevents that authorized goal; feedback preferences do not prevent necessary task diagnostics or fixes.

When an improvement is actually adopted, create or reuse an execution task linked to its issue or knowledge node. Record a generally applicable adopted rule in Control only when it becomes effective. At completion, retain the fix commit, verification, and affected version in the issue and History. Mark source-only validation separately from a verified installed-plugin result. Deferred proposals can remain open with a reconsideration condition; they are not an execution backlog unless action was committed.
