"""
规则015: 跨表一致性
检查 standard_items.item_code 与 quota_items.list_code 关联
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class CrossTableRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查清单-定额跨表一致性'
        self.category = '数据库质量'
        self.DB_NATIONAL = db.NATIONAL_DB
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        if not (os.path.exists(self.DB_NATIONAL) and os.path.exists(self.DB_LIAONING)):
            return problems
        c_n = sqlite3.connect(self.DB_NATIONAL); cur_n = c_n.cursor()
        c_l = sqlite3.connect(self.DB_LIAONING); cur_l = c_l.cursor()

        # 1. standard_items.item_code 在 quota_items 中是否有对应 list_code
        std_codes = set(r[0] for r in cur_n.execute("SELECT DISTINCT item_code FROM standard_items"))
        quota_list_codes = set(r[0] for r in cur_l.execute("SELECT DISTINCT list_code FROM quota_items WHERE list_code IS NOT NULL AND list_code != ''"))
        linked = std_codes & quota_list_codes
        if len(std_codes) > 0:
            link_pct = len(linked) / len(std_codes) * 100
            problems.append({
                '问题': f'清单-定额关联率: {len(linked)}/{len(std_codes)} = {link_pct:.1f}%',
                '位置': 'standard_items.item_code ↔ quota_items.list_code',
                '类别': '跨表关联',
                '影响造价': True,
                '严重程度': '中' if link_pct < 50 else '低',
                '建议': f'补全{len(std_codes)-len(linked)}条清单与定额的对应关系（list_quota_mapping表）'
            })

        # 2. 重复 item_code 检查
        dups_n = cur_n.execute("SELECT item_code, COUNT(*) c FROM standard_items GROUP BY item_code HAVING c > 1").fetchall()
        if dups_n:
            problems.append({
                '问题': f'standard_items 中有{len(dups_n)}个重复item_code',
                '位置': 'standard_items.item_code',
                '类别': '主键重复',
                '影响造价': False,
                '严重程度': '中',
                '建议': '清理重复编码或检查为何重复'
            })

        # 3. 重复 quota_code 检查
        dups_l = cur_l.execute("SELECT quota_code, COUNT(*) c FROM quota_items GROUP BY quota_code HAVING c > 1").fetchall()
        if dups_l:
            problems.append({
                '问题': f'quota_items 中有{len(dups_l)}个重复quota_code',
                '位置': 'quota_items.quota_code',
                '类别': '主键重复',
                '影响造价': False,
                '严重程度': '中',
                '建议': '检查源定额编号是否本身有重复（同名同号）'
            })

        c_n.close(); c_l.close()
        return problems
