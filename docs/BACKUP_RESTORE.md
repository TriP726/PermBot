# Backup and restore guide

PermBot's backup cog stores ZIP snapshots of the runtime JSON files from `data/` in `backups/`. Automatic backups retain the latest 30 archives.

## Create and protect a backup

1. Use the administrator backup command to create a fresh snapshot.
2. Download the copy sent by the bot and store it off-site.
3. Keep local backup archives separate from the bot token and music library.

A database backup does **not** include source code, credentials, or local audio files.

## Full restore

1. **Stop PermBot completely.** Restoring while it runs can overwrite restored files.
2. Copy the current `data/` directory somewhere safe as a rollback point.
3. Choose the desired archive from `backups/` or an off-site copy.
4. Extract the archive into a temporary directory.
5. Confirm the extracted JSON files are directly inside that temporary directory, not inside another nested folder.
6. Copy the JSON files into PermBot's `data/` directory, replacing the current versions.
7. Start PermBot from the repository root with `start.bat`.
8. Verify moderation settings, tickets, playlists, events, role panels, and temporary voice configuration.

## Restore one data store

To recover only one subsystem, stop the bot and copy only its JSON file from the archive into `data/`. For example, restore `events.json` without replacing `tickets.json`. Keep a copy of the current file first.

## Troubleshooting

- **Bot starts with empty settings:** verify the JSON files are in `PermBot/data/`, not `PermBot/data/data/`.
- **JSON parse error:** restore a different archive or validate the file before starting the bot.
- **Permission or file-in-use error:** stop every running PermBot/Python process, then retry.
- **Commands reference deleted Discord objects:** channel, role, and category IDs may need to be configured again after server changes.
