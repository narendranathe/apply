#!/usr/bin/env python3
"""Compatibility entry point: cloud application claims belong to one writer."""
import sys


def main():
    print("BLOCKED: this local claim command is retired. Dispatch the applications lane through cloud.orchestrator. It checks authoritative Notion history, verifies the official requisition, and records an immutable claim. JSON exports are never live application state.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
