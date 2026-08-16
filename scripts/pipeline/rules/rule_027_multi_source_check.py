# -*- coding: utf-8 -*-
"""多来源专业互核规则 — v6.9 R027

真造价师会主动找矛盾: 门窗表数量 vs 平面图门窗符号、构件表 vs 平面图柱位。
多来源数据对不上 → 自动生成图纸疑问(带影响提示)。

检查项:
1. 门窗表总樘数 vs 平面图门窗图块/符号数 — 差异大 → 疑问
2. 钢结构构件表数量 vs 平面图柱脚/构件符号数 — 差异 → 疑问
3. 面积三源核对(v6.3 已有) 不重复
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase


class MultiSourceCheckRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '多来源互核: 门窗表/构件表 vs 平面图符号'
        self.prerequisites = ['施工说明']

    def check(self, drawing_data):
        problems = []
        # 1. 门窗表 vs 平面图门窗符号
        windows = drawing_data.get('门窗', []) or []
        wd_total = sum(int(w.get('数量', 1) or 1) for w in windows)
        blocks = drawing_data.get('图块明细', {}) or {}
        # 门窗图块: 块名含 M/C/LC/门/窗 或 门窗类图块
        wd_blocks = 0
        for cat in ('门窗', '门', '窗'):
            for item in blocks.get(cat, []) or []:
                if isinstance(item, dict):
                    wd_blocks += int(item.get('count', 0) or 0)
        cad_blocks = (drawing_data.get('CAD分析') or {}).get('blocks', {}) or {}
        for key in ('door_blocks', 'window_blocks'):
            d = cad_blocks.get(key, {}) or {}
            if isinstance(d, dict):
                wd_blocks += sum(d.values())
        if wd_total > 0 and wd_blocks > 0:
            diff = abs(wd_total - wd_blocks)
            if diff >= max(3, int(wd_total * 0.1)):
                problems.append({
                    '类别': '多源互核', '严重程度': '中',
                    '位置': '门窗表 vs 平面图',
                    '问题': f'设计门窗表 {wd_total} 樘 vs 平面图门窗符号 {wd_blocks} 个, 差异 {diff}',
                    '建议': '以门窗表为准(权威来源), 复核平面图门窗符号提取是否漏检',
                })
        elif wd_total > 0 and wd_blocks == 0:
            problems.append({
                '类别': '多源互核', '严重程度': '低',
                '位置': '门窗表 vs 平面图',
                '问题': f'门窗表 {wd_total} 樘但平面图未检出门窗符号, 几何提取可能失效',
                '建议': '复核平面图门窗块/符号的图层与图块名',
            })

        # 2. 钢结构构件表 vs 平面图柱位符号
        steel = (drawing_data.get('钢结构') or {}).get('构件', []) or []
        if steel:
            n_members = len(steel)
            n_marks = 0
            for it in (blocks.get('柱脚', []) or []) + (blocks.get('柱', []) or []):
                if isinstance(it, dict):
                    n_marks += int(it.get('count', 0) or 0)
            if n_marks > 0 and n_members > 0 and abs(n_members - n_marks) >= max(3, int(n_members * 0.15)):
                problems.append({
                    '类别': '多源互核', '严重程度': '中',
                    '位置': '构件表 vs 平面图',
                    '问题': f'钢构件类型 {n_members} 类 vs 平面图柱位/构件符号 {n_marks} 个, 差异明显',
                    '建议': '核对构件编号(GZ1/GL1)与平面图布置是否一致, 确认构件数量',
                })
        return problems
