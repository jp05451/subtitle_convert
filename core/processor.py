import re
import json
import subprocess
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

    def _movie_identity_keys(self, folder_path: Path) -> set[str]:
        """
        從資料夾內的 movie.nfo 解析可用的同步鍵。
        優先使用 title/year，並額外納入 tmdb/imdb id，讓跨版本資料夾配對更穩定。
        """
        nfo_path = folder_path / "movie.nfo"
        if not nfo_path.exists():
            return set()

        try:
            root = ET.fromstring(nfo_path.read_text(encoding="utf-8"))
        except Exception:
            return set()

        keys: set[str] = set()

        title = (root.findtext("title") or "").strip().lower()
        year = (root.findtext("year") or "").strip().lower()
        if title and year:
            keys.add(f"title:{title}|year:{year}")

        imdbid = (root.findtext("imdbid") or "").strip().lower()
        if imdbid:
            keys.add(f"imdb:{imdbid}")

        tmdbid = (root.findtext("tmdbid") or "").strip().lower()
        if tmdbid:
            keys.add(f"tmdb:{tmdbid}")

        for uniqueid in root.findall("uniqueid"):
            unique_value = (uniqueid.text or "").strip().lower()
            if not unique_value:
                continue

            unique_type = (uniqueid.get("type") or "").strip().lower()
            if unique_type in {"imdb", "tmdb"}:
                keys.add(f"{unique_type}:{unique_value}")
            elif unique_value.startswith("tt"):
                keys.add(f"imdb:{unique_value}")
            elif unique_value.isdigit():
                keys.add(f"tmdb:{unique_value}")

        return keys

    def _build_identity_index(self, scan_root: Path) -> dict[str, list[Path]]:
        """
        建立 movie.nfo 身分索引：同一組 title/year 或 tmdb/imdb id 會對應到多個版本資料夾。
        """
        index: dict[str, list[Path]] = {}
        for nfo_path in scan_root.rglob("movie.nfo"):
            folder = nfo_path.parent
            if not self._folder_videos(folder):
                continue
            for identity_key in self._movie_identity_keys(folder):
                index.setdefault(identity_key, []).append(folder)
        return index

    def _peer_folders_for_folder(
        self, folder_path: Path, identity_index: dict[str, list[Path]]
    ) -> list[Path]:
        """
        依照同一個資料夾可解析出的所有 identity keys，合併出兄弟資料夾清單。
        """
        peer_folders: list[Path] = []
        seen: set[Path] = set()
        for identity_key in self._movie_identity_keys(folder_path):
            for peer_folder in identity_index.get(identity_key, []):
                if peer_folder in seen:
                    continue
                peer_folders.append(peer_folder)
                seen.add(peer_folder)
        return peer_folders

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

    @staticmethod
    def _traditional_ratio_from_text(content: str) -> float:
        """
        估算文本中的繁體比例。
        透過 t2s 比對差異，比例越高代表文本越偏繁體。
        """
        if not content:
            return 0.0

        chinese_chars = re.findall(r"[\u4e00-\u9fff]", content)
        if not chinese_chars:
            return 0.0

        to_simplified = opencc.OpenCC("t2s")
        simplified = to_simplified.convert(content)

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

    @staticmethod
    def _extract_text_subtitle_sample(
        video_path: Path, stream_index: int, max_chars: int = 4000
    ) -> str | None:
        """
        以 ffmpeg 從指定字幕串流抽樣文本內容。
        只適用於文字型字幕串流；抽樣失敗時回傳 None。
        """
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    str(video_path),
                    "-map",
                    f"0:{stream_index}",
                    "-f",
                    "srt",
                    "-",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None

        if result.returncode != 0:
            return None

        sample = (result.stdout or "").strip()
        if not sample:
            return None
        return sample[:max_chars]

    def _has_embedded_traditional_zh_subtitle(self, video_path: Path) -> bool:
        """
        使用 ffprobe 檢查影片是否已經有內嵌「繁體中文」字幕。
        若有，外掛同步字幕會直接略過，避免覆蓋掉更穩定的內嵌版本。
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "s",
                    "-show_entries",
                    "stream=index,codec_name,tags",
                    "-of",
                    "json",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            # 環境沒有 ffprobe 或探測失敗時，回退為不阻擋同步。
            return False

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return False

        traditional_lang_tags = {"zh-tw", "zht", "cht", "zh-hant"}
        simplified_lang_tags = {"zh-cn", "zhs", "chs", "zh-hans"}
        ambiguous_zh_lang_tags = {"zh", "zho", "chi"}
        text_subtitle_codecs = {"subrip", "ass", "ssa", "mov_text", "webvtt"}
        traditional_title_keywords = (
            "繁體",
            "繁体",
            "traditional",
            "zh-tw",
            "zht",
            "cht",
            "big5",
        )
        simplified_title_keywords = (
            "简体",
            "簡中",
            "zh-cn",
            "zhs",
            "chs",
            "zh-hans",
        )

        for stream in payload.get("streams", []):
            tags = stream.get("tags", {})
            language = str(tags.get("language", "")).strip().lower()
            title = str(tags.get("title", "")).strip().lower()
            codec_name = str(stream.get("codec_name", "")).strip().lower()
            stream_index = stream.get("index")

            if language in traditional_lang_tags:
                return True

            if any(keyword in title for keyword in traditional_title_keywords):
                return True

            # 明確標示簡中時，直接視為非繁中並略過後續繁體判斷。
            if language in simplified_lang_tags:
                continue
            if any(keyword in title for keyword in simplified_title_keywords):
                continue

            # 對 zho/chi/zh 這類模糊語言標籤，若是文字字幕則抽樣內容估算繁體比例。
            if (
                language in ambiguous_zh_lang_tags
                and codec_name in text_subtitle_codecs
                and isinstance(stream_index, int)
            ):
                sample = self._extract_text_subtitle_sample(video_path, stream_index)
                if not sample:
                    continue

                ratio = self._traditional_ratio_from_text(sample)
                if ratio >= 0.08:
                    return True

        return False

    def _sync_subtitle_to_peer_folders(
        self,
        source_folder: Path,
        peer_folders: list[Path],
        dry_run: bool = False,
        seed_path: Path | None = None,
        seed_content: str | None = None,
    ):
        """
        將來源資料夾的 zh-TW 字幕同步到同片其他資料夾。
        目標檔名規則：<video_stem>.zh-TW<subtitle_suffix>
        """
        seed = seed_path or self._pick_zh_tw_seed(source_folder)
        if not seed:
            return

        content = seed_content
        if content is None:
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
                if self._has_embedded_traditional_zh_subtitle(video):
                    print(f"   ⏩ 偵測到內嵌繁中字幕，略過同步: {video.name}")
                    continue
                target_name = f"{video.stem}.zh-TW{seed.suffix}"
                target_path = peer_folder / target_name
                if dry_run:
                    print(f"   👀 dry-run 預覽同步: {target_path}")
                    continue
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
        dry_run: bool = False,
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

        if ".zh-tw" in subtitle_path.name.lower():
            print("   ⏩ 已經是 .zh-TW，略過。")
            return

        candidate = self.tag_scorer.to_candidate(subtitle_path)
        if not candidate:
            print("   ⏩ 非 zh 系中文字幕，略過。")
            return

        content, _ = self._safe_read(subtitle_path)
        if not content:
            print("   ❌ 字幕讀取失敗，略過。")
            return

        converted_content = self.converter.convert(content)

        found_tag = candidate.tag
        target_path = self._to_zh_tw_path(subtitle_path, found_tag=found_tag)

        if dry_run:
            print(f"   👀 dry-run 預覽輸出: {target_path}")
        else:
            target_path.write_text(converted_content, encoding="utf-8")
            print(f"   ✅ 已輸出繁中字幕: {target_path.name}")

        if (
            not dry_run
            and remove_original
            and target_path != subtitle_path
            and subtitle_path.exists()
        ):
            subtitle_path.unlink()
            print(f"   🗑️ 已移除舊尾綴字幕: {subtitle_path.name}")

        # 預設開啟：若可判定同片兄弟資料夾，就同步過去。
        if peer_folders:
            self._sync_subtitle_to_peer_folders(
                source_folder=target_path.parent,
                peer_folders=peer_folders,
                dry_run=dry_run,
                seed_path=target_path,
                seed_content=converted_content,
            )

    def process_bazarr_subtitle_auto_sync(
        self,
        subtitle_path: Path,
        remove_original: bool = True,
        scan_root: Path | None = None,
        dry_run: bool = False,
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
        peer_folders = self._peer_folders_for_folder(
            subtitle_path.parent, identity_index
        )
        self.process_bazarr_subtitle(
            subtitle_path=subtitle_path,
            remove_original=remove_original,
            peer_folders=peer_folders or None,
            dry_run=dry_run,
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
                    peer_folders = self._peer_folders_for_folder(folder, identity_index)
                    if peer_folders:
                        self._sync_subtitle_to_peer_folders(
                            source_folder=folder,
                            peer_folders=peer_folders,
                        )
                    processed_folders.add(folder)

    def scan_folders_dry_run(self, scan_path: Path):
        """
        掃描模式的 dry-run：只預覽每個資料夾最後會採用哪個字幕與輸出檔名。
        """
        if scan_path.is_file():
            self.process_folder_smart(scan_path.parent, dry_run=True)
            return

        processed_folders = set()
        for sub_file in scan_path.rglob("*"):
            if sub_file.suffix.lower() in self.sub_exts:
                folder = sub_file.parent
                if folder not in processed_folders:
                    self.process_folder_smart(folder, dry_run=True)
                    processed_folders.add(folder)

    def process_folder_smart(self, folder_path: Path, dry_run: bool = False):
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
        self._convert_and_distribute(
            best_sub.path,
            best_sub.tag,
            folder_path,
            videos,
            dry_run=dry_run,
        )

        # 第五步：大清洗，處理完最優的字幕之後，把剛剛落榜陪跑的其他舊中文標籤字幕通通砍掉
        if dry_run:
            print("   👀 dry-run 模式：略過冗餘字幕清理。")
            return

        for other in chinese_subs:
            # 安全防護：只刪除確實存在於硬碟的檔案，且絕對不砍殺我們剛產出的 `.zh-TW` 目標檔
            if other.path.exists() and ".zh-TW" not in other.path.name:
                other.path.unlink()
                print(f"   🗑️ 已清理冗餘字幕: {other.path.name}")

    def _convert_and_distribute(
        self,
        sub_path: Path,
        found_tag: str,
        folder: Path,
        videos: list,
        dry_run: bool = False,
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
            target_path = folder / new_name
            if dry_run:
                print(f"   👀 dry-run 預覽輸出: {target_path}")
                return
            target_path.write_text(converted_content, encoding="utf-8")
            print(f"   ✅ 已產出/同步: {new_name}")
            return

        # 主要情境：針對每一部影片，依照影片檔名產出同源的標準繁體字幕
        for video in videos:
            target_sub_name = f"{video.stem}.zh-TW{sub_path.suffix}"
            target_path = folder / target_sub_name
            if dry_run:
                print(f"   👀 dry-run 預覽輸出: {target_path}")
                continue
            # 一律強制轉正，以真正的 utf-8 格式寫出檔案，解決未來讀取的相容性問題
            target_path.write_text(converted_content, encoding="utf-8")
            print(f"   ✅ 已產出/同步: {target_sub_name}")

    def run(
        self,
        input_path: str | Path,
        remove_original: bool = True,
        dry_run: bool = False,
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
                dry_run=dry_run,
            )
            return

        if dry_run:
            self.scan_folders_dry_run(path)
            return

        self.scan_folders(path)
