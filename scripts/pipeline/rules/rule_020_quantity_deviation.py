"""
规则020: 工程量计算偏差（rule_009 强化版）
对 quota_items 中"已知数量+单位"的算量结果做交叉验证
- 同类构件定额基价应符合分布规律
- 同类定额单位应一致
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class QuantityDeviationRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查同类定额基价分布与单位一致性'
        self.category = '工程量校验'
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        if not os.path.exists(self.DB_LIAONING): return problems
        c = sqlite3.connect(self.DB_LIAONING); cur = c.cursor()

        # 1. 同 category 下同类定额单位是否一致
        from collections import defaultdict
        cat_units = defaultdict(set)
        for cat, unit, n in cur.execute("SELECT category, unit, COUNT(*) FROM quota_items WHERE unit IS NOT NULL AND unit != '' GROUP BY category, unit").fetchall():
            cat_units[cat].add(unit)
        inconsistent = []
        for cat, units in cat_units.items():
            if len(units) > 3:  # 同专业超过3种单位是异常
                inconsistent.append((cat, len(units)))
        if inconsistent:
            problems.append({
                '问题': f'{len(inconsistent)}个专业定额单位种类>3 (建议统一)',
                '位置': 'quota_items.unit',
                '类别': '单位不统一',
                '影响造价': False,
                '严重程度': '低',
                '建议': '检查是否有同专业混用 m/m²/m³'
            })

        # 2. 同 category 下同类定额基价是否合理（3σ 原则）
        # 取前 5 个 category 做抽查
        for cat, in cur.execute("SELECT category FROM quota_items GROUP BY category LIMIT 5").fetchall():
            prices = [p for p, in cur.execute("SELECT base_price FROM quota_items WHERE category=? AND base_price > 0", (cat,))]
            if len(prices) < 30: continue
            prices.sort()
            median = prices[len(prices)//2]
            p99 = prices[int(len(prices)*0.99)]
            p01 = prices[int(len(prices)*0.01)]
            # p99/p01 比例应 < 1000（同类定额价差不应过大）
            if p01 > 0 and p99 / p01 > 1000:
                problems.append({
                    '问题': f'"{cat}"同类定额基价极差>1000倍 (1%={p01:.0f} 元, 99%={p99:.0f} 元)',
                    '位置': f'quota_items.{cat}',
                    '类别': '价格分布异常',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '检查高价/低价定额是否单位错配或源数据错误'
                })
        c.close()
        return problems
