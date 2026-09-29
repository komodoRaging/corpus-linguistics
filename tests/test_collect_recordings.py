import csv
import hashlib
import io
import os
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import collect_recordings  # noqa: E402

RECORDED = datetime(2024, 3, 14, 15, 32, 0).timestamp()


def make_wav(path: Path, seconds: float, rate=16000, channels=1, fill=1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([fill, 0]) * frames * channels)
    os.utime(path, (RECORDED, RECORDED))
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CollectRecordingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        self.dest = tmp / "repo" / "recordings"
        docs = self.home / "Documents"
        onedrive_docs = self.home / "OneDrive" / "Documents"

        self.recording = make_wav(docs / "Sound Recordings" / "Recording (2).wav", 2)
        self.duplicate = onedrive_docs / "Sound Recordings" / "Recording (2).wav"
        self.duplicate.parent.mkdir(parents=True)
        self.duplicate.write_bytes(self.recording.read_bytes())
        self.interview = make_wav(self.home / "Desktop" / "interviews" / "Interview Ayşe.wav",
                                  3, rate=44100, channels=2, fill=7)
        (self.home / "Desktop" / "notes.txt").write_text("not audio")
        make_wav(self.home / "Downloads" / "click.wav", 0.2, fill=9)
        self.broken = docs / "Broken.m4a"
        self.broken.write_bytes(b"this is not an m4a file")

        self.roots = [docs, onedrive_docs, self.home / "Desktop", self.home / "Downloads"]
        self.originals = {p: digest(p) for p in self.home.rglob("*") if p.is_file()}

    def tearDown(self):
        self._tmp.cleanup()

    def run_collector(self, *extra):
        args = ["--only-roots", "--dest", str(self.dest)]
        for root in self.roots:
            args += ["--root", str(root)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(collect_recordings.main(args + list(extra)), 0)

    def inventory(self):
        with open(self.dest / "inventory.csv", newline="", encoding="utf-8-sig") as fh:
            rows = {}
            for r in csv.DictReader(fh):  # first row per name; duplicates come later
                rows.setdefault(Path(r["original_path"]).name, r)
            return rows

    def test_copies_unique_recordings_and_writes_inventory(self):
        self.run_collector()
        rows = self.inventory()

        self.assertEqual(sorted(rows), ["Broken.m4a", "Interview Ayşe.wav", "Recording (2).wav"])
        copies = sorted(p.name for p in (self.dest / "raw").iterdir())
        self.assertEqual(copies, [
            "2024-03-14_153200_interview-ayşe.wav",
            "2024-03-14_153200_recording-2.wav",
            rows["Broken.m4a"]["corpus_file"].removeprefix("raw/"),
        ])

        rec = rows["Recording (2).wav"]
        self.assertEqual(rec["corpus_file"], "raw/2024-03-14_153200_recording-2.wav")
        self.assertEqual(rec["duration_s"], "2.000")
        self.assertEqual(rec["sample_rate_hz"], "16000")
        self.assertEqual(rec["channels"], "1")
        self.assertEqual(rec["recorded_at"], "2024-03-14T15:32:00")
        self.assertEqual(rec["date_source"], "file_modified")
        self.assertEqual(rec["sha256"], self.originals[self.recording])
        self.assertEqual(digest(self.dest / rec["corpus_file"]), rec["sha256"])

        interview = rows["Interview Ayşe.wav"]
        self.assertEqual(interview["sample_rate_hz"], "44100")
        self.assertEqual(interview["channels"], "2")
        self.assertEqual(interview["duration_s"], "3.000")

        broken = rows["Broken.m4a"]
        self.assertTrue(broken["error"])
        self.assertEqual(broken["duration_s"], "")
        self.assertTrue((self.dest / broken["corpus_file"]).exists())

    def test_duplicate_is_listed_but_copied_once(self):
        self.run_collector()
        with open(self.dest / "inventory.csv", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        same = [r for r in rows if r["sha256"] == self.originals[self.recording]]

        self.assertEqual(len(same), 2)
        first, second = same
        self.assertEqual(first["duplicate_of"], "")
        self.assertEqual(second["duplicate_of"], first["id"])
        self.assertEqual(second["corpus_file"], "")
        self.assertIn("OneDrive", second["original_path"])

    def test_originals_are_untouched(self):
        self.run_collector()
        for path, sha in self.originals.items():
            self.assertTrue(path.exists(), path)
            self.assertEqual(digest(path), sha, path)

    def test_rerun_adds_nothing(self):
        self.run_collector()
        first = (self.dest / "inventory.csv").read_bytes()
        copies = sorted((self.dest / "raw").iterdir())
        self.run_collector()
        self.assertEqual((self.dest / "inventory.csv").read_bytes(), first)
        self.assertEqual(sorted((self.dest / "raw").iterdir()), copies)

    def test_rerun_picks_up_new_recordings(self):
        self.run_collector()
        make_wav(self.home / "Documents" / "Sound Recordings" / "Recording (3).wav", 5, fill=3)
        self.run_collector()
        rows = self.inventory()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows["Recording (3).wav"]["id"], "5")

    def test_dry_run_writes_nothing(self):
        self.run_collector("--dry-run")
        self.assertFalse(self.dest.exists())

    def test_destination_inside_a_scanned_folder_is_not_rescanned(self):
        self.dest = self.home / "Documents" / "corpus-linguistics" / "recordings"
        self.run_collector()
        self.run_collector()
        self.assertEqual(len(self.inventory()), 3)

    def test_embedded_date_wins_over_file_time(self):
        from mutagen.id3 import TDRC
        from mutagen.wave import WAVE

        tagged = WAVE(self.interview)
        tagged.add_tags()
        tagged.tags.add(TDRC(encoding=3, text="2023-05-01T10:20:30"))
        tagged.save()
        self.run_collector()
        row = self.inventory()["Interview Ayşe.wav"]
        self.assertEqual(row["recorded_at"], "2023-05-01T10:20:30")
        self.assertEqual(row["date_source"], "tag")
        self.assertEqual(row["corpus_file"], "raw/2023-05-01_102030_interview-ayşe.wav")

    def test_inventory_saved_by_german_excel_is_read(self):
        self.run_collector()
        csv_path = self.dest / "inventory.csv"
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            row["notes"] = "Küche, laut" if row["original_path"].endswith("Ayşe.wav") else ""
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

        make_wav(self.home / "Desktop" / "New.wav", 2, fill=4)
        self.run_collector()
        rows = self.inventory()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows["Interview Ayşe.wav"]["notes"], "Küche, laut")
        header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertTrue(header.startswith("id,corpus_file,"))  # written back with commas

    def test_paths_under_home_are_shown_with_tilde(self):
        with mock.patch.object(collect_recordings.Path, "home", return_value=self.home):
            shown = collect_recordings.display_path(self.recording)
        self.assertEqual(shown, str(Path("~", "Documents", "Sound Recordings", "Recording (2).wav")))


if __name__ == "__main__":
    unittest.main()
