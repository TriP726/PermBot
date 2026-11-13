# PermBot

PermBot is a modular Discord bot for Windows and Python 3.14. It combines local-first music playback, 48 kHz DSP audio, moderation, tickets, recruitment, Eastern Time event scheduling, and Rust Console Edition clan tooling.

## Repository layout

```text
PermBot/
├── main.py                  # Bot entry point and cog loader
├── apply_patch.py           # Atomic patch.json deployment tool
├── requirements.txt        # Python dependencies
├── start.bat                # Windows launcher
├── cogs/                    # Discord command and event modules
├── utils/                   # Shared audio and persistence helpers
├── data/                    # Runtime JSON state (ignored by Git)
├── docs/                    # Operator documentation
└── tools/                   # Maintenance and migration utilities
```

Generated folders such as `.backups/`, `backups/`, `art_cache/`, Python bytecode, and local music libraries are intentionally excluded from source control.

## Requirements

- Windows with **Python 3.14**
- **FFmpeg** and `ffprobe` available on `PATH`
- A Discord application with the **Message Content** and **Server Members** privileged intents enabled
- A bot token stored in an environment variable or local `.env` file

## Setup

From PowerShell in the repository root:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env`, set `DISCORD_TOKEN`, and never commit that file. Start the bot with:

```powershell
.\start.bat
```

or:

```powershell
python main.py
```

## Local music

Place audio under any supported local library directory:

- `music/`
- `songs/`
- `audio/`
- `library/`
- `local_music/`

PermBot searches these files before falling back to online extraction. Supported formats include MP3, FLAC, M4A, OGG, WAV, Opus, AAC, WMA, ALAC, and AIFF.

To preview standardized filenames without changing anything:

```powershell
py -3.14 tools\clean_music.py "D:\Music"
```

Add `--apply` only after reviewing the dry-run output.

## Translation

Discord does not let a slash command be sent as a reply, so translation has three entry points:

- Reply to a message with `!translate <language>` (alias `!tr`), for example `!translate spanish`.
- Right-click a message and choose **Apps → Translate**. The result is private, starts in your Discord language, and has a dropdown to switch.
- `/translate <language> [text]` translates the latest message in the channel, or `text` if you supply it.

Languages can be names or codes (`japanese`, `fr`, `zh-TW`). Translations come from Google Translate's keyless web endpoint, so no API key is needed, but it is an unofficial service that can rate limit or change without notice. The bot needs **Read Message History** in channels where it is used.

### Voice messages

The same three entry points work on voice messages and audio attachments up to 5 minutes long. The audio is transcribed on the bot's own machine with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), which detects the spoken language, and the transcript is then translated. The reply shows both the translation and what was heard.

- `faster-whisper` is installed by `requirements.txt`. Without it the bot still runs and translates text; voice requests get a "not installed" message.
- The speech model (`small`, about 500 MB) is downloaded once from Hugging Face when the bot starts, and is loaded into memory (roughly 1 GB) on the first voice translation.
- Transcription uses the CPU and handles one request at a time. Set `WHISPER_MODEL=base` in `.env` for a lighter, less accurate model, or `medium` for a heavier, more accurate one.

## Runtime data and backups

Guild configuration and user state are written to `data/*.json`. Those files are deployment-specific and ignored by Git. The backup cog writes snapshots to `backups/`; see [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) before restoring one.

## Multi-file patches

All multi-file source updates use one `patch.json` object:

```json
{
  "files": {
    "path/to/file.py": "complete file contents"
  }
}
```

Apply it from the repository root:

```powershell
py -3.14 apply_patch.py
```

The patcher validates repository-local paths, backs up replaced files under `.backups/`, writes files atomically, rolls back a failed application, and removes `patch.json` after success.
