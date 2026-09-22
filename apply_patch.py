"""Apply one atomic, repository-local multi-file patch from patch.json."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PATCH_FILE = ROOT / "patch.json"
BACKUP_ROOT = ROOT / ".backups"


def _fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _target_path(relative_name: str) -> Path:
    raw = Path(relative_name)
    if raw.is_absolute() or not relative_name.strip():
        raise ValueError(f"Patch path must be relative: {relative_name!r}")

    target = (ROOT / raw).resolve(strict=False)
    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"Patch path leaves the repository: {relative_name!r}") from exc

    if target == ROOT or target == PATCH_FILE or ROOT / ".git" in target.parents or target == ROOT / ".git":
        raise ValueError(f"Patch path is protected: {relative_name!r}")
    return target


def _load_patch() -> dict[Path, str]:
    if not PATCH_FILE.is_file():
        _fail(f"{PATCH_FILE.name} was not found beside apply_patch.py")

    try:
        payload: Any = json.loads(PATCH_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"Could not parse {PATCH_FILE.name}: {exc}")

    raw_files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(raw_files, dict) or not raw_files:
        _fail("patch.json must contain a non-empty 'files' object")

    parsed: dict[Path, str] = {}
    for name, content in raw_files.items():
        if not isinstance(name, str) or not isinstance(content, str):
            _fail("Every patch entry must map a string path to string content")
        try:
            target = _target_path(name)
        except ValueError as exc:
            _fail(str(exc))
        if target in parsed:
            _fail(f"Duplicate normalized patch path: {name}")
        parsed[target] = content
    return parsed


def _atomic_write(target: Path, content: str, previous_mode: int | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    patch = _load_patch()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = BACKUP_ROOT / f"patch_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    existing: dict[Path, Path] = {}
    created: list[Path] = []
    applied: list[Path] = []

    try:
        for target in patch:
            if target.exists():
                if not target.is_file():
                    raise IsADirectoryError(f"Patch target is not a regular file: {target.relative_to(ROOT)}")
                backup = backup_dir / target.relative_to(ROOT)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                existing[target] = backup
            else:
                created.append(target)

        for target, content in patch.items():
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
            _atomic_write(target, content, mode)
            applied.append(target)
            action = "UPDATED" if target in existing else "CREATED"
            print(f"[{action}] {target.relative_to(ROOT)}")
    except Exception as exc:
        print(f"ERROR: Patch failed; rolling back: {exc}", file=sys.stderr)
        for target in reversed(applied):
            try:
                if target in existing:
                    shutil.copy2(existing[target], target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as rollback_exc:
                print(f"ERROR: Could not roll back {target}: {rollback_exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    PATCH_FILE.unlink()
    print(
        f"Patch complete: {len(existing)} updated, {len(created)} created. "
        f"Backups: {backup_dir.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
