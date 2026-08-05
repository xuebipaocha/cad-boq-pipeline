# -*- coding: utf-8 -*-
"""数据可信度核心 — v5.6 H1

全链路"防编造"机制:

1. **数字三要素校验**: 提取的数字必须 (单位存在 + 语境匹配 + 量级合理)
   三者齐备才算"提取成功"; 缺任一 → 待提取(不猜值)
2. **数据来源四级**: 实测 / 文字标注 / 估算 / 待提取
   - 实测:     几何/标注测量(DIMENSION 值, 闭合轮廓)
   - 文字标注: 图纸文字明确给出(带单位+语境)
   - 估算:     有公式但有系数假设(脚手架×系数等)
   - 待提取:   无证据, 不猜 — 不进清单, 进待提取清单
3. **公式注册表**: 所有计算公式注册(名称/输入证据/适用/限制),
   未注册公式 → 审图拦截"公式无证据输入"
"""
import re

# ── 数据来源分级 ──
SRC_MEASURED = '实测'        # 几何/标注测量
SRC_TEXT = '文字标注'        # 图纸文字明确
SRC_ESTIMATED = '估算'       # 有公式但有系数假设
SRC_PENDING = '待提取'       # 无证据

SRC_LEVEL = {SRC_MEASURED: 0, SRC_TEXT: 1, SRC_ESTIMATED: 2, SRC_PENDING: 3}


# ── 数字三要素校验 ──
# 量级合理范围(常见量纲, 按单位)
QTY_RANGE = {
    'm²': (1, 2e7), 'm2': (1, 2e7), '㎡': (1, 2e7), '平方米': (1, 2e7),
    'm³': (0.1, 2e6), 'm3': (0.1, 2e6),
    'm': (0.5, 2e5), '米': (0.5, 2e5),
    '个': (1, 1e6), '根': (1, 1e6), '套': (1, 1e6), '台': (1, 1e6),
    '樘': (1, 1e5), '只': (1, 1e6), '处': (1, 1e5), '座': (1, 1e4),
    't': (0.01, 1e6), '吨': (0.01, 1e6), 'kg': (1, 1e8), 'kg/m²': (0.1, 500),
}

# 数字上下文关键词(判断"这是工程量/数量"而非目录/编号/日期)
QTY_CONTEXT_KW = ['面积', '长度', '高度', '厚度', '数量', '个数', '根数', '约', '总',
                  '维修', '拆除', '更换', '铺设', '直径', '宽度', '跨度', '共',
                  '总计', '合计', '工程量']


def check_number(value, unit='', context=''):
    """数字三要素校验: (单位存在 + 语境匹配 + 量级合理) → (是否可信, 原因)
    任一不满足返回 False + 原因。"""
    # 1. 单位存在
    if not unit:
        return False, '缺单位'
    # 2. 语境匹配(数字上下文)
    if context and not any(k in context for k in QTY_CONTEXT_KW):
        # 无数量语境词但数字是纯数值 → 不可信
        return False, '无数量语境'
    # 3. 量级合理
    lo, hi = QTY_RANGE.get(unit, (0, 1e12))
    if not (lo <= value <= hi):
        return False, f'量级存疑({value}{unit} 超出 {lo}~{hi})'
    return True, ''


def extract_qty(text, patterns, unit='', context=''):
    """带三要素校验的数字提取: 返回 (value, source, note)
    - 提取成功 → (value, SRC_TEXT, '')
    - 提取失败 → (None, SRC_PENDING, 原因)
    单位来源: 参数 unit > 模式捕获组2 > 数字后紧跟的单位字符(m/m2/㎡/个/根等)
    """
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        val = float(m.group(1))
        if not unit:
            if m.lastindex and m.lastindex >= 2:
                unit = m.group(2)
            else:
                # 数字后紧跟单位字符自动识别
                after = text[m.end(1):m.end(1) + 4]
                um = re.match(r'(m2|平方米|㎡|m³|m3|m|个|根|套|台|樘|只|处|座|kg|t|米)', after)
                if um:
                    unit = um.group(1)
        ok, reason = check_number(val, unit, text)
        if ok:
            return val, SRC_TEXT, ''
        return None, SRC_PENDING, f'{reason}({val}{unit})'
    return None, SRC_PENDING, '未匹配'


# ── 公式注册表 ──
# 每条: 名称 / 输入证据(必须存在的 pid 键) / 适用条件 / 限制说明
FORMULA_REGISTRY = {
    '外墙面积_立面图标注': {
        '输入': ['立面DIMENSION'], '适用': '大修外墙面积',
        '限制': '需立面图水平/垂直标注; 无 → 待提取'},
    '外墙面积_文字标注': {
        '输入': ['施工说明#面积'], '适用': '大修外墙面积',
        '限制': '文字带单位且量级合理'},
    '外立面脚手架': {
        '输入': ['外墙面积'], '适用': '大修脚手架',
        '限制': '外墙面积无证据 → 待提取'},
    '雨水管_文字数量': {
        '输入': ['施工说明#雨水管'], '适用': '雨水管更换',
        '限制': "需'约X个/Xm' 带单位"},
    '楼梯_文字面积': {
        '输入': ['施工说明#维修面积'], '适用': '楼梯面层维修',
        '限制': '需带单位'},
    '屋面_系数估算': {
        '输入': ['外墙面积'], '适用': '屋面翻新(无屋面面积标注时)',
        '限制': '×0.5 系数, 来源=估算'},
}


def check_formula(name):
    """公式是否已注册: 未注册 → (False, '公式未注册, 禁止使用')"""
    if name in FORMULA_REGISTRY:
        return True, ''
    return False, f'公式 {name} 未注册, 禁止使用'


def source_of(value, src, note=''):
    """输出统一带来源标记的算量项"""
    return {'工程量': value, '数据来源': src, '来源说明': note}
