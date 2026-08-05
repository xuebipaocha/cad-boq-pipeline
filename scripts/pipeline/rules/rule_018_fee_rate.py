"""
规则018: 费率完整性
检查 fee_rates 表必备费率（管理费/利润/规费/增值税）是否齐全
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class FeeRateRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查费率完整性（管理费/利润/规费/增值税/冬雨季施工/安全文明）'
        self.category = '数据库质量'
        self.DB_LIAONING = db.LIAONING_DB

    REQUIRED = ['管理费', '利润', '规费', '增值税', '安全', '雨季', '冬季']

    def check(self, drawing_data=None):
        problems = []
        if not os.path.exists(self.DB_LIAONING): return problems
        c = sqlite3.connect(self.DB_LIAONING); cur = c.cursor()
        cnt = cur.execute("SELECT COUNT(*) FROM fee_rates").fetchone()[0]
        if cnt == 0:
            problems.append({
                '问题': 'fee_rates 表为空（无法做组价取费）',
                '位置': 'liaoning_24.db.fee_rates',
                '类别': '数据缺失',
                '影响造价': True,
                '严重程度': '高',
                '建议': '从辽宁省2024计价依据PDF补录费率'
            })
        else:
            for kw in self.REQUIRED:
                hit = cur.execute("SELECT COUNT(*) FROM fee_rates WHERE rate_type LIKE ? OR category LIKE ?", (f'%{kw}%', f'%{kw}%')).fetchone()[0]
                if hit == 0:
                    problems.append({
                        '问题': f'fee_rates 缺"{kw}"相关费率',
                        '位置': 'liaoning_24.db.fee_rates',
                        '类别': '费率缺失',
                        '影响造价': True,
                        '严重程度': '高',
                        '建议': f'补录"{kw}"费率'
                    })
        c.close()
        return problems
