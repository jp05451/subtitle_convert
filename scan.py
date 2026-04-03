import argparse

from core.processor import SubtitleProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="字幕轉換工具：資料夾掃描入口。")
    parser.add_argument(
        "--scan-path",
        help="掃描模式：遞迴掃描此資料夾中的字幕並處理。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只預覽處理結果，不寫入、不刪除任何檔案。",
    )
    parser.add_argument(
        "legacy_path",
        nargs="?",
        help="相容舊參數：直接傳入路徑。未提供時預設為 /movies。",
    )
    return parser


if __name__ == "__main__":
    processor = SubtitleProcessor()

    parser = build_parser()
    args = parser.parse_args()

    run_input = args.scan_path or args.legacy_path or "/movies"

    processor.run(
        input_path=run_input,
        dry_run=args.dry_run,
    )
