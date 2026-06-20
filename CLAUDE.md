# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A subtitle conversion tool that:
1. **Bazarr mode** (`main.py`): Accepts a single subtitle file from Bazarr, converts it from Simplified Chinese to Traditional Chinese (Taiwan), renames it to `.zh-TW`, and optionally syncs to peer folders containing the same movie.
2. **Scan mode** (`scan.py`): Recursively scans a folder tree, picks the best Chinese subtitle per movie folder (via multi-criteria scoring), converts it, and distributes it to all video files in that folder.
3. **API mode** (`api.py`): FastAPI service for Bazarr post-processing. Two endpoints:
   - `POST /process` — path-based (legacy).
   - `POST /process-by-id` — ID-based (recommended). Takes Sonarr/Radarr IDs, resolves the video path via arr v3 API, then locates and converts the matching subtitle. Avoids shell quote-mangling when filenames contain `'` or `"`.

## Running the Tool

```bash
# Bazarr single-file mode
uv run python main.py --bazarr-subtitle /path/to/subtitle.zh.srt [--keep-original] [--dry-run]

# Bazarr with path remapping (container path → host path)
uv run python main.py --bazarr-subtitle /movies/foo.srt --bazarr-root-from /movies --bazarr-root-to /volume1/media/movies

# Folder scan mode
uv run python scan.py --scan-path /movies [--dry-run]

# API mode
uv run uvicorn api:app --host 0.0.0.0 --port 6768
```

## Dependency Management

Uses `uv` with a `.venv` and `uv.lock`. To add/update dependencies:
```bash
uv add <package>
uv sync
```

Runtime dependency: `opencc-python-reimplemented`, `fastapi`, `uvicorn`. External system dependency: `ffmpeg`/`ffprobe` (for embedded subtitle detection; gracefully degraded if absent).

## Environment Variables (Docker / API mode)

| Variable | Description |
|----------|-------------|
| `SONARR_URL` | Sonarr base URL (e.g. `http://sonarr:8989`) |
| `SONARR_API_KEY` | Sonarr API key (from `.env`, not hardcoded) |
| `RADARR_URL` | Radarr base URL (e.g. `http://radarr:7878`) |
| `RADARR_API_KEY` | Radarr API key (from `.env`, not hardcoded) |
| `REMAP_ROOT_FROM` | Path remap source prefix (optional) |
| `REMAP_ROOT_TO` | Path remap target prefix (optional) |

## Architecture

```
api.py           # FastAPI service (/process, /process-by-id)
main.py          # Bazarr CLI entry point (single subtitle file)
scan.py          # Folder scan entry point
core/
  arr.py         # Sonarr/Radarr v3 API → video file path resolver
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

### ID-Based Post-Processing (`/process-by-id`)

Bazarr's post-processing runs commands via `shell=True` with textual variable substitution. Filenames containing `'` or `"` break the shell/JSON boundary. The ID-based endpoint sidesteps this entirely:

1. Bazarr sends only integer IDs (`{{series_id}}`, `{{episode_id}}`) and a language code.
2. `core/arr.py` queries Sonarr or Radarr v3 API to resolve the video file path. Discriminator: `series_id` empty → Radarr movie; non-empty → Sonarr episode.
3. `processor.find_chinese_subtitle()` locates the just-downloaded subtitle in the video's folder (newest mtime, matching stem/language, excluding `.zh-TW`).
4. Existing `processor.run()` handles conversion.
