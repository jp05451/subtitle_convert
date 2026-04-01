# Project Guidelines

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
