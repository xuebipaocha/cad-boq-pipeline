# -*- coding: utf-8 -*-
"""平法标注完整解析器 — v4.0 第二批

将 rebar_calc 的简单正则升级为结构化平法解析:
1. 梁集中标注: KL1(3) 300×600 Φ8@100/200(2) 2Φ25;4Φ22 G4Φ12
   - 编号/跨数/截面/箍筋(加密@非加密 肢数)/上部筋/下部筋/构造筋
2. 柱标注: KZ1 500×500 12Φ22 Φ8@100
   - 编号/截面/纵筋/箍筋(间距/加密区)
3. 板标注: 板厚120 底筋Φ10@200双层双向
4. 输出结构化钢筋计算参数, 含锚固/搭接/加密区规则(22G101)

注: 22G101 锚固长度需要混凝土等级/钢筋等级, 从图纸说明提取或默认
"""
import re

# 钢筋理论重量表(kg/m)
STEEL_WEIGHT = {
    6: 0.222, 6.5: 0.260, 8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208,
    16: 1.578, 18: 1.998, 20: 2.466, 22: 2.984, 25: 3.850, 28: 4.830,
    32: 6.313, 36: 7.990, 40: 9.865,
}

# 16G101-1 受拉钢筋抗震锚固长度 laE (d 倍数) — v6.9.8 按混凝土等级×钢筋等级查表
# (16G101-1 §5.2 表, 抗震等级一级; C30 时 HRB400 laE=37d 与原实现一致)
ANCHOR_LEN = {
    'HRB400': {'C25': 40, 'C30': 37, 'C35': 34, 'C40': 31},
    'HRB400E': {'C25': 40, 'C30': 37, 'C35': 34, 'C40': 31},
    'HRB335': {'C25': 33, 'C30': 30, 'C35': 27, 'C40': 25},
    'HPB300': {'C25': 29, 'C30': 26, 'C35': 24, 'C40': 22},
}
_ANCHOR_DEFAULT = 37


def _anchor(grade, concrete):
    """按等级查锚固倍数; 未知等级 → 默认 37d。"""
    tbl = ANCHOR_LEN.get(grade or 'HRB400', {})
    return tbl.get(concrete or 'C30', _ANCHOR_DEFAULT)


def weight_per_m(d):
    if d in STEEL_WEIGHT:
        return STEEL_WEIGHT[d]
    return round(3.14159 * d * d / 4 * 0.00785, 3)


