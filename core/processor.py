import re
from pathlib import Path
import xml.etree.ElementTree as ET
import opencc

from .models import SubtitleCandidate
from .scoring import BaselineTagScorer, BazarrStyleScorer, SubtitleSelector


class SubtitleProcessor:
    """
    字幕處理核心流程控制器。
    這是一個大總管，負責整合「掃描檔案」、「評分篩選」、「求生欲解碼」、「繁簡轉換」與「同步輸出」的完整任務。
    """

    def __init__(self):
        # 初始化 OpenCC 轉換器 (s2twp = 簡體轉繁體並自動套用台灣常用詞彙替換)
        self.converter = opencc.OpenCC("s2twp")

        # 定義系統認可的影片與字幕副檔名格式集合
        self.video_exts = {".mkv", ".mp4", ".avi", ".mov", ".wmv"}
        self.sub_exts = {".srt", ".ass"}

        # 定義標籤優先權字典：分數越高代表品質越好，越優先採用。
        # 原生繁體標籤給 2 分 (最省事)，簡體或難以辨識的通用中文標籤給 1 分 (需要轉譯)
        self.tag_priority = {
            ".tc": 2,
            ".big5": 2,
            "cht": 2,
            "zht": 2,
            ".sc": 1,
            ".zhs": 1,
            ".zho": 1,
            ".chi": 1,
            ".chs": 1,
            ".zh": 1,
        }
        # 建立評分器與最佳解答挑選器的實例
        self.tag_scorer = BaselineTagScorer(self.tag_priority)
        self.bazarr_scorer = BazarrStyleScorer()
        self.selector = SubtitleSelector()

    def _folder_videos(self, folder_path: Path) -> list[Path]:
        return [f for f in folder_path.iterdir() if f.suffix.lower() in self.video_exts]

    def _movie_identity(self, folder_path: Path) -> tuple[str, str] | None:
        """
        從資料夾內的 movie.nfo 解析影片身分鍵。
        目前使用 (title, year) 作為同片判定基準，任一缺失即視為無法判定。
        """
        nfo_path = folder_path / "movie.nfo"
        if not nfo_path.exists():
            return None

        try:
            root = ET.fromstring(nfo_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        title = (root.findtext("title") or "").strip().lower()
        year = (root.findtext("year") or "").strip()
        if not title or not year:
            return None
        return title, year

    def _build_identity_index(
        self, scan_root: Path
    ) -> dict[tuple[str, str], list[Path]]:
        """
        建立 movie.nfo 身分索引：同一個 (title, year) 會對應到多個版本資料夾 (如 1080/2160)。
        """
        index: dict[tuple[str, str], list[Path]] = {}
        for nfo_path in scan_root.rglob("movie.nfo"):
            folder = nfo_path.parent
            if not self._folder_videos(folder):
                continue
            identity = self._movie_identity(folder)
            if not identity:
                continue
            index.setdefault(identity, []).append(folder)
        return index

    def _pick_zh_tw_seed(self, folder_path: Path) -> Path | None:
        """
        從來源資料夾挑選可複製的 zh-TW 字幕。
        若有多份，優先挑體積較大的字幕當 seed，降低挑到殘缺檔的風險。
        """
        seeds = [
            f
            for f in folder_path.iterdir()
            if f.suffix.lower() in self.sub_exts and ".zh-tw" in f.name.lower()
        ]
        if not seeds:
            return None
        return sorted(seeds, key=lambda p: p.stat().st_size, reverse=True)[0]

    def _sync_subtitle_to_peer_folders(
        self,
        source_folder: Path,
        peer_folders: list[Path],
    ):
        """
        將來源資料夾的 zh-TW 字幕同步到同片其他資料夾。
        目標檔名規則：<video_stem>.zh-TW<subtitle_suffix>
        """
        seed = self._pick_zh_tw_seed(source_folder)
        if not seed:
            return

        content, _ = self._safe_read(seed)
        if not content:
            return

        for peer_folder in peer_folders:
            if peer_folder == source_folder:
                continue
            peer_videos = self._folder_videos(peer_folder)
            if not peer_videos:
                continue
            for video in peer_videos:
                target_name = f"{video.stem}.zh-TW{seed.suffix}"
                target_path = peer_folder / target_name
                target_path.write_text(content, encoding="utf-8")
                print(f"   🔄 已跨資料夾同步: {target_path}")

    def remap_input_path(
        self,
        input_path: str,
        root_from: str | None = None,
        root_to: str | None = None,
    ) -> Path:
        """
        Bazarr 路徑重寫工具。
        當 Bazarr 傳入的路徑使用容器內掛載根目錄時，可透過 root_from/root_to 轉換成主機實際路徑。
        """
        resolved = Path(input_path)
        if not root_from or not root_to:
            return resolved

        source_root = Path(root_from)
        target_root = Path(root_to)
        try:
            relative = resolved.relative_to(source_root)
            remapped = target_root / relative
            print(f"🔁 已重寫路徑: {resolved} -> {remapped}")
            return remapped
        except ValueError:
            print("⚠️ 路徑重寫略過：輸入路徑不在 root_from 範圍內。")
            return resolved

    def _safe_read(self, file_path: Path):
        """
        求生欲極強的檔案讀取方法。
        由於各大論壇的字幕編碼千奇百怪，這個方法會循序嘗試數種常見的中文編碼格式。
        如果一直到最後 utf-16 都失敗了才會放棄並回傳 None，確保程式不會因為遇到鳥編碼而中斷崩潰。
        """
        for enc in ["utf-8", "gb18030", "gbk", "big5", "utf-16"]:
            try:
                return file_path.read_text(encoding=enc), enc
            except Exception:
                continue
        return None, None

    def _to_zh_tw_path(self, subtitle_path: Path, found_tag: str | None = None) -> Path:
        """
        將字幕檔名標準化為 `.zh-TW` 尾綴。
        優先使用已命中的標籤替換；若未命中，則嘗試已知標籤；最後才直接附加 `.zh-TW`。
        """
        lower_name = subtitle_path.name.lower()
        if ".zh-tw" in lower_name:
            return subtitle_path

        if found_tag:
            replaced_name = re.sub(
                re.escape(found_tag),
                ".zh-TW",
                subtitle_path.name,
                count=1,
                flags=re.IGNORECASE,
            )
            if replaced_name != subtitle_path.name:
                return subtitle_path.with_name(replaced_name)

        for tag in sorted(self.tag_priority.keys(), key=len, reverse=True):
            replaced_name = re.sub(
                re.escape(tag),
                ".zh-TW",
                subtitle_path.name,
                count=1,
                flags=re.IGNORECASE,
            )
            if replaced_name != subtitle_path.name:
                return subtitle_path.with_name(replaced_name)

        fallback_name = f"{subtitle_path.stem}.zh-TW{subtitle_path.suffix}"
        return subtitle_path.with_name(fallback_name)

    def process_bazarr_subtitle(
        self,
        subtitle_path: Path,
        remove_original: bool = True,
        peer_folders: list[Path] | None = None,
    ):
        """
        Bazarr 單檔模式：只做純翻譯 + 尾綴標準化。
        不做資料夾內多檔評分、不做全資料夾同步分發。
        """
        print(f"🎯 Bazarr 單檔模式: {subtitle_path}")

        if not subtitle_path.exists() or not subtitle_path.is_file():
            print("   ❌ 找不到字幕檔案，略過。")
            return

        if subtitle_path.suffix.lower() not in self.sub_exts:
            print("   ⏩ 非支援字幕副檔名，略過。")
            return

        content, _ = self._safe_read(subtitle_path)
        if not content:
            print("   ❌ 字幕讀取失敗，略過。")
            return

        converted_content = self.converter.convert(content)

        candidate = self.tag_scorer.to_candidate(subtitle_path)
        found_tag = candidate.tag if candidate else None
        target_path = self._to_zh_tw_path(subtitle_path, found_tag=found_tag)

        target_path.write_text(converted_content, encoding="utf-8")
        print(f"   ✅ 已輸出繁中字幕: {target_path.name}")

        if remove_original and target_path != subtitle_path and subtitle_path.exists():
            subtitle_path.unlink()
            print(f"   🗑️ 已移除舊尾綴字幕: {subtitle_path.name}")

        # 預設開啟：若可判定同片兄弟資料夾，就同步過去。
        if peer_folders:
            self._sync_subtitle_to_peer_folders(
                source_folder=target_path.parent,
                peer_folders=peer_folders,
            )

    def process_bazarr_subtitle_auto_sync(
        self,
        subtitle_path: Path,
        remove_original: bool = True,
        scan_root: Path | None = None,
    ):
        """
        Bazarr 單檔模式 + 自動同片跨資料夾同步。
        會根據 movie.nfo 的 (title, year) 建立同片集合後再同步字幕。
        """
        if not scan_root:
            scan_root = (
                subtitle_path.parent.parent
                if subtitle_path.parent.parent.exists()
                else subtitle_path.parent
            )

        identity_index = self._build_identity_index(scan_root)
        identity = self._movie_identity(subtitle_path.parent)
        peer_folders = identity_index.get(identity, []) if identity else None
        self.process_bazarr_subtitle(
            subtitle_path=subtitle_path,
            remove_original=remove_original,
            peer_folders=peer_folders,
        )

    def scan_folders(self, scan_path: Path):
        """
        掃描模式：遞迴掃描資料夾並以既有的智能流程處理每個字幕資料夾。
        """
        if scan_path.is_file():
            self.process_folder_smart(scan_path.parent)
            return

        identity_index = self._build_identity_index(scan_path)

        processed_folders = set()
        for sub_file in scan_path.rglob("*"):
            if sub_file.suffix.lower() in self.sub_exts:
                folder = sub_file.parent
                if folder not in processed_folders:
                    self.process_folder_smart(folder)
                    identity = self._movie_identity(folder)
                    if identity and identity in identity_index:
                        self._sync_subtitle_to_peer_folders(
                            source_folder=folder,
                            peer_folders=identity_index[identity],
                        )
                    processed_folders.add(folder)

    def process_folder_smart(self, folder_path: Path):
        """
        以「資料夾」為單位的智能處理邏輯 (避免處理單一檔案結果產生重複多個繁體字幕的災難)：
        1. 找出並建立影片基準清單。
        2. 掃出所有中文字幕並打分數。
        3. 從中斬殺出最高分的字幕當作「種子」。
        4. 種子轉繁體後，同步分發 (複製) 給資料夾裡的所有影片檔。
        5. 清除多餘的歷史舊字幕，維護資料夾的純潔。
        """
        print(f"📂 正在掃描資料夾: {folder_path}")

        # 第一步：找出所有影片檔 (做為後續自動命名與對齊的參考點)
        videos = self._folder_videos(folder_path)

        # 第二步：篩選出所有附屬字幕，並透過 Scorer 審查其身分與打分 (產生 Candidate)
        all_subs = [
            f for f in folder_path.iterdir() if f.suffix.lower() in self.sub_exts
        ]
        chinese_subs: list[SubtitleCandidate] = []
        for sub in all_subs:
            candidate = self.tag_scorer.to_candidate(sub)
            if candidate:
                content, _ = self._safe_read(sub)
                file_size = sub.stat().st_size
                line_count = content.count("\n") + 1 if content else 0
                candidate = self.bazarr_scorer.score_candidate(
                    candidate=candidate,
                    content=content,
                    file_size=file_size,
                    line_count=line_count,
                )
                chinese_subs.append(candidate)

        # 若這資料夾清心寡慾沒有任何中文標籤的字幕，直接提前打完收工
        if not chinese_subs:
            print("   ⏩ 未發現中文字幕標籤，跳過。")
            return

        # 第三步：從所有候選名單中，依靠 Selector 挑出萬中選一的冠軍
        best_sub = self.selector.select_best(chinese_subs)
        print(
            f"   ⭐ 挑選最優種子: {best_sub.path.name} (綜合分數: {best_sub.quality_score:.3f}, 標籤權重: {best_sub.priority})"
        )

        ranked = sorted(
            chinese_subs,
            key=lambda x: (x.quality_score, x.priority),
            reverse=True,
        )
        print("   📊 候選排名明細：")
        for idx, item in enumerate(ranked, start=1):
            print(
                "      "
                f"{idx}. {item.path.name} | total={item.quality_score:.3f} "
                f"(trad={item.score_breakdown.get('traditional_ratio', 0.0):.4f}, "
                f"loc={item.score_breakdown.get('localization', 0.0):.4f}, "
                f"integrity={item.score_breakdown.get('integrity', 0.0):.4f}) | "
                f"tag_priority={item.priority}"
            )

        # 第四步：進行核心的：讀取檔案、簡繁轉換，然後平均分配寫出給每個影片檔
        self._convert_and_distribute(best_sub.path, best_sub.tag, folder_path, videos)

        # 第五步：大清洗，處理完最優的字幕之後，把剛剛落榜陪跑的其他舊中文標籤字幕通通砍掉
        for other in chinese_subs:
            # 安全防護：只刪除確實存在於硬碟的檔案，且絕對不砍殺我們剛產出的 `.zh-TW` 目標檔
            if other.path.exists() and ".zh-TW" not in other.path.name:
                other.path.unlink()
                print(f"   🗑️ 已清理冗餘字幕: {other.path.name}")

    def _convert_and_distribute(
        self, sub_path: Path, found_tag: str, folder: Path, videos: list
    ):
        """
        讀取冠軍字幕的內容，使用 OpenCC 對齊進行慣用語轉換，
        最後按照手上的影片檔名，一一輸出結尾為 .zh-TW 的標準字幕。
        """
        content, _ = self._safe_read(sub_path)
        if not content:
            # 如果嘗試了所有編碼還是讀進來一場空，就靜悄悄地退場
            return

        # 核心：執行強大的 OpenCC 轉換 (簡轉繁 + 台灣慣用詞彙大替換)
        converted_content = self.converter.convert(content)

        # 邊緣情境防護：如果這個資料夾很神奇地只有放置孤獨的字幕而沒有影片
        # 我們就將他原本的自定義標籤替換成國際標準的 .zh-TW 並且另存新檔即可
        if not videos:
            new_name = sub_path.name.replace(found_tag, ".zh-TW")
            (folder / new_name).write_text(converted_content, encoding="utf-8")
            return

        # 主要情境：針對每一部影片，依照影片檔名產出同源的標準繁體字幕
        for video in videos:
            target_sub_name = f"{video.stem}.zh-TW{sub_path.suffix}"
            target_path = folder / target_sub_name
            # 一律強制轉正，以真正的 utf-8 格式寫出檔案，解決未來讀取的相容性問題
            target_path.write_text(converted_content, encoding="utf-8")
            print(f"   ✅ 已產出/同步: {target_sub_name}")

    def run(
        self,
        input_path: str | Path,
        remove_original: bool = True,
    ):
        """
        系統統一入口。
        僅接收最終路徑與是否刪除原始字幕，其他參數前處理由上層 (main) 負責。
        """
        path = Path(input_path)

        # auto：檔案走 Bazarr 單檔流程；資料夾走掃描流程。
        if path.is_file():
            self.process_bazarr_subtitle_auto_sync(
                subtitle_path=path,
                remove_original=remove_original,
            )
            return

        self.scan_folders(path)
