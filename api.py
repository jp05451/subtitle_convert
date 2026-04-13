import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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

    if remapped.suffix.lower() not in _SUPPORTED_EXTS:
        logger.info("不支援的副檔名，略過: %s", remapped.name)
        return ProcessResponse(
            status="skipped",
            message=f"不支援的副檔名 '{remapped.suffix}'，僅支援 {sorted(_SUPPORTED_EXTS)}",
            input_path=str(remapped),
            dry_run=req.dry_run,
        )

    if ".zh-tw" in remapped.name.lower():
        logger.info("已是 zh-TW 字幕，略過: %s", remapped.name)
        return ProcessResponse(
            status="skipped",
            message="字幕已是 .zh-TW 格式，無需轉換",
            input_path=str(remapped),
            dry_run=req.dry_run,
        )

    # ── 執行轉換（用 asyncio.to_thread 避免阻塞 event loop）─
    try:
        await asyncio.to_thread(
            processor.run,
            remapped,
            not req.keep_original,
            req.dry_run,
        )
    except Exception as exc:
        logger.exception("字幕轉換失敗: %s", remapped)
        raise HTTPException(status_code=500, detail=f"字幕轉換失敗: {exc}")

    logger.info("處理完成: %s", remapped)
    return ProcessResponse(
        status="ok",
        message="字幕轉換完成" if not req.dry_run else "Dry-run 預覽完成（未寫入檔案）",
        input_path=str(remapped),
        dry_run=req.dry_run,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 6768)),
        log_level="info",
    )
