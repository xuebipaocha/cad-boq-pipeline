# -*- coding: utf-8 -*-
"""规范审查规则: 梁高跨比 — v5.0 P3 / v5.1 多跨修正

GB 50010-2010 §8.3.1: 梁高跨比 1/8~1/12; GB 50011-2010 §6.3.3: 抗震 1/10。
读构件模型的梁对象; 长度/截面缺失 → 跳过该梁(不误报)。

v5.1: 多跨梁按单跨审查 — 跨度 = 梁长 / 跨数(平法跨数, 如 KL1(3) 24m → 单跨 8m),
修复合规多跨梁按整跨 24m 误报超限的问题。条文为"宜"级(推荐区间),
超限报出阈值带 15% 容差(规范允许设计人员在合理范围内突破推荐值)。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
from pipeline.rules.spec_db import find_rules

# "宜"级条文容差: 突破推荐区间 15% 才报出(推荐值非强制)
RATIO_TOL = 0.15


class BeamSpanRatioRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '梁高跨比规范审查(GB 50010 8.3.1: 1/8~1/12)'
        self.prerequisites = ['构件模型']

    def check(self, drawing_data):
        problems = []
        beams = (drawing_data.get('构件模型', {}) or {}).get('梁', []) or []
        if not beams:
            return problems
        rules = find_rules('h_span_ratio', '梁')
        if not rules:
            return problems
        # 抗震条文(GB 50011)只在图纸注明抗震时启用 — 非抗震设计不受抗震限值约束
        texts = ' '.join(drawing_data.get('施工说明', []) or [])
        is_seismic = any(k in texts for k in ('抗震', '设防烈度', 'SEISMIC', 'seismic'))
        for b in beams:
            h = b.get('截面高_mm')
            ln = b.get('长度_m')
            if not h or not ln:
                continue
            # v5.1: 多跨梁单跨跨度 = 梁长/跨数(平法跨数, 如 KL1(3) → 24/3=8m)
            spans = max(int(b.get('跨数') or 1), 1)
            span_len = ln / spans
            h_eff = h
            if h_eff <= 0:
                continue
            ratio = h_eff / (span_len * 1000)
            for rule in rules:
                if rule.get('standard_code', '').startswith('GB 50011') and not is_seismic:
                    continue  # 抗震条文跳过(非抗震设计)
                lo, hi = rule.get('param_value'), rule.get('range_max')
                if rule.get('value_type') == 'range' and lo and hi:
                    # "宜"级条文: 突破推荐区间 15% 才报出
                    if ratio < lo * (1 - RATIO_TOL) or ratio > hi * (1 + RATIO_TOL):
                        problems.append({
                            '问题': f"{b.get('编号', '梁')} 梁高跨比 1/{1/ratio:.1f} 超出规范 1/{1/lo:.1f}~1/{1/hi:.1f}(梁高{h}mm, 单跨{span_len:.1f}m)",
                            '位置': f"构件模型.梁.{b.get('编号', '?')}",
                            '类别': '规范审查',
                            '影响造价': True,
                            '严重程度': '中',
                            '建议': f"加大梁高或减小跨度({rule['standard_code']} {rule['clause']})",
                        })
                        break
                elif rule.get('value_type') == 'min' and lo:
                    if ratio < lo * (1 - RATIO_TOL):
                        problems.append({
                            '问题': f"{b.get('编号', '梁')} 梁高跨比 1/{1/ratio:.1f} 低于规范下限 1/{1/lo:.1f}",
                            '位置': f"构件模型.梁.{b.get('编号', '?')}",
                            '类别': '规范审查',
                            '影响造价': True,
                            '严重程度': '中',
                            '建议': f"加大梁高或减小跨度({rule['standard_code']} {rule['clause']})",
                        })
                        break
        return problems
