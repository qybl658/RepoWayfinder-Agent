"""Compatibility entry point for project-local Grok registration."""
from __future__ import annotations

import sys

from configure_clients import main as configure_main


if __name__ == "__main__":
    raise SystemExit(configure_main(["--clients", "grok", "--scope", "project", "--json", *sys.argv[1:]]))
