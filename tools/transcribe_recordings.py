#!/usr/bin/env python3
"""Transcribe the collected recordings into a text corpus, locally.

Run this after collect_recordings.py. It reads recordings/inventory.csv, runs
Whisper (via faster-whisper) on this computer for every collected recording
that has no transcript yet, and writes two files per recording into
recordings/transcripts/:

    <name>.txt   plain text, one speech segment per line (for AntConc etc.)
    <name>.srt   the same text with timestamps (opens in any video/subtitle tool)

The inventory gets the detected language, the transcript path and the model
used. The audio never leaves this computer; only the model is downloaded once.

    py tools\\transcribe_recordings.py --limit 1       # try one recording first
    py tools\\transcribe_recordings.py                 # everything not yet transcribed
    py tools\\transcribe_recordings.py --language de   # skip language detection
    py tools\\transcribe_recordings.py --model large-v3-turbo --force   # redo, better model
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from collect_recordings import REPO_ROOT, hms, load_inventory, write_inventory

MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]


def load_model(name: str, device: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper is missing. Install it with:  py -m pip install -r requirements.txt")
    print(f"Loading Whisper model '{name}' on {device} "
          "(the first time, this downloads it)...")
    try:
        return WhisperModel(name, device=device,
                            compute_type="int8" if device == "cpu" else "float16")
    except Exception as exc:
        sys.exit(f"Could not load the Whisper model: {type(exc).__name__}: {exc}\n"
                 "The first run downloads it from huggingface.co, so it needs internet. "
                 "With --device cuda, also check that CUDA 12 and cuDNN 9 are installed.")


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_one(model, audio: Path, language: str | None, txt: Path, srt: Path) -> str:
    """Write the .txt and .srt for one recording; return the language used."""
    segments, info = model.transcribe(str(audio), language=language, vad_filter=True)
    lines, cues = [], []
    for segment in segments:  # a generator: decoding happens while we iterate
        text = segment.text.strip()
        if not text:
            continue
        lines.append(text)
        cues.append(f"{len(cues) + 1}\n{srt_time(segment.start)} --> "
                    f"{srt_time(segment.end)}\n{text}\n")
        if info.duration:
            print(f"\r    {min(segment.end / info.duration, 1):4.0%}", end="", flush=True)
    print("\r    done")
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    srt.write_text("\n".join(cues), encoding="utf-8")
    return info.language


def transcribe_all(dest: Path, get_model, model_label: str, language: str | None,
                   force: bool, limit: int | None) -> dict:
    csv_path = dest / "inventory.csv"
    rows = load_inventory(csv_path)
    # Duplicates have no corpus_file of their own; their audio is transcribed once.
    todo = [r for r in rows if r["corpus_file"] and (force or not r.get("transcript"))]
    if limit:
        todo = todo[:limit]
    taken = {Path(r["transcript"]).stem.lower() for r in rows if r.get("transcript")}
    stats = dict(done=0, missing=0, failed=0, seconds=0.0)
    model = None

    for n, row in enumerate(todo, 1):
        audio = dest / row["corpus_file"]
        length = hms(float(row["duration_s"])) if row["duration_s"] else "?:??:??"
        print(f"[{n}/{len(todo)}] {row['corpus_file']}  [{length}]")
        if not audio.exists():
            stats["missing"] += 1
            print(f"    ! {audio} no longer exists; run collect_recordings.py again", file=sys.stderr)
            continue

        if row.get("transcript"):  # --force: overwrite the same files
            stem = Path(row["transcript"]).stem
        else:
            stem = audio.stem if audio.stem.lower() not in taken else f"{audio.stem}_{row['id']}"
        txt = dest / "transcripts" / f"{stem}.txt"

        if model is None:
            model = get_model()
        try:
            detected = transcribe_one(model, audio, language, txt, txt.with_suffix(".srt"))
        except Exception as exc:  # a damaged file must not stop a long batch
            stats["failed"] += 1
            print(f"\n    ! could not transcribe: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        row["language"] = detected or ""
        row["transcript"] = f"transcripts/{stem}.txt"
        row["transcribed_with"] = model_label
        taken.add(stem.lower())
        write_inventory(csv_path, rows)  # after every file, so Ctrl+C loses nothing
        stats["done"] += 1
        stats["seconds"] += float(row["duration_s"] or 0)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe the recordings listed in inventory.csv with Whisper, locally.")
    parser.add_argument("--dest", type=Path, default=REPO_ROOT / "recordings",
                        help="corpus folder (default: recordings/ in this repo)")
    parser.add_argument("--model", default="small", choices=MODELS,
                        help="Whisper model; larger is slower and more accurate (default: small)")
    parser.add_argument("--language", metavar="CODE",
                        help="language code such as de, en, tr, nl; default: detect per recording")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"],
                        help="cuda needs an NVIDIA GPU with CUDA 12 and cuDNN 9 (default: cpu)")
    parser.add_argument("--force", action="store_true",
                        help="transcribe again even if a transcript exists (e.g. with a better model)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="stop after N recordings; handy for a first test")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if not (args.dest / "inventory.csv").exists():
        print(f"No inventory at {args.dest / 'inventory.csv'}; run collect_recordings.py first.")
        return 1

    stats = transcribe_all(
        args.dest, lambda: load_model(args.model, args.device),
        f"faster-whisper {args.model}", args.language, args.force, args.limit)

    if not stats["done"] + stats["missing"] + stats["failed"]:
        print("Every collected recording already has a transcript (use --force to redo).")
        return 0
    print(f"\nTranscribed {stats['done']} recordings ({hms(stats['seconds'])}) "
          f"into {args.dest / 'transcripts'}.")
    if stats["missing"] or stats["failed"]:
        print(f"{stats['missing']} audio files were missing and {stats['failed']} failed; "
              "see the warnings above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
