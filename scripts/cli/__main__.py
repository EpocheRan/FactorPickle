"""辅助命令入口：python -m scripts.cli <command> [args...]

因子报告已迁到 ``python -m research.report``；这里只保留 profile 和股票池导出。
"""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "profile": ("scripts.cli.profile", "报告章节 profile 管理"),
    "export-pool": ("scripts.cli.export_pool", "导出股票池"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.cli")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS))
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)
    if not ns.command:
        parser.print_help()
        print("\n可用子命令：")
        for name, (_mod, desc) in COMMANDS.items():
            print(f"  {name:<16} {desc}")
        return 0
    module = importlib.import_module(COMMANDS[ns.command][0])
    sys.argv = [ns.command, *ns.args]
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
