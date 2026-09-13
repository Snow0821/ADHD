# ADHD Recovery and Migration

Use only for legacy state or evidence of an interrupted write. Set the verified script and data paths as in [protocol.md](protocol.md#paths-and-runtime).

## Interrupted writes

1. For an interrupted task operation, run task recovery once. It removes closed references, restores orphan open tasks, and dispatches when possible.
2. Validate the workspace.
3. Run tree recovery only for a registered project whose validation reports unreachable node files after an interrupted tree mutation.
4. Revalidate after an actual recovery change. Inspect remaining semantic conflicts; recovery does not settle conflicting roots, dangling references, or multiple parents.

```bash
python3 "$ADHD_SCRIPT_DIR/taskctl.py" --root "$ADHD_ROOT" recover
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" validate
```

When tree recovery is indicated:

```bash
python3 "$ADHD_SCRIPT_DIR/treectl.py" \
  --root "$PROJECT_ROOT" --knowledge-root "$ADHD_ROOT" recover
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" validate
```

A normal conversational pause with a valid checkpoint needs no repair. For missing indexes, graph files, or an unknown schema, preserve the files and diagnose before initialization; an empty replacement can conceal lost relationships or IDs.

## Schema-1 migration

If the legacy `<workspace>/adhd/state.yaml` has no schema version and the schema-2 destination is absent:

```bash
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" migrate \
  /absolute/workspace/adhd \
  --project-root "$PROJECT_ROOT" --project-id project-slug \
  --project-name "Project title" \
  --backup-root /absolute/workspace/migration-backups/adhd-schema-v1
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" validate
```

Migration refuses existing destinations, moves the complete legacy root to the specified backup, converts task state and paths to schema 2, and separates the project tree and artifacts from the runtime. It registers the project and qualifies project references. Legacy waiting tasks become queued tasks with linked unresolved records. Task/history sequence values, knowledge IDs, project IDs, and source files are retained.

Keep the backup until the migrated workspace has been exercised successfully. If migration stops, inspect the backup and partial destinations before retrying.
