# Maintaining ADHD

- Maintain the plugin at `plugins/adhd`; its `skills/adhd` directory is the authoritative skill source. Do not edit installed copies as a substitute for updating this repository.
- Read that skill and the relevant command reference before changing workflow behavior. Preserve existing runtime data and supported migration paths.
- Keep private runtime records and user artifacts outside this public repository. Tests use temporary workspaces.
- The default knowledge policy stores only relevant transferable knowledge, with evidence and limits. Retain only necessary execution state and operational consent; do not build personal profiles or personal feedback archives. External databases for personalization are deferred.
- Run `python3 scripts/check.py` before committing. For changes to persistence or task ordering, verify observable behavior and retained records.
- Update the plugin's semantic version and `CHANGELOG.md` for a distributed change. Use the plugin-creator update workflow when refreshing a local development installation.
- Keep the instructions and documentation concise. Document installation limitations accurately; successful validation is not evidence of account installation or public catalog registration.
