"""
规则017: 量纲 vs 数量范围合理性
检查不同量纲对应的数量级是否合理
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

# 不同量纲的合理数量范围（防止小数点错位/多填）
RANGE_RULES = {
    'm³': (0.001, 1e6),
    'm²': (0.01, 1e7),
    'm': (0.1, 1e5),
    't': (0.001, 1e5),
    'kg': (0.1, 1e7),
    '个': (1, 1e7),
    '套': (1, 1e4),
    '台': (1, 1e4),
    '座': (1, 1e3),
    '根': (1, 1e6),
    '株': (1, 1e6),
    '块': (1, 1e7),
    '项': (1, 1e3),
    '樘': (1, 1e4),
}

class QuantityRangeRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查工程量数量级是否合理（防止小数点错位）'
        self.category = '工程量校验'
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        if not os.path.exists(self.DB_LIAONING): return problems
        c = sqlite3.connect(self.DB_LIAONING); cur = c.cursor()
        # 这个规则作用于"算量结果"，不是 quota_items（定额基价是单价，不是数量）
        # 因此本规则仅在有 drawing_data 时检查工程量结果
        if drawing_data and '工程量' in drawing_data:
            quantities = drawing_data['工程量']
            for item_code, qty_data in quantities.items():
                unit = qty_data.get('unit', '')
                qty = qty_data.get('quantity', 0)
                if unit in RANGE_RULES and qty > 0:
                    lo, hi = RANGE_RULES[unit]
                    if qty < lo or qty > hi:
                        problems.append({
                            '问题': f'"{item_code}"工程量{qty}{unit}超出合理范围[{lo}, {hi}]',
                            '位置': f'工程量结果.{item_code}',
                            '类别': '数量异常',
                            '影响造价': True,
                            '严重程度': '高',
                            '建议': f'复核图纸尺寸与计算式，可能小数点错位'
                        })
        c.close()
        return problems
