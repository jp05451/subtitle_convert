from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SubtitleCandidate:
    """
    字幕候選資訊結構 (Data Model)。
    將掃描到的字幕檔案封裝成統一的物件，
    方便後續進行跨物件的評分、比較與統一管理。
    """

    path: Path  # 字幕檔案的完整實體路徑 (用來讀取檔案內容與後續刪除操作)
    tag: str  # 從檔名中解析出的原始語言標籤 (例如 '.zh-TW', '.sc', '.chi')
    priority: int  # 該字幕檔案的優先權分數 (分數越高，越優先被選為轉換種子)
    quality_score: float = 0.0  # 新版綜合評分 (0-100)，由多指標加權產生
    score_breakdown: dict[str, float] = field(
        default_factory=dict
    )  # 每個分項分數，供 debug 排名輸出
