# Subtitle Convert

自動將簡體中文字幕轉換為繁體中文（台灣），支援 Bazarr 後處理整合。

## 功能

- **Bazarr 單檔模式** (`main.py`)：接收字幕檔，簡轉繁 + 改名為 `.zh-TW`，自動同步到同片其他資料夾
- **資料夾掃描模式** (`scan.py`)：遞迴掃描，多標準評分挑最佳字幕，轉換後分發給所有影片
- **API 模式** (`api.py`)：FastAPI 服務，提供 HTTP 接口供 Bazarr 後處理呼叫

## 快速開始

```bash
# 安裝依賴（使用 uv）
uv sync

# CLI 模式
uv run python main.py --bazarr-subtitle /path/to/subtitle.zh.srt
uv run python scan.py --scan-path /movies

# API 模式
uv run uvicorn api:app --host 0.0.0.0 --port 6768
```

## Docker 部署

```bash
docker compose up -d
```

需設定 `docker-compose.yaml` 中 `subtitle-processor` 服務的環境變數：

| 環境變數 | 說明 |
|----------|------|
| `SONARR_URL` | Sonarr 位址，如 `http://sonarr:8989` |
| `SONARR_API_KEY` | Sonarr API Key（Settings → General） |
| `RADARR_URL` | Radarr 位址，如 `http://radarr:7878` |
| `RADARR_API_KEY` | Radarr API Key（Settings → General） |
| `REMAP_ROOT_FROM` | 路徑重映射來源（選填，容器掛載路徑相同時不需要） |
| `REMAP_ROOT_TO` | 路徑重映射目標（選填） |

## API 端點

### `GET /health`

健康檢查。

### `POST /process`（路徑模式）

直接傳入字幕檔路徑進行轉換。

```json
{
  "subtitle_path": "/video/movies/Movie Name/Movie.zh.srt",
  "keep_original": false,
  "dry_run": false
}
```

### `POST /process-by-id`（ID 模式，推薦）

以 Sonarr/Radarr ID 定位影片，自動找到對應字幕進行轉換。**徹底避免檔名含 `'` 或 `"` 時被 shell 引號切斷的問題。**

```json
{
  "series_id": "",
  "episode_id": "42",
  "language": "zh",
  "keep_original": false,
  "dry_run": false
}
```

| 欄位 | 說明 |
|------|------|
| `series_id` | Sonarr series ID。**空字串或 null 代表電影**（走 Radarr），非空代表影集（走 Sonarr） |
| `episode_id` | Sonarr episode ID 或 Radarr movie ID |
| `language` | 字幕語言代碼（選填，如 `zh`，用於定位剛下載的字幕） |

## Bazarr 後處理設定

在 Bazarr → Settings → Subtitles → Post-processing command 填入：

```
curl -s -X POST http://subtitle-processor:6768/process-by-id -H "Content-Type: application/json" -d "{\"series_id\": \"{{series_id}}\", \"episode_id\": \"{{episode_id}}\", \"language\": \"{{subtitles_language_code2}}\"}"
```

只有整數 ID 和語言代碼穿過 shell 邊界，不會被特殊字元破壞。

### Bazarr 可用變數參考

| 變數 | 說明 |
|------|------|
| `{{episode}}` | 影片完整路徑 |
| `{{subtitles}}` | 字幕完整路徑 |
| `{{series_id}}` | Sonarr series ID（電影時為空） |
| `{{episode_id}}` | Sonarr episode ID 或 Radarr movie ID |
| `{{subtitles_language_code2}}` | 兩碼語言代碼（如 `zh`） |
| `{{subtitles_language_code3}}` | 三碼語言代碼（如 `zho`） |
| `{{provider}}` | 字幕來源 |
| `{{score}}` | 匹配分數 |
| `{{subtitle_id}}` | 字幕 ID |

## 架構

```
api.py           # FastAPI 服務（/process, /process-by-id）
main.py          # Bazarr CLI 入口
scan.py          # 資料夾掃描入口
core/
  arr.py         # Sonarr/Radarr API → 影片路徑解析
  models.py      # SubtitleCandidate dataclass
  scoring.py     # BaselineTagScorer, BazarrStyleScorer, SubtitleSelector
  processor.py   # SubtitleProcessor — 核心業務邏輯
```
