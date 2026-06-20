import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from core.arr import ArrError, resolve_media_path
from core.processor import SubtitleProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("subtitle_api")

# processor 支援的字幕副檔名，與 SubtitleProcessor.sub_exts 一致
_SUPPORTED_EXTS = {".srt", ".ass"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("初始化 SubtitleProcessor...")
    app.state.processor = SubtitleProcessor()
    logger.info("SubtitleProcessor 就緒。")
    yield
    logger.info("API server 關閉。")


app = FastAPI(
    title="Subtitle Convert API",
    description="Bazarr post-processing：將字幕自動轉換為繁體中文（zh-TW）",
    version="1.0.0",
    lifespan=lifespan,
)


class ProcessRequest(BaseModel):
    subtitle_path: str = Field(
        ...,
        description="Bazarr 傳入的字幕檔路徑（容器內路徑或主機路徑）",
        examples=["/movies/The.Batman.2022/The.Batman.2022.zh.srt"],
    )
    root_from: str | None = Field(
        default=None,
        description="路徑重映射：容器內根目錄（覆蓋環境變數 REMAP_ROOT_FROM）",
        examples=["/movies"],
    )
    root_to: str | None = Field(
        default=None,
        description="路徑重映射：主機根目錄（覆蓋環境變數 REMAP_ROOT_TO）",
        examples=["/media/movies"],
    )
    keep_original: bool = Field(default=False, description="保留原始字幕檔（不刪除）")
    dry_run: bool = Field(default=False, description="Dry-run 模式：只預覽不寫入")


class ProcessByIdRequest(BaseModel):
    series_id: str | None = Field(
        default=None,
        description="Sonarr series ID（空或 None 代表電影，走 Radarr）",
    )
    episode_id: str = Field(
        ...,
        description="Sonarr episode ID 或 Radarr movie ID",
    )
    language: str | None = Field(
        default=None,
        description="字幕語言代碼，如 zh（用於定位剛下載的字幕）",
    )
    keep_original: bool = Field(default=False, description="保留原始字幕檔（不刪除）")
    dry_run: bool = Field(default=False, description="Dry-run 模式：只預覽不寫入")


class ProcessResponse(BaseModel):
    status: str   # "ok" | "skipped"
    message: str
    input_path: str
    dry_run: bool


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/process", response_model=ProcessResponse)
async def process_subtitle(req: ProcessRequest):
    processor: SubtitleProcessor = app.state.processor

    root_from = req.root_from or os.environ.get("REMAP_ROOT_FROM")
    root_to = req.root_to or os.environ.get("REMAP_ROOT_TO")

    logger.info(
        "收到處理請求: path=%s root_from=%s root_to=%s dry_run=%s",
        req.subtitle_path,
        root_from,
        root_to,
        req.dry_run,
    )

    # ── 路徑重映射 ───────────────────────────────────────────
    try:
        remapped = processor.remap_input_path(
            input_path=req.subtitle_path,
            root_from=root_from,
            root_to=root_to,
        )
    except Exception as exc:
        logger.exception("路徑重映射失敗: %s", req.subtitle_path)
        raise HTTPException(status_code=500, detail=f"路徑重映射失敗: {exc}")

    # ── 前置驗證（在進 processor 前給出明確的 HTTP 狀態碼）───
    if not remapped.exists():
        logger.warning("字幕檔案不存在: %s（原始路徑: %s）", remapped, req.subtitle_path)
        raise HTTPException(
            status_code=404,
            detail=(
                f"字幕檔案不存在: {remapped}"
                + (f"（Bazarr 原始路徑: {req.subtitle_path}）" if root_from else "")
            ),
        )

    if not remapped.is_file():
        logger.warning("路徑不是檔案: %s", remapped)
        raise HTTPException(status_code=422, detail=f"路徑指向的不是檔案: {remapped}")

    return await _run_convert(processor, remapped, req.keep_original, req.dry_run)


async def _run_convert(
    processor: SubtitleProcessor,
    subtitle_path: Path,
    keep_original: bool,
    dry_run: bool,
) -> ProcessResponse:
    """共用的字幕轉換執行邏輯（/process 和 /process-by-id 都用）。"""
    if subtitle_path.suffix.lower() not in _SUPPORTED_EXTS:
        return ProcessResponse(
            status="skipped",
            message=f"不支援的副檔名 '{subtitle_path.suffix}'，僅支援 {sorted(_SUPPORTED_EXTS)}",
            input_path=str(subtitle_path),
            dry_run=dry_run,
        )

    if ".zh-tw" in subtitle_path.name.lower():
        return ProcessResponse(
            status="skipped",
            message="字幕已是 .zh-TW 格式，無需轉換",
            input_path=str(subtitle_path),
            dry_run=dry_run,
        )

    try:
        await asyncio.to_thread(
            processor.run,
            subtitle_path,
            not keep_original,
            dry_run,
        )
    except Exception as exc:
        logger.exception("字幕轉換失敗: %s", subtitle_path)
        raise HTTPException(status_code=500, detail=f"字幕轉換失敗: {exc}")

    logger.info("處理完成: %s", subtitle_path)
    return ProcessResponse(
        status="ok",
        message="字幕轉換完成" if not dry_run else "Dry-run 預覽完成（未寫入檔案）",
        input_path=str(subtitle_path),
        dry_run=dry_run,
    )


@app.post("/process-by-id", response_model=ProcessResponse)
async def process_subtitle_by_id(req: ProcessByIdRequest):
    """以 Sonarr/Radarr ID 定位影片，再找出對應的中文字幕進行繁簡轉換。

    Bazarr 後處理指令只需傳整數 ID，不經過 shell 引號邊界，徹底避免檔名含 ' 或 " 時被切斷。
    """
    processor: SubtitleProcessor = app.state.processor

    # 驗證 episode_id 是數字
    if not req.episode_id.strip().isdigit():
        raise HTTPException(status_code=422, detail="episode_id 必須是數字")

    # 透過 arr API 解析影片路徑
    try:
        video_path = await asyncio.to_thread(
            resolve_media_path, req.series_id, req.episode_id.strip()
        )
    except ArrError as exc:
        logger.error("arr API 錯誤: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    logger.info(
        "arr 解析: series_id=%s episode_id=%s → %s",
        req.series_id,
        req.episode_id,
        video_path,
    )

    # 路徑重映射（arr 回傳的路徑 → 本容器掛載路徑）
    root_from = os.environ.get("REMAP_ROOT_FROM")
    root_to = os.environ.get("REMAP_ROOT_TO")
    remapped = processor.remap_input_path(str(video_path), root_from, root_to)

    if not remapped.parent.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"影片所在資料夾不存在: {remapped.parent}",
        )

    # 在影片資料夾中找剛下載的中文字幕
    subtitle = processor.find_chinese_subtitle(remapped, language=req.language)
    if not subtitle:
        return ProcessResponse(
            status="skipped",
            message=f"未找到可轉換的中文字幕 (影片: {remapped.name})",
            input_path=str(remapped),
            dry_run=req.dry_run,
        )

    logger.info("定位到字幕: %s", subtitle)
    return await _run_convert(processor, subtitle, req.keep_original, req.dry_run)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 6768)),
        log_level="info",
    )
