#!/usr/bin/env python3
"""Collect the sound recordings on a Windows computer into one corpus folder.

The script looks for audio files in the places Windows usually keeps them:
Sound Recorder's "Sound Recordings" folder, Desktop, Downloads, Music, Videos,
and the OneDrive copies of those folders. Each unique recording is copied into
recordings/raw/ and listed in recordings/inventory.csv. The originals are only
read, never moved or changed.

    py tools\\collect_recordings.py --dry-run     # preview, writes nothing
    py tools\\collect_recordings.py               # copy + inventory
    py tools\\collect_recordings.py --root E:\\   # also scan an SD card
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import mutagen
except ImportError:  # pragma: no cover - exercised only on a fresh machine
    sys.exit("mutagen is missing. Install it with:  py -m pip install -r requirements.txt")

AUDIO_EXTENSIONS = {
    ".m4a", ".wav", ".mp3", ".wma", ".aac", ".flac", ".ogg", ".opus",
    ".aif", ".aiff", ".amr", ".3gp",
}

# Directory names never descended into (compared case-insensitively).
SKIP_DIRS = {
    "appdata", "node_modules", ".git", "$recycle.bin",
    "system volume information", "__pycache__", ".venv", "venv",
}

# Windows known-folder ids; these follow folder redirection (e.g. to OneDrive).
KNOWN_FOLDERS = {
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}

TAG_DATE_KEYS = ("©day", "TDRC", "date", "DATE")

COLUMNS = [
    "id", "corpus_file", "original_path", "sha256", "size_bytes", "format",
    "duration_s", "sample_rate_hz", "channels", "bitrate_kbps", "recorded_at",
    "date_source", "collected_at", "duplicate_of", "error",
]

REPO_ROOT = Path(__file__).resolve().parent.parent


def known_folder(guid: str) -> Path | None:
    """Resolve a Windows known folder, or None when not on Windows."""
    if os.name != "nt":
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder_id = GUID.from_buffer_copy(uuid.UUID(guid).bytes_le)
    path_ptr = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr))
    if result != 0:
        return None
    try:
        return Path(path_ptr.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)


def default_roots() -> list[Path]:
    """The folders where recordings usually end up, Sound Recordings first."""
    home = Path.home()
    bases = [home]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            bases.append(Path(os.environ[var]))

    folders = [known_folder(guid) for guid in KNOWN_FOLDERS.values()]
    folders += [base / name for base in bases for name in KNOWN_FOLDERS]
    folders = [f for f in folders if f is not None]

    documents = [f for f in folders if f.name.lower() == "documents"]
    roots = [d / "Sound Recordings" for d in documents]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        packages = Path(local_appdata, "Packages")
        roots += sorted(packages.glob("Microsoft.WindowsSoundRecorder_*/LocalState"))
    roots += folders
    return unique_existing(roots)


def unique_existing(paths: list[Path]) -> list[Path]:
    seen, result = set(), []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and path.is_dir():
            seen.add(key)
            result.append(path)
    return result


def find_audio(roots: list[Path], exclude: Path):
    """Yield every audio file below the roots once, skipping `exclude`."""
    excluded = os.path.normcase(os.path.abspath(exclude))
    seen = set()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if d.lower() not in SKIP_DIRS
                and os.path.normcase(os.path.abspath(os.path.join(dirpath, d))) != excluded
            )
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                path = Path(dirpath, name)
                key = os.path.normcase(os.path.abspath(path))
                if key not in seen:
                    seen.add(key)
                    yield path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_tag_date(value) -> datetime | None:
    """Accept tag dates with at least day precision; a bare year is too coarse."""
    if isinstance(value, list):
        value = value[0] if value else None
    text = str(value).strip() if value is not None else ""
    if len(text) < 10:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def read_audio_info(path: Path) -> dict:
    """Technical metadata and embedded date; raises if mutagen can't parse it."""
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("format not recognised by mutagen")
    info = audio.info
    bitrate = getattr(info, "bitrate", None)
    tag_date = None
    if audio.tags is not None:
        for key in TAG_DATE_KEYS:
            try:
                tag_date = parse_tag_date(audio.tags.get(key))
            except (KeyError, ValueError, TypeError):
                tag_date = None
            if tag_date:
                break
    return {
        "duration_s": getattr(info, "length", None),
        "sample_rate_hz": getattr(info, "sample_rate", None),
        "channels": getattr(info, "channels", None),
        "bitrate_kbps": round(bitrate / 1000) if bitrate else None,
        "tag_date": tag_date,
    }


def file_date(path: Path) -> tuple[datetime, str]:
    """Earliest file timestamp: a copied file keeps its original modified time."""
    st = path.stat()
    created = getattr(st, "st_birthtime", None)
    if created is None and os.name == "nt":
        created = st.st_ctime  # creation time on Windows before Python 3.12
    if created is not None and created < st.st_mtime:
        return datetime.fromtimestamp(created), "file_created"
    return datetime.fromtimestamp(st.st_mtime), "file_modified"


def display_path(path: Path) -> str:
    """Absolute path with the profile folder shown as ~, so no user name leaks."""
    path = Path(os.path.abspath(path))
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def corpus_name(recorded_at: datetime, original: Path, sha: str, raw_dir: Path,
                taken: set[str]) -> tuple[str, bool]:
    """Pick a file name in raw/; returns (name, already_there_with_same_bytes)."""
    stem = re.sub(r"[^\w.-]+", "-", original.stem).strip("-.").lower() or "recording"
    base = f"{recorded_at:%Y-%m-%d_%H%M%S}_{stem}"
    ext = original.suffix.lower()
    for name in (f"{base}{ext}", f"{base}_{sha[:8]}{ext}"):
        target = raw_dir / name
        if name.lower() in taken:
            continue
        if not target.exists():
            return name, False
        if sha256_of(target) == sha:  # left behind by an interrupted run
            return name, True
    raise FileExistsError(f"no free name for {original} in {raw_dir}")


