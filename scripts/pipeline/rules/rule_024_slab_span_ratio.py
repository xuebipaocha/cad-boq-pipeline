# -*- coding: utf-8 -*-
"""规范审查规则: 板厚跨比 — v5.0 P3

GB 50010-2010 §8.3.1: 双向板厚跨比 1/40(简支)~1/30(连续)。
板跨取构件模型梁的最大长度; 无梁数据 → 跳过。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
from pipeline.rules.spec_db import find_rules


class SlabSpanRatioRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '板厚跨比规范审查(GB 50010 8.3.1: 1/30~1/40)'
        self.prerequisites = ['构件模型']

    def check(self, drawing_data):
        problems = []
        cm = drawing_data.get('构件模型', {}) or {}
        slabs = cm.get('板', []) or []
        beams = cm.get('梁', []) or []
        if not slabs or not beams:
            return problems
        rules = find_rules('slab_span_ratio', '板')
        if not rules:
            return problems
        lo_rule = next((r for r in rules if r.get('value_type') == 'min'), None)
        hi_rule = next((r for r in rules if r.get('value_type') == 'max'), None)
        if not lo_rule and not hi_rule:
            return problems
        # v5.1: 板跨取单跨跨度(梁长/跨数), 与 rule_021 同口径 — 多跨梁不按整跨审查
        spans = [b.get('长度_m', 0) / max(int(b.get('跨数') or 1), 1) for b in beams if b.get('长度_m')]
        span = max(spans, default=0)
        if span <= 0:
            return problems
        for s in slabs:
            thick = s.get('厚度_mm')
            if not thick:
                continue
            ratio = thick / (span * 1000)
            lo, hi = lo_rule['param_value'] if lo_rule else None, hi_rule['param_value'] if hi_rule else None
            if lo and ratio < lo - 1e-2:
                problems.append({
                    '问题': f"{s.get('编号', '板')} 板厚跨比 1/{1/ratio:.1f}({thick}mm/{span}m) 低于规范下限 1/{1/lo:.1f}",
                    '位置': f"构件模型.板.{s.get('编号', '?')}",
                    '类别': '规范审查',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': f"加大板厚或增设梁/次梁减小跨度({lo_rule['standard_code']} {lo_rule['clause']})",
                })
            elif hi and ratio > hi + 1e-2:
                problems.append({
                    '问题': f"{s.get('编号', '板')} 板厚跨比 1/{1/ratio:.1f}({thick}mm/{span}m) 高于规范上限 1/{1/hi:.1f}",
                    '位置': f"构件模型.板.{s.get('编号', '?')}",
                    '类别': '规范审查',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': f"可减薄板厚或调整跨度({hi_rule['standard_code']} {hi_rule['clause']})",
                })
        return problems
