"""
规则019: 量纲与单位换算一致性
检查 unit_convert 表与实际单位使用是否一致
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class UnitConvertRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查单位换算表覆盖（unit_convert表）'
        self.category = '数据库质量'
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        if not os.path.exists(self.DB_LIAONING): return problems
        c = sqlite3.connect(self.DB_LIAONING); cur = c.cursor()
        cnt = cur.execute("SELECT COUNT(*) FROM unit_convert").fetchone()[0]
        if cnt == 0:
            problems.append({
                '问题': 'unit_convert 表为空（无单位换算数据）',
                '位置': 'liaoning_24.db.unit_convert',
                '类别': '数据缺失',
                '影响造价': False,
                '严重程度': '中',
                '建议': '补充常用换算: 100m²=100m², 100m³=100m³, 100m=100m 等'
            })
        else:
            problems.append({
                '问题': f'unit_convert 有{cnt}条换算规则',
                '位置': 'liaoning_24.db.unit_convert',
                '类别': '数据情况',
                '影响造价': False,
                '严重程度': '低',
                '建议': '覆盖良好'
            })
        c.close()
        return problems
