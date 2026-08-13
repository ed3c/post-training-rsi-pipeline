from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import PipelineConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="post-training-rsi")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("artifacts/demo"))
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    PipelineConfig.load(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
