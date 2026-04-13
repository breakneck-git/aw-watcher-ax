import argparse
import logging

from . import __version__
from .config import load_config
from .watcher import run

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aw-watcher-ax")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once, emit one heartbeat, print result, and exit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config()
    except (FileNotFoundError, ValueError) as e:
        log.error("config error: %s", e)
        return 2

    try:
        return run(cfg, once=args.once)
    except KeyboardInterrupt:
        return 0
