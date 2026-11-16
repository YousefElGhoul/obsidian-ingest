"""Launch shared acquisition, curation-support, and draft operations over stdio MCP for harnesses."""

import sys

from obsidian_ingest.cli import main

if __name__ == "__main__":
    sys.argv[1:1] = ["mcp"]
    main()
