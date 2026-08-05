"""材料名提取工具 — 从构件/层名提取材料关键词"""
MATERIAL_LIST = ['沥青混凝土', '水泥稳定碎石', '级配碎石', '混凝土', '钢筋混凝土', '砂浆',
                 '砌体', '钢筋', '防水卷材', '乳化沥青', '石油沥青', '种植土', '钢板',
                 'H型钢', '水泥砂浆', '石灰', '砂', '碎石']


def extract_material_name(name):
    for m in MATERIAL_LIST:
        if m in (name or ''):
            return m
    return ''