class BeamRebar:
    """梁平法标注"""

    def __init__(self, text):
        self.text = text
        self.name = ''
        self.spans = 1
        self.b = 300
        self.h = 600
        self.stirrup_d = 8
        self.stirrup_enc = 100
        self.stirrup_non = 200
        self.stirrup_legs = 2
        self.top_bars = []   # [(根数, 直径)]
        self.bottom_bars = []
        self.construct_bars = []  # 构造筋 G / 抗扭筋 N

    def parse(self):
        t = self.text
        # 梁编号: KL1(3) / WKL2(2A) / L3
        m = re.search(r'([A-Z]{1,3}\d+)\s*\((\d+[A-Z]?)\)', t)
        if m:
            self.name = m.group(1)
            self.spans = int(re.match(r'\d+', m.group(2)).group())
        # 截面: 300×600
        m = re.search(r'(\d{2,4})\s*[×xX*]\s*(\d{2,4})', t)
        if m:
            self.b = int(m.group(1))
            self.h = int(m.group(2))
        # 箍筋: Φ8@100/200(2) 或 Φ8@100/200
        m = re.search(r'[ΦфФφ]\s*(\d+)\s*@\s*(\d+)(?:/(\d+))?\s*(?:\((\d)\))?', t)
        if m:
            self.stirrup_d = int(m.group(1))
            self.stirrup_enc = int(m.group(2))
            self.stirrup_non = int(m.group(3)) if m.group(3) else int(m.group(2))
            self.stirrup_legs = int(m.group(4)) if m.group(4) else 2
        # 主筋: '2Φ25;4Φ22' (上;下) 或 '2Φ25+2Φ22'
        m = re.search(r'(\d+)\s*[ΦфФφ]\s*(\d+)\s*[;；]\s*(\d+)\s*[ΦфФφ]\s*(\d+)', t)
        if m:
            self.top_bars = [(int(m.group(1)), int(m.group(2)))]
            self.bottom_bars = [(int(m.group(3)), int(m.group(4)))]
        else:
            # 单侧: '2Φ25'
            bars = re.findall(r'(\d+)\s*[ΦфФφ]\s*(\d+)', t)
            for n, d in bars:
                if n == '2' and self.top_bars == []:
                    pass
        # 通配: 所有 主筋 对
        if not self.top_bars and not self.bottom_bars:
            bars = re.findall(r'(\d+)\s*[ΦфФφ]\s*(\d+)', t)
            if bars:
                # 第一个通常是上部筋
                self.top_bars = [(int(bars[0][0]), int(bars[0][1]))]
                if len(bars) > 1:
                    self.bottom_bars = [(int(bars[1][0]), int(bars[1][1]))]
        # 构造筋: G4Φ12 / N4Φ12
        m = re.search(r'[GN]\s*(\d+)\s*[ΦфФφ]\s*(\d+)', t)
        if m:
            self.construct_bars = [(int(m.group(1)), int(m.group(2)))]
        return self

    def steel_kg(self, beam_len_m, concrete='C30', grade='HRB400'):
        """计算梁钢筋总重(kg)"""
        total = 0.0
        lae = _anchor(grade, concrete)
        # 主筋: 每根 = 梁长 + 两端锚固
        for n, d in self.top_bars + self.bottom_bars:
            per = weight_per_m(d)
            total += n * per * (beam_len_m + 2 * lae * d / 1000)
        # 构造筋: 梁长 + 搭接(简化为 0.15 系数)
        for n, d in self.construct_bars:
            per = weight_per_m(d)
            total += n * per * beam_len_m * 1.15
        # 箍筋: 展开长 = 2×(b-2c + h-2c) + 弯钩, 加密区 = 1.5h 两端
        c = 25  # 保护层
        perim = 2 * ((self.b - 2 * c) + (self.h - 2 * c)) / 1000 + 0.12  # 弯钩
        enc_len = min(1.5 * self.h / 1000 * 2, beam_len_m)
        non_len = max(beam_len_m - enc_len, 0)
        enc_count = int(enc_len / (self.stirrup_enc / 1000)) + 1
        non_count = int(non_len / (self.stirrup_non / 1000)) + 1
        total += (enc_count + non_count) * perim * weight_per_m(self.stirrup_d)
        return total

    def to_dict(self):
        """v5.0: JSON 可序列化(显式白名单字段)"""
        return {
            '编号': self.name,
            '跨数': self.spans,
            '截面宽_mm': self.b,
            '截面高_mm': self.h,
            '箍筋': {'直径_mm': self.stirrup_d, '加密间距_mm': self.stirrup_enc,
                    '非加密间距_mm': self.stirrup_non, '肢数': self.stirrup_legs},
            '上部筋': [{'根数': n, '直径_mm': d} for n, d in self.top_bars],
            '下部筋': [{'根数': n, '直径_mm': d} for n, d in self.bottom_bars],
            '构造筋': [{'根数': n, '直径_mm': d} for n, d in self.construct_bars],
        }


class ColumnRebar:
    """柱平法标注"""

    def __init__(self, text):
        self.text = text
        self.name = ''
        self.b = 500
        self.h = 500
        self.main_bars = []   # [(根数, 直径)]
        self.stirrup_d = 8
        self.stirrup_sp = 100

    def parse(self):
        t = self.text
        m = re.search(r'([A-Z]{1,2}\d+)', t)
        if m:
            self.name = m.group(1)
        # 截面: 500×500
        m = re.search(r'(\d{2,4})\s*[×xX*]\s*(\d{2,4})', t)
        if m:
            self.b = int(m.group(1))
            self.h = int(m.group(2))
        # 纵筋: 12Φ22
        m = re.search(r'(\d{1,2})\s*[ΦфФφ]\s*(\d{2})', t)
        if m and int(m.group(1)) >= 4:
            self.main_bars = [(int(m.group(1)), int(m.group(2)))]
        # 箍筋: Φ8@100
        m = re.search(r'[ΦфФφ]\s*(\d+)\s*@\s*(\d+)', t)
        if m:
            self.stirrup_d = int(m.group(1))
            self.stirrup_sp = int(m.group(2))
        return self

    def steel_kg(self, col_h_m, concrete='C30', grade='HRB400'):
        """柱钢筋总重(kg)"""
        total = 0.0
        lae = _anchor(grade, concrete)
        for n, d in self.main_bars:
            per = weight_per_m(d)
            # 纵筋: 柱高 + 上下锚固/搭接
            total += n * per * (col_h_m + 2 * lae * d / 1000)
        # 箍筋: 展开长 + 加密区(柱端 1/3 净高 或 500mm 取大)
        c = 25
        perim = 2 * ((self.b - 2 * c) + (self.h - 2 * c)) / 1000 + 0.12
        count = int(col_h_m / (self.stirrup_sp / 1000)) + 1
        total += count * perim * weight_per_m(self.stirrup_d)
        return total

    def to_dict(self):
        """v5.0: JSON 可序列化(显式白名单字段)"""
        return {
            '编号': self.name,
            '截面宽_mm': self.b,
            '截面高_mm': self.h,
            '纵筋': [{'根数': n, '直径_mm': d} for n, d in self.main_bars],
            '箍筋': {'直径_mm': self.stirrup_d, '间距_mm': self.stirrup_sp},
        }


