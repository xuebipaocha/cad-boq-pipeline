# -*- coding: utf-8 -*-
"""规范审查规则: 保护层 — v5.0 P3

GB 50010-2010 §8.2.1: 柱/梁 20mm, 板 15mm(室内正常环境)。
构件无配筋数据 → 跳过(无依据不误报); 报出为"可能不足", 提示按图纸说明复核。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
from pipeline.rules.spec_db import find_rules


class RebarCoverRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '钢筋保护层规范审查(GB 50010 8.2.1)'
        self.prerequisites = ['构件模型']

    def check(self, drawing_data):
        problems = []
        cm = drawing_data.get('构件模型', {}) or {}
        rules = {r['component']: r for r in find_rules('cover')}
        if not rules:
            return problems
        # 平法标准构造保护层(图纸一般取此值): 柱/梁 25, 板 20
        assumed = {'柱': 25, '梁': 25, '板': 20}
        for cls, key in (('柱', '钢筋'), ('梁', '钢筋'), ('板', '配筋')):
            for comp in cm.get(cls, []) or []:
                rebar = comp.get(key) or {}
                has_rebar = bool(rebar)
                if not has_rebar:
                    continue
                rule = rules.get(cls)
                if not rule:
                    continue
                problems.append({
                    '问题': f"{comp.get('编号', cls)} 保护层取平法标准构造 {assumed.get(cls)}mm, 请与图纸说明核对是否满足规范最小 {rule['param_value']}mm",
                    '位置': f"构件模型.{cls}.{comp.get('编号', '?')}",
                    '类别': '规范审查',
                    '影响造价': False,
                    '严重程度': '低',
                    '建议': f"按图纸说明复核保护层厚度({rule['standard_code']} {rule['clause']})",
                })
        return problems
