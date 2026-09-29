# corpus-linguistics

Two steps turn the sound recordings on a Windows computer into a text corpus. Both run entirely on your machine.

1. `tools/collect_recordings.py` finds the recordings. It copies each unique one into `recordings/raw/` and lists it in `recordings/inventory.csv`. The originals are only read, never moved, renamed or changed.
2. `tools/transcribe_recordings.py` runs Whisper speech-to-text on every collected recording. It writes a `.txt` and a `.srt` transcript for each one into `recordings/transcripts/`.

## 1. Collecting your sound recordings

### One-time setup (PowerShell)

```powershell
winget install Python.Python.3.12        # skip if `py --version` already works
git clone https://github.com/komodoRaging/corpus-linguistics
cd corpus-linguistics
py -m pip install -r requirements.txt
```

### Run it

```powershell
py tools\collect_recordings.py --dry-run     # preview: lists what it would collect, writes nothing
py tools\collect_recordings.py               # copy + write recordings\inventory.csv
```

Other options:

```powershell
py tools\collect_recordings.py --root "E:\"                      # also scan an SD card / field recorder
py tools\collect_recordings.py --root "D:\Projects\Interviews"   # or any other folder (repeatable)
py tools\collect_recordings.py --min-seconds 5                   # ignore clips under 5 s
```

It is safe to run again whenever you have made new recordings. Files that are already in the inventory are skipped, and new ones are appended with the next id.

### Where it looks

- `Documents\Sound Recordings`, where Windows Sound Recorder / Voice Recorder saves by default. This includes a Documents folder that OneDrive has redirected.
- The older Voice Recorder app's own storage under `%LOCALAPPDATA%\Packages\Microsoft.WindowsSoundRecorder_*`.
- Desktop, Downloads, Music and Videos, both in your profile and in OneDrive.
- Every folder passed with `--root`.

It collects `.m4a .wav .mp3 .wma .aac .flac .ogg .opus .aif .aiff .amr .3gp`. It skips `AppData`, the recycle bin, `.git`, `node_modules`, and the `recordings\` folder itself.

### What you get

`recordings\raw\` holds the copies, named `YYYY-MM-DD_HHMMSS_<original-name>.<ext>` so they sort chronologically. This folder is git-ignored and stays on your machine.

`recordings\inventory.csv` opens directly in Excel and is the file you commit. It has one row per file found:

| column | meaning |
| --- | --- |
| `id` | running number |
| `corpus_file` | the copy inside `recordings\` (empty for duplicates) |
| `original_path` | where the recording was found, with your profile folder shown as `~` so your Windows user name is not published |
| `sha256`, `size_bytes` | fingerprint used to detect duplicates |
| `format`, `duration_s`, `sample_rate_hz`, `channels`, `bitrate_kbps` | audio properties |
| `recorded_at`, `date_source` | best recording date: an embedded tag date (`tag`), otherwise the file's creation or modification time, whichever is earlier |
| `collected_at` | when this script picked the file up |
| `duplicate_of` | id of the row holding identical audio found elsewhere (e.g. a OneDrive copy) |
| `error` | set when the audio properties could not be read; the file is still copied |
| `language`, `transcript`, `transcribed_with` | filled in by step 2: detected language code, transcript file, model used |

You can add your own columns, such as `speaker`, `place` or `notes`; both scripts keep them. If you save the file from Excel, save it as **CSV UTF-8**. Semicolon-separated files from German Excel are read correctly. Don't edit the existing columns, because the scripts rely on them.

### Good to know

- **OneDrive online-only files** (cloud icon in Explorer) are downloaded when the script reads them. Run `--dry-run` first if your connection is slow.
- **Phone recordings** are not on the computer until you copy them over. Copy them into any folder and pass it with `--root`.
- **Audacity projects** (`.aup3`) are not audio files. Export them to WAV first if you want them in the corpus.

## 2. Transcribing them

This step uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper), a fast local build of OpenAI's Whisper. The audio is never uploaded. The only download is the model itself, once, from huggingface.co.

```powershell
py tools\transcribe_recordings.py --limit 1        # try one recording first
py tools\transcribe_recordings.py                  # all recordings without a transcript
```

Each run only picks up recordings that don't have a transcript yet, so after collecting new recordings you just run both steps again. Progress is saved after every file, so you can stop with Ctrl+C at any point.

Each recording `raw\<name>.m4a` gets two files:

- `transcripts\<name>.txt`: plain UTF-8 text, one speech segment per line, ready for AntConc, Voyant, spaCy or NLTK.
- `transcripts\<name>.srt`: the same text with timestamps. It opens in VLC, Premiere, DaVinci Resolve or any subtitle editor, which is handy for checking a passage against the audio.

Options:

```powershell
py tools\transcribe_recordings.py --language de                       # skip detection: de, en, tr, nl, ...
py tools\transcribe_recordings.py --model large-v3-turbo --force      # redo everything with a better model
py tools\transcribe_recordings.py --device cuda                       # NVIDIA GPU (needs CUDA 12 + cuDNN 9)
```

Model choice (`--model`, default `small`):

| model | download | notes |
| --- | --- | --- |
| `tiny`, `base` | 75–145 MB | fast drafts, weak on accents and non-English speech |
| `small` | ~480 MB | default; a reasonable balance on a normal CPU |
| `medium` | ~1.5 GB | clearly better for German, Turkish, Dutch; several times slower |
| `large-v3-turbo` | ~1.6 GB | near-best accuracy; comfortable on a GPU, slow on CPU |
| `large-v3` | ~3 GB | most accurate, slowest |

Without `--language`, Whisper detects one language per recording from its first 30 seconds. For recordings that switch between languages, the transcript follows that first language. Split such recordings, or run them again with `--language`.

The transcripts contain the same personal speech as the audio, so `recordings/transcripts/` is git-ignored. To publish the text corpus once everyone recorded has agreed, delete that line from `.gitignore`.

Whisper is good but not perfect. It can mishear names, and occasionally it invents a sentence during long silences or music. Check passages against the `.srt` before quoting them.

## Tests

```powershell
py -m unittest discover tests      # no model download needed; Whisper is replaced by a stand-in
```
