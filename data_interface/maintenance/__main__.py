"""数据运维入口：python -m data_interface.maintenance <cmd> --help

各子命令保持原有脚本参数，只做转发，不含业务逻辑。
"""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "init-db": "data_interface.maintenance.init_database",
    "migrate-db": "data_interface.maintenance.migrate_database",
    "migrate-factors": "data_interface.maintenance.migrate_factors",
    "repair-index": "data_interface.maintenance.repair_index_data",
    "fix-index-series": "data_interface.maintenance.fix_index_series",
    "verify-backfill": "data_interface.maintenance.verify_p0_p2",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m data_interface.maintenance")
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
