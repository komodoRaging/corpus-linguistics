import csv
import io
import shutil
import sys
import tempfile
import unittest
from collections import namedtuple
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import collect_recordings  # noqa: E402
import transcribe_recordings  # noqa: E402
from test_collect_recordings import make_wav  # noqa: E402

Segment = namedtuple("Segment", "start end text")
Info = namedtuple("Info", "language duration")

EXPECTED_TXT = "Hallo und willkommen.\nZweiter Satz.\n"
EXPECTED_SRT = (
    "1\n00:00:00,000 --> 00:00:01,500\nHallo und willkommen.\n\n"
    "2\n00:01:01,250 --> 01:01:01,500\nZweiter Satz.\n"
)


class FakeModel:
    """Stands in for faster_whisper.WhisperModel; fails on files named in fail_on."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = fail_on

    def transcribe(self, path, language=None, vad_filter=False):
        self.calls.append((Path(path).name, language, vad_filter))
        if any(name in path for name in self.fail_on):
            raise RuntimeError("cannot decode")
        segments = [
            Segment(0.0, 1.5, " Hallo und willkommen."),
            Segment(1.5, 1.6, "   "),  # silence Whisper returned as blank text
            Segment(61.25, 3661.5, " Zweiter Satz."),
        ]
        return iter(segments), Info(language or "de", 3700.0)


class TranscribeRecordingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.source = tmp / "Sound Recordings"
        self.dest = tmp / "recordings"
        make_wav(self.source / "Interview.wav", 2)
        make_wav(self.source / "Walk.wav", 3, fill=5)
        copy = tmp / "OneDrive" / "Interview.wav"
        copy.parent.mkdir()
        shutil.copy(self.source / "Interview.wav", copy)
        self.collect(self.source, copy.parent)
        self.model = FakeModel()
        self.model_loads = 0

    def tearDown(self):
        self._tmp.cleanup()

    def collect(self, *roots):
        args = ["--only-roots", "--dest", str(self.dest)]
        for root in roots:
            args += ["--root", str(root)]
        with redirect_stdout(io.StringIO()):
            collect_recordings.main(args)

    def get_model(self):
        self.model_loads += 1
        return self.model

    def transcribe(self, language=None, force=False, limit=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return transcribe_recordings.transcribe_all(
                self.dest, self.get_model, "faster-whisper small", language, force, limit)

    def rows(self):
        with open(self.dest / "inventory.csv", newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))

    def test_writes_txt_and_srt_and_updates_inventory(self):
        stats = self.transcribe()

        self.assertEqual(stats["done"], 2)
        interview, walk, duplicate = self.rows()
        for row in (interview, walk):
            stem = Path(row["corpus_file"]).stem
            self.assertEqual(row["transcript"], f"transcripts/{stem}.txt")
            self.assertEqual(row["language"], "de")
            self.assertEqual(row["transcribed_with"], "faster-whisper small")
            txt = self.dest / row["transcript"]
            self.assertEqual(txt.read_text(encoding="utf-8"), EXPECTED_TXT)
            self.assertEqual(txt.with_suffix(".srt").read_text(encoding="utf-8"), EXPECTED_SRT)
        self.assertEqual(duplicate["duplicate_of"], interview["id"])
        self.assertEqual(duplicate["transcript"], "")
        self.assertTrue(all(vad for _, _, vad in self.model.calls))

    def test_rerun_skips_transcribed_and_never_loads_the_model(self):
        self.transcribe()
        self.model_loads = 0
        stats = self.transcribe()
        self.assertEqual(stats["done"], 0)
        self.assertEqual(self.model_loads, 0)

    def test_new_recordings_are_transcribed_on_the_next_run(self):
        self.transcribe()
        make_wav(self.source / "Later.wav", 4, fill=8)
        self.collect(self.source)
        self.model.calls.clear()
        self.transcribe()
        self.assertEqual([name for name, _, _ in self.model.calls],
                         ["2024-03-14_153200_later.wav"])

    def test_force_redoes_into_the_same_files_with_given_language(self):
        self.transcribe()
        before = {r["id"]: r["transcript"] for r in self.rows()}
        self.model.calls.clear()
        self.transcribe(language="tr", force=True)
        self.assertEqual(len(self.model.calls), 2)
        self.assertTrue(all(lang == "tr" for _, lang, _ in self.model.calls))
        self.assertEqual({r["id"]: r["transcript"] for r in self.rows()}, before)
        self.assertEqual(len(list((self.dest / "transcripts").glob("*.txt"))), 2)
        self.assertTrue(all(r["language"] == "tr" for r in self.rows() if r["transcript"]))

    def test_limit(self):
        self.assertEqual(self.transcribe(limit=1)["done"], 1)
        self.assertEqual(sum(bool(r["transcript"]) for r in self.rows()), 1)

    def test_failed_and_missing_files_do_not_stop_the_batch(self):
        interview, walk, _ = self.rows()
        (self.dest / walk["corpus_file"]).unlink()
        self.model.fail_on = (Path(interview["corpus_file"]).name,)
        stats = self.transcribe()
        self.assertEqual((stats["done"], stats["failed"], stats["missing"]), (0, 1, 1))
        self.assertTrue(all(r["transcript"] == "" for r in self.rows()))

    def test_collector_keeps_transcripts_and_hand_added_columns(self):
        self.transcribe()
        rows = self.rows()
        rows[0]["speaker"] = "Ayşe"
        collect_recordings.write_inventory(self.dest / "inventory.csv", rows)
        make_wav(self.source / "Later.wav", 4, fill=8)
        self.collect(self.source)

        first = self.rows()[0]
        self.assertEqual(first["speaker"], "Ayşe")
        self.assertEqual(first["language"], "de")
        self.assertTrue(first["transcript"].startswith("transcripts/"))
        self.assertEqual(len(self.rows()), 4)

    def test_main_end_to_end(self):
        with mock.patch.object(transcribe_recordings, "load_model", return_value=self.model), \
                redirect_stdout(io.StringIO()) as out:
            code = transcribe_recordings.main(["--dest", str(self.dest), "--language", "nl"])
        self.assertEqual(code, 0)
        self.assertIn("Transcribed 2 recordings (0:00:05)", out.getvalue())
        self.assertTrue(all(lang == "nl" for _, lang, _ in self.model.calls))

    def test_main_without_inventory(self):
        with redirect_stdout(io.StringIO()):
            code = transcribe_recordings.main(["--dest", str(self.dest / "nowhere")])
        self.assertEqual(code, 1)

    def test_srt_time(self):
        self.assertEqual(transcribe_recordings.srt_time(0), "00:00:00,000")
        self.assertEqual(transcribe_recordings.srt_time(3661.5), "01:01:01,500")
        self.assertEqual(transcribe_recordings.srt_time(59.9996), "00:01:00,000")


if __name__ == "__main__":
    unittest.main()
