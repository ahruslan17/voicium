from __future__ import annotations

import argparse
from collections.abc import Sequence

from voicium import __version__
from voicium.config import AppConfig, default_config_path
from voicium.healthcheck import has_failures, render_results
from voicium.healthcheck import run_healthcheck as collect_healthcheck


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voicium",
        description="Fast Russian push-to-talk dictation for Ubuntu.",
    )
    parser.add_argument("--version", action="version", version=f"voicium {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    healthcheck_parser = subparsers.add_parser(
        "healthcheck",
        help="Print Ubuntu environment diagnostics.",
    )
    healthcheck_parser.set_defaults(handler=run_healthcheck)

    config_parser = subparsers.add_parser("config", help="Inspect Voicium configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_show_parser = config_subparsers.add_parser("show", help="Print default config values.")
    config_show_parser.set_defaults(handler=show_config)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(args))


def run_healthcheck(_args: argparse.Namespace) -> int:
    config_path = default_config_path()
    print(f"Config path: {config_path}")
    results = collect_healthcheck()
    print(render_results(results))
    return 1 if has_failures(results) else 0


def show_config(_args: argparse.Namespace) -> int:
    config = AppConfig.default()
    print(config.to_toml())
    return 0
