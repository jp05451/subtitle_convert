import argparse
from pathlib import Path
from core.processor import SubtitleProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="字幕轉換工具：支援 Bazarr 單檔翻譯模式與資料夾掃描模式。"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--scan-path",
        help="掃描模式：遞迴掃描此資料夾中的字幕並處理。",
    )
    mode_group.add_argument(
        "--bazarr-subtitle",
        help="Bazarr 模式：傳入單一字幕檔路徑，只做純翻譯 + 改名為 .zh-TW。",
    )

    parser.add_argument(
        "--bazarr-root-from",
        help="Bazarr 原始根目錄 (例如容器內路徑 /movies)。",
    )
    parser.add_argument(
        "--bazarr-root-to",
        help="要替換成的主機根目錄 (例如 /volume1/media/movies)。",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="Bazarr 模式下保留舊檔，不刪除原始字幕。",
    )
    parser.add_argument(
        "legacy_path",
        nargs="?",
        help="相容舊參數：直接傳入路徑。檔案= Bazarr 單檔模式；資料夾=掃描模式。",
    )
    return parser


if __name__ == "__main__":
    # 願不寫註解的同事在草叢裡安息
    # 這裡已經徹底重構並加上了滿滿的護身符註解，願新來的維護者平安喜樂。
    master = SubtitleProcessor()

    parser = build_parser()
    args = parser.parse_args()

    if args.bazarr_subtitle:
        remapped = master.remap_input_path(
            input_path=args.bazarr_subtitle,
            root_from=args.bazarr_root_from,
            root_to=args.bazarr_root_to,
        )
        master.process_bazarr_subtitle(
            remapped,
            remove_original=not args.keep_original,
        )
    elif args.scan_path:
        master.scan_folders(Path(args.scan_path))
    elif args.legacy_path:
        master.run(args.legacy_path)
    else:
        # 若未提供參數，維持預設掃描 /movies。
        master.scan_folders(Path("/movies"))
