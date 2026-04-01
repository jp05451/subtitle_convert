import os
import sys
import shutil
from pathlib import Path
import opencc


class SubtitleProcessor:
    def __init__(self):
        self.converter = opencc.OpenCC("s2twp.json")
        self.video_exts = {".mkv", ".mp4", ".avi", ".mov", ".wmv"}
        self.sub_exts = {".srt", ".ass"}

        # 定義標籤優先級：分數越高越優先
        # 原生繁體標籤給 2 分，簡體/通用標籤給 1 分
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

    def _safe_read(self, file_path: Path):
        for enc in ["utf-8", "gb18030", "gbk", "big5", "utf-16"]:
            try:
                return file_path.read_text(encoding=enc), enc
            except Exception:
                continue
        return None, None

    def process_folder_smart(self, folder_path: Path):
        """
        以資料夾為單位進行智能掃描，避免重複產生多個繁體字幕。
        """
        print(f"📂 正在掃描資料夾: {folder_path}")

        # 1. 找出所有影片檔 (作為同步基準)
        videos = [
            f for f in folder_path.iterdir() if f.suffix.lower() in self.video_exts
        ]

        # 2. 找出所有可能的中文標籤字幕
        all_subs = [
            f for f in folder_path.iterdir() if f.suffix.lower() in self.sub_exts
        ]
        chinese_subs = []
        for sub in all_subs:
            for tag, priority in self.tag_priority.items():
                if tag in sub.name.lower():
                    chinese_subs.append({"path": sub, "priority": priority, "tag": tag})
                    break

        if not chinese_subs:
            print("   ⏩ 未發現中文字幕標籤，跳過。")
            return

        # 3. 排序：優先處理分數高的（原生繁體），同分的則按檔名排
        chinese_subs.sort(key=lambda x: x["priority"], reverse=True)

        # 4. 執行處理：我們只取「最優」的那一份來做種子，生成所有影片需要的字幕
        best_sub = chinese_subs[0]
        print(
            f"   ⭐ 挑選最優種子: {best_sub['path'].name} (權重: {best_sub['priority']})"
        )

        self._convert_and_distribute(
            best_sub["path"], best_sub["tag"], folder_path, videos
        )

        # 5. 清理 (選選)：處理完最優的，剩下的舊中文標籤字幕可以刪除，保持資料夾乾淨
        for other in chinese_subs:
            # 只有當檔案還存在且不是我們剛產出的檔名時才刪除
            if other["path"].exists() and ".zh-TW" not in other["path"].name:
                other["path"].unlink()
                print(f"   🗑️ 已清理冗餘字幕: {other['path'].name}")

    def _convert_and_distribute(
        self, sub_path: Path, found_tag: str, folder: Path, videos: list
    ):
        """讀取最優字幕，轉換內容，並分發給所有影片版本"""
        content, _ = self._safe_read(sub_path)
        if not content:
            return

        # 轉換
        converted_content = self.converter.convert(content)

        # 如果沒有影片檔（可能只有單獨字幕），就產生一個標準命名的繁體字幕
        if not videos:
            new_name = sub_path.name.replace(found_tag, ".zh-TW")
            (folder / new_name).write_text(converted_content, encoding="utf-8")
            return

        # 為每一個影片檔產生對應的繁體字幕
        for video in videos:
            target_sub_name = f"{video.stem}.zh-TW{sub_path.suffix}"
            target_path = folder / target_sub_name
            target_path.write_text(converted_content, encoding="utf-8")
            print(f"   ✅ 已產出/同步: {target_sub_name}")

    def run(self, input_path: str):
        path = Path(input_path)
        if path.is_file():
            # 如果是 Bazarr 傳入單檔，我們直接處理該檔案所在的資料夾，實行智能去重
            self.process_folder_smart(path.parent)
        else:
            # 如果是手動全掃描，遞迴尋找含有影片的資料夾
            processed_folders = set()
            for sub_file in path.rglob("*"):
                if sub_file.suffix.lower() in self.sub_exts:
                    folder = sub_file.parent
                    if folder not in processed_folders:
                        self.process_folder_smart(folder)
                        processed_folders.add(folder)


if __name__ == "__main__":
    # 願不寫註解的同事在草叢裡安息
    master = SubtitleProcessor()
    if len(sys.argv) > 1:
        master.run(sys.argv[1])
    else:
        master.run("/movies")
