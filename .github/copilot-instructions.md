# Project Guidelines

## 🤖 Subtitle Automation & Optimization Rules

### 1. Project Context
* **Target:** A Python-based post-processing tool for Bazarr (running in Docker/Synology NAS).
* **Goal:** Automatically detect, convert, and synchronize subtitles for a media library (Jellyfin/Plex/Emby).
* **Key Workflow:** 1. Bazarr downloads a sub (often Simplified Chinese `.zh`/`.chi`/'zho').
    2. This script is triggered via Post-processing command.
    3. Script performs: Encoding detection -> Quality Scoring -> OpenCC Conversion (S2TWP) -> Multi-version Sync -> Naming standardization.

### 2. Core Technical Logic
* **Language Conversion:** Must use `opencc-python-reimplemented` with `s2twp.json` (Simplified to Traditional Taiwan with phrase optimization).
* **Encoding Resilience:** Must attempt reading in order: `utf-8`, `gb18030`, `big5`, `utf-16`. Output must always be `utf-8`.
* **Standardized Naming:** Subtitles must end in `.zh-TW.srt` or `.zh-TW.ass` for maximum compatibility with Jellyfin.

### 3. Quantitative Quality Scoring (The "Best-of-Breed" Algorithm)
When multiple subtitles exist in a folder, do not process all. Calculate a **Quality Score** to select the "Seed" file:
* **Traditional Ratio (Weight: 50%):** Compare original content vs. `t2s` converted version. Higher difference = more native Traditional Chinese characters.
* **Phrase Localization (Weight: 30%):** * `+Score`: 螢幕, 影片, 軟體, 程式, 品質, 網路.
    * `-Score`: 屏幕, 视频, 软件, 程序, 质量, 网络.
* **Integrity (Weight: 20%):** Line count > 500 lines (Standard movie), File size > 20KB (Exclude corrupted files).

### 4. Multi-Version Synchronization
* Identify all video files in the directory (`.mkv`, `.mp4`).
* Clone the "Seed" subtitle and rename it to match each video filename (e.g., `Movie.4K.mkv` -> `Movie.4K.zh-TW.srt`).

### 5. Implementation Constraints
* **Path Awareness:** Support both single file input (Bazarr trigger: `{{subtitles}}`) and recursive directory scanning.
* **Diversity:** Must include a "Wildcard" fallback—if no native Traditional sub is found, use the highest-scoring Simplified sub and convert it.
* **Cleanliness:** After generating the `.zh-TW` version, optional logic to remove redundant `.zho`, `.chi`, `.sc` files to keep the directory clean.

### 6. Development Style
* **Architecture:** Class-based, modular design (Analyzer, Converter, Synchronizer).
* **Comments:** Explicit, professional documentation (witty comments are allowed but must not obscure logic).
* **Environment:** Cross-platform (Linux/Docker/NAS).

## Code Style

- Target Python 3.12 syntax and standard library behavior. See [.python-version](../.python-version).
- Keep changes minimal and focused. Do not rename public symbols or refactor unrelated code.
- Preserve current style in [main.py](../main.py): class-based workflow, `pathlib.Path` usage, and clear print-based progress logs.

## Architecture

- Core logic is in `SubtitleProcessor` in [main.py](../main.py).
- `process_folder_smart` handles folder-level subtitle selection, priority ranking, and cleanup.
- `_convert_and_distribute` performs OpenCC conversion and writes `.zh-TW` subtitles for matching videos.
- `run` supports both single-file input (Bazarr-style) and recursive directory scanning.

## Build and Test

- Install dependencies with `uv sync` (lockfile is [uv.lock](../uv.lock)).
- Run with `python main.py` (defaults to `/movies`) or `python main.py <path>`.
- There is no formal test suite yet; for changes, validate by running against a sample folder under [movies](../movies).

## Conventions

- Subtitle priority is project-specific and must be preserved unless explicitly requested. See `self.tag_priority` in [main.py](../main.py).
- Process one folder as one unit to avoid duplicate Traditional Chinese outputs.
- Output naming convention is `<video_stem>.zh-TW<subtitle_suffix>`.
- Keep compatibility with mixed subtitle encodings handled by `_safe_read`.

## Pitfalls

- The [movies](../movies) directory is git-ignored in [.gitignore](../.gitignore); do not rely on committed fixtures.
- Avoid deleting `.zh-TW` outputs during cleanup unless the task explicitly asks to change cleanup behavior.
- Dependency metadata and Python requirement are defined in [pyproject.toml](../pyproject.toml).
