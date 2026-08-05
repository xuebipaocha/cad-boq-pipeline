"""
规则011: 数据库质量校验 - 编码格式
检查 standard_items.item_code / quota_items.quota_code 格式
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class CodeFormatRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查清单/定额编码格式（9位数字，前缀01-09表示专业）'
        self.category = '数据库质量'
        self.DB_NATIONAL = db.NATIONAL_DB
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        # 检查 national_24.db
        if os.path.exists(self.DB_NATIONAL):
            c = sqlite3.connect(self.DB_NATIONAL); cur = c.cursor()
            for code, in cur.execute("SELECT item_code FROM standard_items"):
                if not re.fullmatch(r'\d{9}', str(code)):
                    problems.append({
                        '问题': f'清单编码格式错误: "{code}" (应为9位数字)',
                        '位置': f'standard_items.item_code={code}',
                        '类别': '编码格式',
                        '影响造价': True,
                        '严重程度': '高',
                        '建议': f'修复为9位数字编码'
                    })
            c.close()
        # 检查 liaoning_24.db
        if os.path.exists(self.DB_LIAONING):
            c = sqlite3.connect(self.DB_LIAONING); cur = c.cursor()
            for code, in cur.execute("SELECT quota_code FROM quota_items"):
                if not re.fullmatch(r'\d{1,3}(?:-\d+)?', str(code)):
                    problems.append({
                        '问题': f'定额编码格式异常: "{code}"',
                        '位置': f'quota_items.quota_code={code}',
                        '类别': '编码格式',
                        '影响造价': False,
                        '严重程度': '中',
                        '建议': '确认编码是否为 X-Y 形式'
                    })
            c.close()
        return problems
