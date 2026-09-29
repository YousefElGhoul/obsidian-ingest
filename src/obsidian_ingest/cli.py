import argparse

from obsidian_ingest.extractor.transcript import print_transcript


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Video URL")
    args = parser.parse_args()

    print_transcript(args.url)