class SlabRebar:
    """板钢筋"""

    def __init__(self, text):
        self.text = text
        self.thick = 120
        self.bottom = []   # [(直径, 间距)]
        self.top = []
        self.double_layer = False

    def parse(self):
        t = self.text
        m = re.search(r'板厚\s*(\d+)', t)
        if m:
            self.thick = int(m.group(1))
        # 双层双向
        self.double_layer = '双层' in t and '双向' in t
        for m in re.finditer(r'[ΦфФφ]\s*(\d+)\s*@\s*(\d+)', t):
            d, sp = int(m.group(1)), int(m.group(2))
            if '面筋' in t or '上部' in t:
                self.top.append((d, sp))
            else:
                self.bottom.append((d, sp))
        return self

    def steel_kg_per_m2(self):
        """每 m² 钢筋含量(kg/m²)"""
        total = 0.0
        for d, sp in self.bottom:
            per = weight_per_m(d)
            content = (1 / (sp / 1000)) * per * 2  # 双向
            if self.double_layer:
                content *= 2
            total += content
        for d, sp in self.top:
            per = weight_per_m(d)
            content = (1 / (sp / 1000)) * per * 2
            total += content
        return total

    def to_dict(self):
        """v5.0: JSON 可序列化(显式白名单字段)"""
        return {
            '厚度_mm': self.thick,
            '底筋': [{'直径_mm': d, '间距_mm': sp} for d, sp in self.bottom],
            '面筋': [{'直径_mm': d, '间距_mm': sp} for d, sp in self.top],
            '双层双向': self.double_layer,
        }


def parse_rebar_notes(texts):
    """从图纸文字中解析全部平法标注
    返回 {'beams': [BeamRebar], 'columns': [ColumnRebar], 'slabs': [SlabRebar]}
    """
    beams, columns, slabs = [], [], []
    for t in texts:
        if re.search(r'KL\d|WKL\d|LL\d|L\d+\s', t) and 'Φ' in t and '×' in t:
            try:
                beams.append(BeamRebar(t).parse())
            except Exception:
                pass
        elif re.search(r'KZ\d|GZ\d|Z\d', t) and 'Φ' in t and '×' in t:
            try:
                columns.append(ColumnRebar(t).parse())
            except Exception:
                pass
        elif '板' in t and ('Φ' in t or 'φ' in t) and ('@' in t or '＠' in t):
            try:
                slabs.append(SlabRebar(t).parse())
            except Exception:
                pass
    return {'beams': beams, 'columns': columns, 'slabs': slabs}


def calc_total_steel(parsed, bfa, col_h=3.0, beam_len=6.0, concrete='C30'):
    """综合计算钢筋量(t) — v6.9.8 混凝土等级透传(16G101 锚固查表)。"""
    total_kg = 0.0
    details = []
    for b in parsed['beams']:
        kg = b.steel_kg(beam_len, concrete=concrete)
        total_kg += kg
        details.append(f'{b.name}: {kg:.0f}kg')
    for c in parsed['columns']:
        kg = c.steel_kg(col_h, concrete=concrete)
        total_kg += kg
        details.append(f'{c.name}: {kg:.0f}kg')
    for s in parsed['slabs']:
        content = s.steel_kg_per_m2()
        kg = content * bfa
        total_kg += kg
        details.append(f'板筋: {content:.1f}kg/m²×{bfa:.0f}m²={kg:.0f}kg')
    return round(total_kg / 1000, 2), ' | '.join(details)


def rebars_to_dict(parsed):
    """v5.0: 平法解析结果 → 纯 dict(JSON 可序列化), 供构件模型落盘"""
    return {
        'beams': [b.to_dict() for b in parsed.get('beams', [])],
        'columns': [c.to_dict() for c in parsed.get('columns', [])],
        'slabs': [s.to_dict() for s in parsed.get('slabs', [])],
    }
