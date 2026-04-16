#!/usr/bin/env python3
"""
轻量占位脚本。
当前版本仅保留为后续扩展入口，避免后续目录结构变化。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main() -> int:
    print("taskctl.py 当前为占位版本。")
    print(f"项目根目录: {ROOT}")
    print("后续可扩展为自动创建任务卡、开发记录、审核记录、验证记录与索引更新。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
