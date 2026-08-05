"""
规则014: 必填字段完整性
检查关键字段是否为空
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class FieldCompletenessRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查必填字段是否完整（编码/名称/单位/基价/类别）'
        self.category = '数据库质量'
        self.DB_NATIONAL = db.NATIONAL_DB
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        checks = [
            (self.DB_NATIONAL, 'standard_items', 'item_code', '清单编码缺失'),
            (self.DB_NATIONAL, 'standard_items', 'item_name', '清单名称缺失'),
            (self.DB_NATIONAL, 'standard_items', 'unit', '清单单位缺失（影响算量）'),
            (self.DB_NATIONAL, 'standard_items', 'category', '清单专业类别缺失'),
            (self.DB_LIAONING, 'quota_items', 'quota_code', '定额编号缺失'),
            (self.DB_LIAONING, 'quota_items', 'item_name', '定额名称缺失'),
            (self.DB_LIAONING, 'quota_items', 'category', '定额专业类别缺失'),
        ]
        for DB, table, col, desc in checks:
            if not os.path.exists(DB): continue
            c = sqlite3.connect(DB); cur = c.cursor()
            cnt = cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL OR {col} = ''").fetchone()[0]
            c.close()
            if cnt:
                problems.append({
                    '问题': f'{desc}: {cnt}条',
                    '位置': f'{table}.{col}',
                    '类别': '字段缺失',
                    '影响造价': True,
                    '严重程度': '高' if col in ('item_code','quota_code','item_name','unit') else '中',
                    '建议': f'补全{col}字段'
                })
        return problems
