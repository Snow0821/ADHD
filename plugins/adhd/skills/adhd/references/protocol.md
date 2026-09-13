# ADHD Commands

Read the section needed for the current operation. Use each command's `--help` for additional options.

## Contents

- [Paths and runtime](#paths-and-runtime)
- [Initialize and register](#initialize-and-register)
- [Task operations](#task-operations)
- [Knowledge operations](#knowledge-operations)
- [Control and unresolved](#control-and-unresolved)
- [Project operations](#project-operations)
- [Validation and completion](#validation-and-completion)

## Paths and runtime

Use Python 3.10+ with PyYAML on a POSIX host (Linux, macOS, or WSL; locking uses `fcntl`). Resolve the scripts from the active `SKILL.md`, independently of the current directory.

Always pass verified roots explicitly:

```bash
ADHD_SCRIPT_DIR=/absolute/skill/path/scripts
ADHD_ROOT=/absolute/workspace/.adhd
PROJECT_ROOT=/absolute/workspace/projects/project-slug
```

The runtime owns `state.yaml`, `task/{open,closed}`, `knowledge`, `control`, `history`, and `unresolved`. Control entries and unresolved items have index files. The project registry is `control/projects.yaml`; each project owns `project/tree.yaml`, `project/nodes`, and its artifacts.

Task, knowledge, and control mutations share a workspace lock. Tree mutations use a project lock. Individual file replacement is atomic; a multi-file operation can still be interrupted. Serialize mutations and use [recovery.md](recovery.md) when necessary.

## Initialize and register

For a new runtime, one command creates all five stores, including the workspace knowledge graph:

```bash
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" init
```

Repeated initialization preserves existing metadata and records. If persisted indexes or graph files have disappeared, inspect the damage before initializing; do not recreate empty metadata over existing records.

Use the explicit user root, otherwise the established runtime or nearest ancestor's schema-2 `.adhd`. Ask about competing roots only when the context cannot distinguish them. For a legacy `adhd/state.yaml` without schema version, use [migration](recovery.md#schema-1-migration).

For a chosen project, initialize a minimal tree outside the runtime, then register its path relative to the workspace:

```bash
python3 "$ADHD_SCRIPT_DIR/treectl.py" \
  --root "$PROJECT_ROOT" --knowledge-root "$ADHD_ROOT" \
  init "Project title" --project-id project-slug --profile generic \
  --purpose "Why it exists" --spec "What it contains" \
  --acceptance "Observable completion condition"

python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" \
  register-project project-slug projects/project-slug --name "Project title"
```

Profiles: `generic`, `paper`, `slides`, `web`, `game`, `plugin`. Existing project content can stay where it is; registration does not require reorganizing it into a template.

## Task operations

Create a committed outcome and dispatch it when no task is active:

```bash
python3 "$ADHD_SCRIPT_DIR/taskctl.py" --root "$ADHD_ROOT" create "Task title" \
  --goal "Observable goal" --todo "Necessary step" \
  --knowledge k000000 --project project-slug:p000000
python3 "$ADHD_SCRIPT_DIR/taskctl.py" --root "$ADHD_ROOT" dispatch
```

Omit references that do not exist. Knowledge IDs are workspace-wide; project references resolve through the registry. Dispatch is idempotent while a valid active task exists.

Checkpoint for a handoff, changed requirement, or blocked answer:

```bash
python3 "$ADHD_SCRIPT_DIR/taskctl.py" --root "$ADHD_ROOT" checkpoint \
  --current "Verified state and current requirements" \
  --next "Next action" --resume "Relevant files and decisions"
```

For a distinct strict prerequisite, supply all three parent checkpoint fields together:

```bash
python3 "$ADHD_SCRIPT_DIR/taskctl.py" --root "$ADHD_ROOT" spawn "Prerequisite" \
  --goal "Condition needed by the parent" \
  --parent-current "Completed parent work" \
  --parent-next "Next step after this child" \
  --parent-resume "Context needed to resume"
```

Close after verification; it resumes the stack before the FIFO queue:

```bash
python3 "$ADHD_SCRIPT_DIR/taskctl.py" --root "$ADHD_ROOT" close \
  --outcome completed --summary "Result and verification" \
  --affect project-slug:p000000 --decision "Durable decision" \
  --artifact "projects/project-slug/output"
```

Outcomes: `completed`, `cancelled`, `obsolete`. Omit unused fields; use `--created-task` for tasks created during this work. A close decision is a historical occurrence; use control for a newly effective rule.

## Knowledge operations

```bash
python3 "$ADHD_SCRIPT_DIR/graphctl.py" --root "$ADHD_ROOT" search "query" --limit 5
python3 "$ADHD_SCRIPT_DIR/graphctl.py" --root "$ADHD_ROOT" create "Idea title" \
  --kind idea --scope project:project-slug \
  --core "One durable idea" --content "Context" --source "Origin"
python3 "$ADHD_SCRIPT_DIR/graphctl.py" --root "$ADHD_ROOT" link \
  k000001 supports k000000 --reason "Why this relation holds"
python3 "$ADHD_SCRIPT_DIR/graphctl.py" --root "$ADHD_ROOT" update k000001 \
  --core "Revised idea"
```

| Field | Values |
|---|---|
| ID | `k` + six digits |
| Kind | `concept`, `claim`, `idea`, `question`, `source` |
| Status | `active`, `superseded`, `archived` |
| Scope | `common` or `project:<project-id>` |
| Relation | `related_to`, `part_of`, `depends_on`, `supports`, `contradicts`, `extends`, `supersedes` |

`related_to` and `contradicts` are symmetric; the rest are directional. Use `show`, `neighbors <id> --hops 2`, or `status` to read relevant context.

## Control and unresolved

Create effective control; use `--supersedes` only when replacing a prior entry:

```bash
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" control-create \
  "Decision title" --kind decision --statement "Effective decision" \
  --rationale "Reason" --source task:000000 --supersedes c000000
```

Kinds: `principle`, `policy`, `constraint`, `assumption`, `decision`. IDs are `c` + six digits.

Record processed uncertainty; omit `--task` when there is no committed executable follow-up:

```bash
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" unresolved-create \
  "Question" --domain knowledge --question "What is unknown?" \
  --reason "Why unresolved" --needed "Resolution condition" \
  --ref knowledge:k000000 --task 12
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" unresolved-resolve \
  u000000 --resolution "What settled it"
```

Domains: `knowledge`, `control`. References: `knowledge:k000000`, `control:c000000`, `project:<project-id>:p000000`. IDs are `u` + six digits; resolution retains the record.

## Project operations

Use `--knowledge-root` whenever the tree contains knowledge references.

```bash
python3 "$ADHD_SCRIPT_DIR/treectl.py" \
  --root "$PROJECT_ROOT" --knowledge-root "$ADHD_ROOT" \
  add p000000 "Chosen component" \
  --purpose "Role" --spec "Contents" --acceptance "Completion condition" \
  --maturity defined --knowledge k000000
python3 "$ADHD_SCRIPT_DIR/treectl.py" \
  --root "$PROJECT_ROOT" --knowledge-root "$ADHD_ROOT" \
  update p000001 --maturity realized --artifact "path/to/output"
```

IDs are project-local `p` + six digits. Each active node has one parent except the root. Ordered siblings define outline order. Maturities: `stub`, `defined`, `realized`, `verified`. Artifact paths must exist and be relative to the project root.

Use `outline` or `flatten` to inspect, `move <id> <parent> --index <n>` to reorder, `archive <id>` to retire scope, and `restore <id> <parent> --index <n>` to restore it.

## Validation and completion

Check acceptance-relevant outputs and classify meaningful new inputs before closing. Validate the affected store after a batch of mutations: `taskctl.py validate`, `graphctl.py validate`, or `treectl.py validate`. Use full workspace validation for initial resume, cross-store changes, migration, or recovery:

```bash
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" validate
python3 "$ADHD_SCRIPT_DIR/adhdctl.py" --root "$ADHD_ROOT" status
```

After closing the task, inspect status and continue if execution work remains. Do not repeat unchanged checks or reopen unrelated unlinked knowledge merely to make status look empty.
