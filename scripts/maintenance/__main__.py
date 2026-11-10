"""数据运维入口：python -m scripts.maintenance <cmd> --help"""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "init-db": "scripts.maintenance.init_database",
    "migrate-db": "scripts.maintenance.migrate_database",
    "migrate-factors": "scripts.maintenance.migrate_factors",
    "repair-index": "scripts.maintenance.repair_index_data",
    "fix-index-series": "scripts.maintenance.fix_index_series",
    "verify-backfill": "scripts.maintenance.verify_pipeline",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.maintenance")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS), help="子命令")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="透传给子命令")
    ns = parser.parse_args(argv)
    if not ns.command:
        parser.print_help()
        print("\n可用子命令：")
        for name, mod in COMMANDS.items():
            print(f"  {name:<16} {mod}")
        return 0
    module = importlib.import_module(COMMANDS[ns.command])
    sys.argv = [ns.command, *ns.args]
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
