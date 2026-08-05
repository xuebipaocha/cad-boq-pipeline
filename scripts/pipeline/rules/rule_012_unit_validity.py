"""
规则012: 量纲合理性校验
检查 unit 是否在标准量纲集合内
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
import sqlite3

# 合法量纲集合
VALID_UNITS = {
    # 长度/面积/体积
    'm', 'cm', 'mm', 'km', 'm²', 'm³', 'cm³', 'mm²',
    # 重量
    't', 'kg', 'g',
    # 时间
    'h', 'min', 's', '天', '月', '年', '次',
    # 数量
    '个', '套', '台', '部', '组', '根', '株', '座', '项',
    '块', '樘', '件', '只', '片', '节', '盏', '袋', '包', '箱', '副',
    '把', '盘', '圈', '道', '户', '户·日', '工日',
    # 复合单位
    't·km', 't.km', 'km·h', 'm/s',
    # 复合量
    '口', '辆', '元', '系统', '处', '部·次', '台·次', '辆·次',
    '个·次', '套·次', '只·次',
    # 其他工程专用
    'M', 'kW', 'kV', 'kVA', 'kW·h', 'kV·A',
    'MPa', 'mmHg',
    'kN', 'daN',
    # 占位/范围类
    '宗', '证',
}

class UnitValidityRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查 unit 是否在标准量纲集合内'
        self.category = '数据库质量'
        self.DB_NATIONAL = db.NATIONAL_DB
        self.DB_LIAONING = db.LIAONING_DB

    def check(self, drawing_data=None):
        problems = []
        invalid = []
        for DB in [self.DB_NATIONAL, self.DB_LIAONING]:
            if not os.path.exists(DB): continue
            c = sqlite3.connect(DB); cur = c.cursor()
            table = 'standard_items' if 'national' in DB else 'quota_items'
            for unit, in cur.execute(f"SELECT DISTINCT unit FROM {table} WHERE unit IS NOT NULL AND unit != ''"):
                if unit not in VALID_UNITS:
                    invalid.append((table, unit))
            c.close()
        # 按单位聚合
        from collections import Counter
        cnt = Counter(invalid)
        for (table, unit), c in cnt.most_common(20):
            problems.append({
                '问题': f'量纲"{unit}"不在标准量纲集合内(出现{c}次)',
                '位置': f'{table}.unit',
                '类别': '量纲异常',
                '影响造价': True,
                '严重程度': '中',
                '建议': f'核实"{unit}"是否为标准量纲，否则修正'
            })
        return problems
