import re
from pathlib import Path
import opencc

from .models import SubtitleCandidate


class BaselineTagScorer:
    """
    基礎標籤評分器。
    根據寫死在系統裡的檔名標籤 (tag) 字典，對字幕進行配對與給分。
    這是為了相容過去舊有的簡單評分機制而保留的策略類別。
    """

    def __init__(self, tag_priority: dict[str, int]):
        # 接收外部定義的標籤與其對應分數的字典
        self.tag_priority = tag_priority

    def to_candidate(self, subtitle_path: Path) -> SubtitleCandidate | None:
        """
        嘗試將傳入的實體路徑轉換為具有分數的候選物件。
        如果檔名中不包含任何我們定義的中文字幕標籤，就會回傳 None (表示可以直接略過)。
        """
        lower_name = subtitle_path.name.lower()
        for tag, priority in self.tag_priority.items():
            if tag in lower_name:
                # 一旦命中標籤，就將它包裝成 Candidate 儲存下來
                return SubtitleCandidate(path=subtitle_path, tag=tag, priority=priority)
        return None


class BazarrStyleScorer:
    """
    Bazarr 風格的多指標評分器。
    權重配置如下：
    - 繁體比例 (50%)
    - 台灣詞彙在地化 (30%)
    - 字幕完整度 (20%)
    """

    def __init__(self):
        self.to_simplified = opencc.OpenCC("t2s")
        self.positive_terms = ["螢幕", "影片", "軟體", "程式", "品質", "網路"]
        self.negative_terms = ["屏幕", "视频", "软件", "程序", "质量", "网络"]

    def score_candidate(
        self,
        candidate: SubtitleCandidate,
        content: str | None,
        file_size: int,
        line_count: int,
    ) -> SubtitleCandidate:
        if not content:
            candidate.quality_score = 0.0
            candidate.score_breakdown = {
                "traditional_ratio": 0.0,
                "localization": 0.0,
                "integrity": self._integrity_score(
                    file_size=file_size, line_count=line_count
                ),
                "weighted_total": 0.0,
            }
            return candidate

        traditional_ratio = self._traditional_ratio(content)
        localization = self._localization_score(content)
        integrity = self._integrity_score(file_size=file_size, line_count=line_count)

        weighted_total = (
            traditional_ratio * 50.0 + localization * 30.0 + integrity * 20.0
        )

        candidate.quality_score = round(weighted_total, 3)
        candidate.score_breakdown = {
            "traditional_ratio": round(traditional_ratio, 4),
            "localization": round(localization, 4),
            "integrity": round(integrity, 4),
            "weighted_total": candidate.quality_score,
        }
        return candidate

    def _traditional_ratio(self, content: str) -> float:
        simplified = self.to_simplified.convert(content)
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", content)
        if not chinese_chars:
            return 0.0

        changed_count = 0
        chinese_count = 0
        for old_char, new_char in zip(content, simplified):
            if "\u4e00" <= old_char <= "\u9fff":
                chinese_count += 1
                if old_char != new_char:
                    changed_count += 1

        if chinese_count == 0:
            return 0.0
        return changed_count / chinese_count

    def _localization_score(self, content: str) -> float:
        positive_count = sum(content.count(term) for term in self.positive_terms)
        negative_count = sum(content.count(term) for term in self.negative_terms)
        total = positive_count + negative_count
        if total == 0:
            return 0.5
        return positive_count / total

    @staticmethod
    def _integrity_score(file_size: int, line_count: int) -> float:
        score = 0.0
        if line_count > 500:
            score += 0.5
        if file_size > 20 * 1024:
            score += 0.5
        return score


class SubtitleSelector:
    """
    最佳字幕挑選器。
    負責從一堆合格的字幕候選名單中，選出最優質的那一個作為轉換與同步的「核心種子」。
    """

    @staticmethod
    def select_best(candidates: list[SubtitleCandidate]) -> SubtitleCandidate:
        """
        新邏輯：先看多指標品質分數 (quality_score)，再看舊版標籤優先權 (priority) 作為 fallback。
        若仍同分，依靠 Python sorted 的穩定排序特性，保留一開始掃描到的順序。
        """
        return sorted(
            candidates,
            key=lambda x: (x.quality_score, x.priority),
            reverse=True,
        )[0]
