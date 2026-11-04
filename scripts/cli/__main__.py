"""统一 CLI 入口：python -m scripts.cli <command> [args...]

每个子命令都是薄封装，实现在 research/ 对应模块的 ``cli.main``。
"""

from __future__ import annotations

import argparse
import importlib
import sys

COMMANDS = {
    "factor-report": ("scripts.cli.factor_report", "单因子报告 / 批量报告"),
    "factor-eval": ("scripts.cli.factor_eval", "批量因子评估（IC/价差/换手）"),
    "factor-score": ("scripts.cli.factor_score", "快速评分（不生成 HTML）"),
    "profile": ("scripts.cli.profile", "评估 profile 管理"),
    "export-pool": ("scripts.cli.export_pool", "导出股票池"),
    "verify-equivalence": ("scripts.cli.verify_equivalence", "新旧评估口径等价性回放"),
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