def load_inventory(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_inventory(csv_path: Path, rows: list[dict]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, csv_path)


def fmt(value, decimals: int | None = None) -> str:
    if value is None:
        return ""
    if decimals is not None:
        return f"{value:.{decimals}f}"
    return str(value)


def hms(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def collect(roots: list[Path], dest: Path, min_seconds: float, dry_run: bool) -> dict:
    raw_dir = dest / "raw"
    csv_path = dest / "inventory.csv"
    rows = load_inventory(csv_path)
    known_hash = {r["sha256"]: r["id"] for r in rows if not r["duplicate_of"]}
    known_entry = {(r["original_path"], r["sha256"]) for r in rows}
    taken = {r["corpus_file"].lower() for r in rows if r["corpus_file"]}
    next_id = max((int(r["id"]) for r in rows), default=0) + 1
    stats = dict(found=0, new=0, duplicates=0, already=0, too_short=0,
                 errors=0, unreadable=0, seconds=0.0)
    new_rows: list[dict] = []

    try:
        for path in find_audio(roots, exclude=dest):
            stats["found"] += 1
            shown = display_path(path)
            try:
                sha = sha256_of(path)
                size = path.stat().st_size
            except OSError as exc:
                stats["unreadable"] += 1
                print(f"  ! cannot read {shown}: {exc}", file=sys.stderr)
                continue
            if (shown, sha) in known_entry:
                stats["already"] += 1
                continue

            try:
                info, error = read_audio_info(path), ""
            except Exception as exc:  # mutagen raises many types on bad files
                info, error = {}, f"{type(exc).__name__}: {exc}"
            duration = info.get("duration_s")
            if duration is not None and duration < min_seconds:
                stats["too_short"] += 1
                continue

            if info.get("tag_date"):
                recorded_at, date_source = info["tag_date"], "tag"
            else:
                recorded_at, date_source = file_date(path)

            row = {
                "id": str(next_id),
                "corpus_file": "",
                "original_path": shown,
                "sha256": sha,
                "size_bytes": str(size),
                "format": path.suffix.lower().lstrip("."),
                "duration_s": fmt(duration, 3),
                "sample_rate_hz": fmt(info.get("sample_rate_hz")),
                "channels": fmt(info.get("channels")),
                "bitrate_kbps": fmt(info.get("bitrate_kbps")),
                "recorded_at": recorded_at.isoformat(timespec="seconds"),
                "date_source": date_source,
                "collected_at": datetime.now().isoformat(timespec="seconds"),
                "duplicate_of": known_hash.get(sha, ""),
                "error": error,
            }

            if row["duplicate_of"]:
                stats["duplicates"] += 1
                print(f"  = {shown}  (same as #{row['duplicate_of']})")
            else:
                name, present = corpus_name(recorded_at, path, sha, raw_dir, taken)
                if not dry_run and not present:
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, raw_dir / name)
                row["corpus_file"] = f"raw/{name}"
                taken.add(name.lower())
                known_hash[sha] = row["id"]
                stats["new"] += 1
                stats["seconds"] += duration or 0.0
                length = hms(duration) if duration is not None else "?:??:??"
                print(f"  + {shown}  [{length}]  -> {row['corpus_file']}")
            if error:
                stats["errors"] += 1
                print(f"    metadata not readable: {error}")

            known_entry.add((shown, sha))
            new_rows.append(row)
            next_id += 1
    finally:
        if new_rows and not dry_run:
            write_inventory(csv_path, rows + new_rows)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy the sound recordings on this computer into a corpus "
                    "folder and list them in inventory.csv.")
    parser.add_argument("--root", action="append", type=Path, default=[],
                        metavar="PATH", help="extra folder to scan (repeatable)")
    parser.add_argument("--only-roots", action="store_true",
                        help="scan only the --root folders, not the Windows defaults")
    parser.add_argument("--dest", type=Path, default=REPO_ROOT / "recordings",
                        help="corpus folder (default: recordings/ in this repo)")
    parser.add_argument("--min-seconds", type=float, default=1.0,
                        help="ignore clips shorter than this (default: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be collected without copying or writing")
    args = parser.parse_args(argv)
    # A redirected Windows console may be cp1252; don't crash on "Ayşe.m4a".
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if args.only_roots and not args.root:
        parser.error("--only-roots needs at least one --root")
    missing = [r for r in args.root if not r.is_dir()]
    if missing:
        parser.error(f"not a folder: {missing[0]}")
    roots = unique_existing(args.root if args.only_roots else default_roots() + args.root)
    if not roots:
        print("No recording folders found; name one with --root PATH.")
        return 1

    print("Scanning:")
    for root in roots:
        print(f"  {display_path(root)}")
    print("Dry run: nothing will be copied or written.\n" if args.dry_run else "")

    stats = collect(roots, args.dest, args.min_seconds, args.dry_run)

    print(f"\nFound {stats['found']} audio files: {stats['new']} new "
          f"({hms(stats['seconds'])}), {stats['duplicates']} duplicates, "
          f"{stats['already']} already in the inventory, "
          f"{stats['too_short']} shorter than {args.min_seconds:g} s.")
    if stats["errors"]:
        print(f"{stats['errors']} files were copied but their metadata could not be read.")
    if stats["unreadable"]:
        print(f"{stats['unreadable']} files could not be read at all; see the warnings above.")
    if not args.dry_run and stats["new"] + stats["duplicates"]:
        print(f"Inventory: {args.dest / 'inventory.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
