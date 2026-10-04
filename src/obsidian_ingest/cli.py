import argparse

from obsidian_ingest.extractor.transcript import print_transcript


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Video URL")
    parser.add_argument(
        "--inspect-chunks",
        action="store_true",
        help="Inspect sponsor-filtered, chapter-aligned baseline chunks (no model calls)",
    )
    args = parser.parse_args()

    if args.inspect_chunks:
        from obsidian_ingest.segmentation import format_segmentation, inspect_source

        print(format_segmentation(inspect_source(args.url)))
    else:
        print_transcript(args.url)
