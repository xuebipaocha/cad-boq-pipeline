# -*- coding: utf-8 -*-
"""术语统一: 工程性质「大修与改造」→「改造」— v6.10.1

背景(用户 2026-08-19 口径确认): **项目类型只有两类 —— 新建 / 改造**, 不叫"大修"。
处理原则:
- 只替换**分类取值**与**面向用户的表述**: '大修与改造' → '改造';
- **保留**"大修"作为**识别线索词**(图纸写"大修工程"仍是改造的强信号)及其在关键词表中的权重;
- 历史数据兼容: 由 step1 的 normalize_nature() 把旧值 '大修与改造'/'大修' 归一为 '改造'。

用法:
  python3 tools/rename_renovation_term.py            # 预览(dry-run)
  python3 tools/rename_renovation_term.py --apply    # 实际写入
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

SKILL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE = os.path.dirname(SKILL)

OLD = '大修与改造'
NEW = '改造'

TARGETS = [
    os.path.join(SKILL, 'scripts', '*.py'),
    os.path.join(SKILL, 'scripts', 'pipeline', '*.py'),
    os.path.join(SKILL, 'scripts', 'pipeline', 'rules', '*.py'),
    os.path.join(SKILL, 'scripts', 'tools', '*.py'),
    os.path.join(SKILL, '*.md'),
    os.path.join(WORKSPACE, 'AGENTS.md'),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写入(默认仅预览)')
    args = ap.parse_args(argv)

    files = []
    for pat in TARGETS:
        files.extend(sorted(glob.glob(pat)))
    total_files = total_hits = 0
    for path in files:
        if os.path.basename(path) == os.path.basename(__file__):
            continue  # 跳过本脚本自身(其 OLD/NEW 常量与注释含目标字符串)
        try:
            with open(path, encoding='utf-8') as f:
                src = f.read()
        except Exception:
            continue
        n = src.count(OLD)
        if not n:
            continue
        total_files += 1
        total_hits += n
        rel = os.path.relpath(path, WORKSPACE)
        print(f'  {rel}: {n} 处')
        if args.apply:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(src.replace(OLD, NEW))
    print(f'合计: {total_files} 个文件, {total_hits} 处 「{OLD}」→「{NEW}」'
          f'{"（已写入）" if args.apply else "（预览，加 --apply 写入）"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
