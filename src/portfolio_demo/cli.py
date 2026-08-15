"""Command-line entry point for the synthetic/offline portfolio demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analysis import analyze_snapshot
from .report import render_markdown


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a local Questrade-shaped snapshot.")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concentration-limit", type=float, default=0.20)
    parser.add_argument("--event-sleeve-limit", type=float, default=0.15)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = _load_object(args.snapshot)
    catalog = _load_object(args.catalog) if args.catalog else {"securities": {}}
    analysis = analyze_snapshot(
        snapshot,
        catalog,
        concentration_limit=args.concentration_limit,
        event_sleeve_limit=args.event_sleeve_limit,
    )
    report = render_markdown(analysis, generated_at=str(snapshot.get("generatedAt") or "unknown"))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote report: {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
