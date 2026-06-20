"""Ponytail self-check for the ID-based post-processing flow.

Tests find_chinese_subtitle (apostrophe in filename, skip zh-TW, newest-mtime)
and arr JSON parsing (no live HTTP).
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from core.arr import resolve_media_path, ArrError
from core.processor import SubtitleProcessor


def test_find_chinese_subtitle():
    proc = SubtitleProcessor()
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        video = base / "Tom's Movie (2024).mkv"
        video.touch()

        # 目標：帶撇號的中文字幕（最新）
        target = base / "Tom's Movie (2024).zh.srt"
        target.write_text("簡體內容", encoding="utf-8")

        # 應跳過：已是 zh-TW
        zhtw = base / "Tom's Movie (2024).zh-TW.srt"
        zhtw.write_text("繁體", encoding="utf-8")

        # 應跳過：非中文標籤
        eng = base / "Tom's Movie (2024).en.srt"
        eng.write_text("English", encoding="utf-8")

        # 舊的中文字幕（mtime 更早）
        old = base / "Tom's Movie (2024).chi.srt"
        old.write_text("舊的", encoding="utf-8")
        # 確保 target 比 old 新
        import os
        os.utime(old, (time.time() - 100, time.time() - 100))

        result = proc.find_chinese_subtitle(video)
        assert result is not None, "應該找到字幕"
        assert result.name == target.name, f"應該挑中帶撇號的最新字幕，實際: {result.name}"
        assert ".zh-tw" not in result.name.lower(), "不應回傳 zh-TW"


def test_resolve_radarr_path():
    """Radarr: series_id 為空 → 走 movie endpoint。"""
    movie_json = json.dumps({
        "movieFile": {"path": "/video/movies/Tom's Movie (2024)/Tom's Movie (2024).mkv"}
    }).encode()

    with patch.dict("os.environ", {"RADARR_URL": "http://fake:7878", "RADARR_API_KEY": "k"}):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda *a: None
            mock_open.return_value.read.return_value = movie_json

            result = resolve_media_path(None, "42")
            assert result == Path("/video/movies/Tom's Movie (2024)/Tom's Movie (2024).mkv")


def test_resolve_sonarr_path():
    """Sonarr: series_id 非空 → episode → episodefile。"""
    ep_json = json.dumps({"episodeFileId": 99}).encode()
    ef_json = json.dumps({"path": "/video/tv/Show S01E01.mkv"}).encode()

    call_count = 0

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self_inner):
            nonlocal call_count
            call_count += 1
            return ep_json if call_count == 1 else ef_json

    with patch.dict("os.environ", {"SONARR_URL": "http://fake:8989", "SONARR_API_KEY": "k"}):
        with patch("urllib.request.urlopen", return_value=FakeResp()):
            result = resolve_media_path("5", "10")
            assert result == Path("/video/tv/Show S01E01.mkv")


def test_missing_env():
    """未設環境變數 → ArrError。"""
    with patch.dict("os.environ", {}, clear=True):
        try:
            resolve_media_path(None, "1")
            assert False, "應該拋出 ArrError"
        except ArrError:
            pass


if __name__ == "__main__":
    test_find_chinese_subtitle()
    print("✅ find_chinese_subtitle OK")
    test_resolve_radarr_path()
    print("✅ resolve_radarr_path OK")
    test_resolve_sonarr_path()
    print("✅ resolve_sonarr_path OK")
    test_missing_env()
    print("✅ missing_env OK")
    print("🎉 All checks passed")
