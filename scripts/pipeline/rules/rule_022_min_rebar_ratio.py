# -*- coding: utf-8 -*-
"""规范审查规则: 最小配筋率 — v5.0 P3

GB 50010-2010 §8.5.1: 柱 0.6%(全部纵筋), 梁受拉 0.2%, 板 0.2%。
从构件模型的钢筋数据计算 As/bh0; 钢筋非平法来源(无数据) → 跳过, 不误报。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
from pipeline.rules.spec_db import find_rules


def _bars_area(bars):
    """[(根数, 直径_mm)] → 钢筋面积 mm²。根数/直径可能为 str(OCR/JSON 类型漂移), 统一转 float"""
    import math
    total = 0.0
    for item in bars or []:
        try:
            n = float(item.get('根数', 0) or 0)
            d = float(item.get('直径_mm', 0) or 0)
        except (TypeError, ValueError):
            continue
        total += n * math.pi * d * d / 4
    return total


class MinRebarRatioRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '最小配筋率规范审查(GB 50010 8.5.1)'
        self.prerequisites = ['构件模型']

    def check(self, drawing_data):
        problems = []
        cm = drawing_data.get('构件模型', {}) or {}
        rules = {r['component']: r for r in find_rules('min_rebar_ratio')}
        if not rules:
            return problems

        # 柱: 全部纵筋配筋率 = As / (b×h)
        for c in cm.get('柱', []) or []:
            rebar = c.get('钢筋') or {}
            if not rebar.get('纵筋'):
                continue
            b, h = c.get('截面宽_mm'), c.get('截面高_mm')
            if not b or not h:
                continue
            rule = rules.get('柱')
            if not rule:
                continue
            as_ = _bars_area(rebar['纵筋'])
            ratio = as_ / (b * h) * 100
            if ratio < rule['param_value']:
                problems.append({
                    '问题': f"{c.get('编号', '柱')} 全部纵筋配筋率 {ratio:.2f}% 低于规范最小 {rule['param_value']}%",
                    '位置': f"构件模型.柱.{c.get('编号', '?')}",
                    '类别': '规范审查',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': f"增大纵筋直径或根数({rule['standard_code']} {rule['clause']})",
                })

        # 梁: 受拉钢筋(下部筋)配筋率 = As / (b×h0), h0 近似 h-板厚-保护层
        slabs = cm.get('板', []) or []
        slab_thick = slabs[0].get('厚度_mm', 120) if slabs else 120
        for b_ in cm.get('梁', []) or []:
            rebar = b_.get('钢筋') or {}
            bars = (rebar.get('下部筋') or []) or (rebar.get('上部筋') or [])
            if not bars:
                continue
            bw, bh = b_.get('截面宽_mm'), b_.get('截面高_mm')
            if not bw or not bh:
                continue
            rule = rules.get('梁')
            if not rule:
                continue
            h0 = bh - slab_thick - 25
            if h0 <= 0:
                continue
            as_ = _bars_area(bars)
            ratio = as_ / (bw * h0) * 100
            if ratio < rule['param_value']:
                problems.append({
                    '问题': f"{b_.get('编号', '梁')} 受拉钢筋配筋率 {ratio:.2f}% 低于规范最小 {rule['param_value']}%",
                    '位置': f"构件模型.梁.{b_.get('编号', '?')}",
                    '类别': '规范审查',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': f"增大受拉钢筋({rule['standard_code']} {rule['clause']})",
                })

        # 板: 板底筋配筋率 = As / (b×h) 每米宽
        import math
        for s in cm.get('板', []) or []:
            rebar = s.get('配筋') or {}
            bottom = rebar.get('底筋') or []
            if not bottom:
                continue
            thick = s.get('厚度_mm')
            if not thick:
                continue
            rule = rules.get('板')
            if not rule:
                continue
            # 每米宽钢筋面积: 1000/间距 × πd²/4
            as_per_m = sum((1000 / sp) * math.pi * d * d / 4 for d, sp in
                           [(x.get('直径_mm'), x.get('间距_mm')) for x in bottom] if sp)
            ratio = as_per_m / (1000 * thick) * 100
            if ratio < rule['param_value']:
                problems.append({
                    '问题': f"{s.get('编号', '板')} 底筋配筋率 {ratio:.2f}% 低于规范最小 {rule['param_value']}%",
                    '位置': f"构件模型.板.{s.get('编号', '?')}",
                    '类别': '规范审查',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': f"增大板底筋({rule['standard_code']} {rule['clause']})",
                })
        return problems
