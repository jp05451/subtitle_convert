import argparse
from pathlib import Path
from core.processor import SubtitleProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="字幕轉換工具：Bazarr 單檔字幕翻譯入口。"
    )
    parser.add_argument(
        "--bazarr-subtitle",
        required=True,
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
        "--dry-run",
        action="store_true",
        help="只預覽處理結果，不寫入、不刪除任何檔案。",
    )
    return parser


if __name__ == "__main__":
    processor = SubtitleProcessor()

    parser = build_parser()
    args = parser.parse_args()

    run_input = processor.remap_input_path(
        input_path=args.bazarr_subtitle,
        root_from=args.bazarr_root_from,
        root_to=args.bazarr_root_to,
    )

    if not Path(run_input).is_file():
        parser.error(
            "--bazarr-subtitle 必須是字幕檔案路徑，不能是資料夾。"
        )

    processor.run(
        input_path=run_input,
        remove_original=not args.keep_original,
        dry_run=args.dry_run,
    )
