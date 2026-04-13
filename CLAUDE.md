# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A subtitle conversion tool that:
1. **Bazarr mode** (`main.py`): Accepts a single subtitle file from Bazarr, converts it from Simplified Chinese to Traditional Chinese (Taiwan), renames it to `.zh-TW`, and optionally syncs to peer folders containing the same movie.
2. **Scan mode** (`scan.py`): Recursively scans a folder tree, picks the best Chinese subtitle per movie folder (via multi-criteria scoring), converts it, and distributes it to all video files in that folder.

## Running the Tool

```bash
# Activate venv first (managed by uv)
source .venv/bin/activate

# Bazarr single-file mode
python main.py --bazarr-subtitle /path/to/subtitle.zh.srt [--keep-original] [--dry-run]

# Bazarr with path remapping (container path → host path)
python main.py --bazarr-subtitle /movies/foo.srt --bazarr-root-from /movies --bazarr-root-to /volume1/media/movies

# Folder scan mode
python scan.py --scan-path /movies [--dry-run]
python scan.py /movies  # legacy positional arg
```

## Dependency Management

Uses `uv` with a `.venv` and `uv.lock`. To add/update dependencies:
```bash
uv add <package>
uv sync
```

Runtime dependency: `opencc-python-reimplemented`. External system dependency: `ffmpeg`/`ffprobe` (for embedded subtitle detection; gracefully degraded if absent).

## Architecture

```
main.py          # Bazarr entry point (single subtitle file)
scan.py          # Folder scan entry point
core/
  models.py      # SubtitleCandidate dataclass
  scoring.py     # BaselineTagScorer, BazarrStyleScorer, SubtitleSelector
  processor.py   # SubtitleProcessor — all business logic
```

### Core Flow (`SubtitleProcessor`)

**Bazarr mode** (`run()` → `process_bazarr_subtitle_auto_sync()`):
1. Build an identity index from all `movie.nfo` files under the scan root (keyed by title/year, imdb, tmdb).
2. Find peer folders for the same movie.
3. Read subtitle → convert with OpenCC `s2twp` → write as `.zh-TW`.
4. Sync the converted subtitle to all peer folders (skipping folders that already have embedded Traditional Chinese subtitles, detected via `ffprobe`).

**Scan mode** (`scan_folders()` → `process_folder_smart()`):
1. Collect all Chinese-tagged subtitle files in the folder.
2. Score each via `BaselineTagScorer` (tag priority) and `BazarrStyleScorer` (traditional ratio 50%, Taiwan localization 30%, file integrity 20%).
3. `SubtitleSelector.select_best()` picks the winner.
4. Convert winner with OpenCC and write `<video_stem>.zh-TW.<ext>` for every video file in the folder.
5. Delete all other Chinese-tagged subtitles (cleanup step).

### Tag Priority System

Tags embedded in filenames determine baseline priority:
- Priority 2 (already Traditional): `.tc`, `.big5`, `cht`, `zht`
- Priority 1 (Simplified, needs conversion): `.sc`, `.zhs`, `.zho`, `.chi`, `.chs`, `.zh`

### Encoding Resilience

`_safe_read()` tries encodings in order: `utf-8` → `gb18030` → `gbk` → `big5` → `utf-16`. Always writes output as UTF-8.

### Movie Identity (Cross-Folder Sync)

`_movie_identity_keys()` parses `movie.nfo` XML and extracts multiple identity keys (`title|year`, `imdb:ttXXX`, `tmdb:XXXXX`) to match the same film across differently-named folders (e.g. different resolution releases).
