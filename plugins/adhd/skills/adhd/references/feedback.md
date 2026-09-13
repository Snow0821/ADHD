# Optional generalizable feedback

Use this procedure for observations about the ADHD plugin, applying the [knowledge boundary](../SKILL.md#knowledge-boundary). Do not run an unsolicited feedback review after every task. A user's normal task output and necessary diagnostics remain part of that task.

## Recording choice

At the first useful discovery, ask whether the current user wants generalizable improvement notes:

> ADHD의 문제나 개선 아이디어를 다른 사용자에게도 도움이 되는 형태로 정리할까요?

| Choice | Mode | Behavior |
| --- | --- | --- |
| 정리하지 않음 | `off` | Do not archive plugin feedback. |
| 일반화해서 정리 | `general` | Record transferable feedback without personal context. |
| No answer / later | `deferred` | Continue the task without collecting or repeating the question. |

Honor an explicit applicable choice already given by the current user. Installing the plugin or approving development is not consent to feedback collection or publication. Never archive a declined observation under another store to bypass the choice.

When the established runtime supports attributing consent to the current user, use the existing Control helper with source `adhd:feedback-preference` and a statement `mode: off|general|deferred`. Preserve an existing verified user qualifier when needed to distinguish participants. Store only the mode and its authorization context; do not create a user profile or require a new account for feedback. If a choice cannot be reliably attributed, use the current conversation's answer without inventing a persistent save. A repository owner's or another user's choice does not apply.

Use `--supersedes` only for that user's prior preference when they change it. Applicable legacy `off`/`deferred` choices still prevent collection; legacy `private`/`share` choices do not authorize the new mode or public posting. Do not create new personal archives or delete existing user records as part of this policy update. Switching off stops future feedback writes; removal of past records is a separate requested action.

## Generalize before recording

Keep a record only when it describes a reusable problem, method, or proposal. Exclude personal preferences, private conversations, identities, account data, and person-specific project details. Removing identifiers alone is insufficient: the lesson must stand on its own. Mark suspected causes and unverified reports explicitly, and retain the limits of the evidence.

Search for a matching record before writing. Use `graphctl.py` from [protocol.md](protocol.md#knowledge-operations), with kind `claim` for a supported observation or `idea` for a proposal and source `adhd:feedback`. Include a verified plugin version/commit when relevant; do not infer an installed version from the repository's latest version. Use an existing appropriate scope, without creating a project just for feedback.

Keep the record compact: title/type, relevant conditions, expected versus observed behavior or proposed benefit, generalizable evidence, and a next verification step if known. A generalized reproduction can replace private input; do not attach the original personal evidence. If there is no useful transferable content, do not create a record. Recording a candidate does not commit an execution task.

## Public proposal

Generalizable content is not automatically public. Prepare a concise issue title/body and show the user the destination (`Snow0821/ADHD` GitHub Issues) and that the proposal will be publicly visible. Publish only with authorization for the reviewed content and destination; do not ask again when that exact authorization is already present.

Use the connected GitHub capability to search open and closed issues with public-safe terms. Prefer updating a matching issue over duplicating it, and include the intended issue/comment destination in the review. If the destination changes after review, resolve that change before posting. Do not send feedback through another service as a fallback.

On a failed or unavailable submission, retain only an already-authorized generalized note with the actual pending reason. Verify the returned issue URL before reporting success, and check for an existing submission before retrying an ambiguous write. Link the issue from Knowledge; the shared proposal's full discussion belongs in the issue, without a mirrored personal archive.

## Adoption

Continue the current goal after capture. A blocking task is appropriate only when the defect prevents that authorized goal. Necessary task diagnostics and fixes can proceed even when feedback collection is off.

Create or reuse an execution task when an improvement is adopted, linked to the issue or knowledge node. Record an adopted general rule in Control only when effective. Retain the fix commit, verification, and affected version in the issue and History, distinguishing source validation from verified installed behavior. Deferred proposals may remain open with a reconsideration condition; they are not committed work by default.
