"""Sonarr / Radarr v3 API → media file path resolver.

# ponytail: stdlib urllib instead of httpx — no new dep.
"""

import json
import os
import urllib.request
from pathlib import Path


class ArrError(Exception):
    """Raised when an arr API call fails or returns unexpected data."""


def _get_json(base_url: str, api_path: str, api_key: str) -> dict:
    url = f"{base_url.rstrip('/')}{api_path}"
    req = urllib.request.Request(url, headers={"X-Api-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ArrError(f"HTTP {exc.code} from {url}") from exc
    except Exception as exc:
        raise ArrError(f"Failed to reach {url}: {exc}") from exc


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ArrError(f"環境變數 {name} 未設定")
    return val


def resolve_media_path(series_id: str | None, episode_id: str) -> Path:
    """Return the on-disk video file path for a Bazarr post-processing event.

    series_id truthy → Sonarr episode; empty/None → Radarr movie.
    """
    if series_id:
        # Sonarr: episode → episodeFile → path
        url = _require_env("SONARR_URL")
        key = _require_env("SONARR_API_KEY")

        ep = _get_json(url, f"/api/v3/episode/{episode_id}", key)
        file_id = ep.get("episodeFileId")
        if not file_id:
            raise ArrError(f"Sonarr episode {episode_id} 尚無檔案（未匯入）")

        ef = _get_json(url, f"/api/v3/episodefile/{file_id}", key)
        path = ef.get("path")
    else:
        # Radarr: movie → movieFile → path
        url = _require_env("RADARR_URL")
        key = _require_env("RADARR_API_KEY")

        movie = _get_json(url, f"/api/v3/movie/{episode_id}", key)
        # movieFile may be nested or need a separate call
        mf = movie.get("movieFile")
        if mf:
            path = mf.get("path")
        else:
            mf_id = movie.get("movieFileId")
            if not mf_id:
                raise ArrError(f"Radarr movie {episode_id} 尚無檔案（未匯入）")
            mf = _get_json(url, f"/api/v3/moviefile/{mf_id}", key)
            path = mf.get("path")

    if not path:
        raise ArrError(f"無法從 arr API 取得檔案路徑 (episode_id={episode_id})")
    return Path(path)
