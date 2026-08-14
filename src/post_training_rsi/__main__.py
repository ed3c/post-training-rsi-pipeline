from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .config import PipelineConfig
from .engine import build_default_engine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="post-training-rsi")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("artifacts/demo"))
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="run the dependency-free RSI demonstration")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = PipelineConfig.load(args.config)
    if args.command == "demo":
        result = build_default_engine(config, workspace=args.workspace).run()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
