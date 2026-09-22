# Runtime data

PermBot creates and updates JSON state in this directory while it runs. The JSON files are intentionally ignored by Git because they contain server-specific IDs, configuration, application records, warnings, and other live state.

- Stop the bot before manually editing or restoring these files.
- Keep off-site copies of important backup ZIPs.
- Do not commit live JSON data or bot tokens.
- See [`../docs/BACKUP_RESTORE.md`](../docs/BACKUP_RESTORE.md) for restoration steps.
