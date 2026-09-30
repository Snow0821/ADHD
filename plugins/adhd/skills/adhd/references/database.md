# Connector-backed Knowledge

Optional, Knowledge-only persistence through an existing authenticated Supabase connector. File mode remains the default. Task, Control, History, Unresolved, project trees, and artifacts keep their existing locations. Resolve `ADHD_SCRIPT_DIR` and `ADHD_ROOT` as in [protocol.md](protocol.md#paths-and-runtime).

## Boundary and setup

- Obtain explicit opt-in for the selected database and intended information scope before sending knowledge there. Task-relevant private project knowledge may be included within that scope; this is not blanket consent for personal profiles, sensitive-data collection, or public sharing.
- Use the installed, authenticated Supabase connector. These scripts only prepare SQL tool arguments and validate responses: no browser credentials, stored API keys, new MCP server, or public data API.
- Verify the selected project through a read-only connector call. Review `assets/knowledge_schema.sql` relative to the skill directory and apply it through the connector only with the required authorization. It defines private `adhd_knowledge` graphs, nodes, edges, and the `export_graph` / `replace_graph` functions. Do not expose this schema through a public API or grant browser roles access.
- Store destination configuration and response files in the private runtime/workspace, outside the plugin source. Examples use placeholders; do not copy a user's project ID or knowledge into shared docs or tests.

The database holds the authoritative whole-graph snapshot. A local graph is either a verified published cache or an edit draft. Publication checks the expected revision and uses a persisted operation UUID for idempotent retries; it is not a field-level merge. This small-graph bridge transfers the whole graph per round trip; it is not an incremental sync service.

## Connect or migrate without losing originals

1. Validate the existing workspace and preserve a complete private backup of its runtime before configuration. Keep the original installed skill too if it is being replaced. Do not delete either after a single successful call.
2. Check the intended destination project and graph name. To read an existing remote graph, use a fresh runtime or one with no local knowledge to replace. Normal configuration refuses an existing populated local graph; archive its original runtime or deliberately migrate it instead.
3. For a new remote graph only, `--new` retains the existing local snapshot as the initial draft at revision 0. Verify that no graph already exists under that name. It does not authorize overwriting an existing remote graph.

Register corresponding local projects before importing `project:<id>` scopes. Imports do not invent project registrations or bypass the existing scope validator. SQL preserves literal bodies; the file bridge rejects a snapshot before changing the cache if its body cannot round-trip through the existing Markdown format. JSON numeric notation may change without changing the represented value.

```bash
# Connect an empty local knowledge store to an existing remote graph.
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" configure \
  --project-id PROJECT_ID --graph-id GRAPH_NAME

# Alternative: deliberately migrate a backed-up local graph into a new remote graph.
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" configure \
  --project-id PROJECT_ID --graph-id GRAPH_NAME --new
```

For normal connection, complete the read round trip below before editing. For `--new`, the initial draft is already open: run `read-query` through the connector to check that the remote graph is absent (`snapshot` is null; do not pass this to `apply-read`), then publish with the write round trip and read it back. The revision check also rejects a graph created concurrently. Compare IDs, content, relations, and existing task/tree references with the retained originals. Keep the backup until the migrated workspace has been exercised successfully. Do not change knowledge IDs to resolve a migration problem.

## Read, edit, publish, verify

Refresh at session resume and before relying on database knowledge. Keep responses in the private runtime. The apply commands accept the JSON value returned by the SQL function, without the connector or SQL row wrapper: extract the `snapshot` column for reads and the `result` column for writes, preserving every field. Retain the original response as evidence; never construct a successful response yourself.

```bash
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" read-query
# Pass the emitted arguments to the authenticated Supabase SQL tool.
# Save its unwrapped snapshot-column JSON value to "$ADHD_ROOT/db-read.json".
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" \
  apply-read "$ADHD_ROOT/db-read.json"

python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" begin-edit
# Use graphctl.py create / update / link and other graph edits from protocol.md.
python3 "$ADHD_SCRIPT_DIR/graphctl.py" --root "$ADHD_ROOT" validate
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" write-query
# Pass the exact emitted arguments to the same authenticated Supabase SQL tool.
# Save its unwrapped result-column JSON value to "$ADHD_ROOT/db-write.json".
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" \
  apply-write "$ADHD_ROOT/db-write.json"

# Required: repeat read-query → connector read → apply-read to verify publication.
```

`graphctl.py` edits in database mode require `begin-edit`; they do not publish by themselves. `write-query` freezes a pending snapshot and operation UUID. Retry its exact SQL/tool arguments, preserving that UUID. `apply-write` accepts a confirmed response, but only the following fresh `apply-read` restores a verified published cache.

Task/tree knowledge references require that clean published cache. A draft, pending write, or cache left stale after an interrupted operation cannot satisfy those references. Keep unrelated local work moving if it has no dependency on that knowledge. Local validation alone does not establish database availability or publication.

## Failures and conflicts

- Connector unavailable or response invalid: stop database-dependent work and retain the draft/pending files. Do not fall back to local file authority or claim a write succeeded.
- Write outcome uncertain: request `write-query` again and retry the identical pending operation. Do not start a new UUID or re-edit the frozen snapshot to guess whether the first write committed.
- Revision conflict: preserve the rejected draft, retrieve the latest remote snapshot, and deliberately reconcile it before another publication. Normal `apply-read` refuses to replace a draft. After saving the fresh read response, use:

```bash
python3 "$ADHD_SCRIPT_DIR/graphdb.py" --root "$ADHD_ROOT" \
  apply-read "$ADHD_ROOT/db-read.json" --preserve-draft
```

This archives the local draft in the private runtime before accepting the remote graph. Inspect the retained draft, begin a new edit from the current revision, and apply only the intended changes. There is no automatic conflict merge or force overwrite. Keep archives until reconciliation and task/tree references are verified.

For an interrupted local cache import, preserve files and run `graphdb.py --root "$ADHD_ROOT" recover` to finish its retained journal. Then use a fresh connector read to re-establish a current cache; use the preserve-draft route if it would replace pending local work. Recovery replays the saved import and does not contact the database. Do not treat local knowledge or task recovery as evidence that the database graph is current.

## Checks and release order

```bash
# Repository package contract and regression tests, using temporary workspaces.
python3 scripts/check.py

# After migration or a completed connector round trip, with a clean published cache.
python3 "$ADHD_SCRIPT_DIR/graphctl.py" --root "$ADHD_ROOT" validate
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" validate
```

Local tests do not prove a production schema is installed or a remote snapshot was published. Verify those through the selected connector and compare the read-back graph. Update and verify the canonical GitHub release first; synchronize an installed personal copy separately, retain its original, and verify the installed version before claiming it is updated.
