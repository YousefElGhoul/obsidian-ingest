"""Stdio MCP entry point for harness configuration."""

import sys

from obsidian_ingest.cli import main

if __name__ == "__main__":
    sys.argv[1:1] = ["mcp"]
    main()
