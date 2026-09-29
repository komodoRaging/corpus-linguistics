# corpus-linguistics

## Collecting your sound recordings

`tools/collect_recordings.py` finds the audio recordings on a Windows computer. It copies each unique recording into `recordings/raw/` and lists it in `recordings/inventory.csv`. The originals are only read, never moved, renamed or changed.

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

### Good to know

- **OneDrive online-only files** (cloud icon in Explorer) are downloaded when the script reads them. Run `--dry-run` first if your connection is slow.
- **Phone recordings** are not on the computer until you copy them over. Copy them into any folder and pass it with `--root`.
- **Audacity projects** (`.aup3`) are not audio files. Export them to WAV first if you want them in the corpus.

### Tests

```powershell
py -m unittest discover tests
```
