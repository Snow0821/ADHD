# Maintaining ADHD

- Maintain the plugin at `plugins/adhd`; its `skills/adhd` directory is the authoritative skill source. Do not edit installed copies as a substitute for updating this repository.
- Read that skill and the relevant command reference before changing workflow behavior. Preserve existing runtime data and supported migration paths.
- Keep private runtime records and user artifacts outside this public repository. Tests use temporary workspaces.
- The default knowledge policy stores only relevant transferable knowledge, with evidence and limits. Explicitly opted-in, user-selected database storage may also hold task-relevant private project knowledge within the granted scope. Retain only necessary execution state and operational consent; do not build personal profiles or personal feedback archives.
- Connector-backed database support is optional and knowledge-only. Use the existing authenticated Supabase connector, never embedded credentials or a new MCP server. The database is authoritative; preserve drafts on failure and verify the published snapshot before task/tree references use it. Read `plugins/adhd/skills/adhd/references/database.md` before changing this boundary.
- Shared project-record sync is a separate, explicit opt-in adapter. Keep host authentication injected, verify destination/owner before private payloads, preserve unmentioned fields, and never replay uncertain writes. Read `plugins/adhd/skills/adhd/references/project-records.md`; never export core stores or personal context wholesale.
- Worker execution is an optional, separate SQLite sidecar. Preserve schema-2 core FIFO/LIFO and numeric task/history IDs; only the coordinator changes core tasks. Read `plugins/adhd/skills/adhd/references/execution.md` before changing claims, retry, or host responsibilities. A local job is not proof of an active worker or scheduled reminder.
- Run `python3 scripts/check.py` before committing. For changes to persistence or task ordering, verify observable behavior and retained records.
- Update the plugin's semantic version and `CHANGELOG.md` for a distributed change. Use the plugin-creator update workflow when refreshing a local development installation.
- Keep the instructions and documentation concise. Document installation limitations accurately; successful validation is not evidence of account installation or public catalog registration.
