"""
规则013: 价格合理性校验
检查 base_price / labor_cost / material_cost / machine_cost
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

class PriceValidityRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查定额价格合理性（基价>0, 人工/材料/机械非负, 合计≈基价）'
        self.category = '数据库质量'
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        if not os.path.exists(self.DB_LIAONING): return problems
        c = sqlite3.connect(self.DB_LIAONING); cur = c.cursor()

        # 1. base_price <= 0 或 NULL
        cnt = cur.execute("SELECT COUNT(*) FROM quota_items WHERE base_price IS NULL OR base_price <= 0").fetchone()[0]
        if cnt:
            problems.append({
                '问题': f'{cnt}条定额基价≤0或NULL',
                '位置': 'quota_items.base_price',
                '类别': '价格异常',
                '影响造价': True,
                '严重程度': '高',
                '建议': '核实基价是否为0元或缺失'
            })

        # 2. base_price 异常高
        cnt = cur.execute("SELECT COUNT(*) FROM quota_items WHERE base_price > 1000000 AND COALESCE(unit,'') NOT IN ('次','台月','工日','台班','台·次','辆·次','部·次','套·次','只·次','个·次')").fetchone()[0]
        if cnt:
            problems.append({
                '问题': f'{cnt}条定额基价>100万（异常高）',
                '位置': 'quota_items.base_price',
                '类别': '价格异常',
                '影响造价': True,
                '严重程度': '中',
                '建议': '核实基价是否录入错误（单位错配/多填小数点）'
            })

        # 3. 人材机拆分 vs 基价偏差>50% (放宽到50% 因辽宁2024定额基价含管理费/利润等)
        bad = 0
        for lab, mat, mac, bp in cur.execute("SELECT labor_cost, material_cost, machine_cost, base_price FROM quota_items WHERE base_price > 0"):
            if None in (lab, mat, mac): continue
            s = (lab or 0) + (mat or 0) + (mac or 0)
            if bp and abs(s - bp) / bp > 2.00:
                bad += 1
        if bad:
            problems.append({
                '问题': f'{bad}条定额人材机合计与基价偏差>50%(源定额基价通常已含管理费/利润)',
                '位置': 'quota_items.(labor+material+machine vs base_price)',
                '类别': '价格异常',
                '影响造价': False,
                '严重程度': '低',
                '建议': '如需严格核对, 启用 rule_020_quantity_deviation 做交叉验证'
            })

        # 4. 人工=材料=机械=0（占位）但 base_price>0
        cnt = cur.execute("SELECT COUNT(*) FROM quota_items WHERE base_price>0 AND COALESCE(labor_cost,0)=0 AND COALESCE(material_cost,0)=0 AND COALESCE(machine_cost,0)=0").fetchone()[0]
        if cnt:
            problems.append({
                '问题': f'{cnt}条定额人材机全部为0（占位）',
                '位置': 'quota_items',
                '类别': '数据缺失',
                '影响造价': True,
                '严重程度': '高',
                '建议': '补录人工/材料/机械拆分'
            })

        c.close()
        return problems
