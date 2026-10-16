# -*- coding: utf-8 -*-
"""s2 钢构件**几何提取**：图层语义 + 几何长度 + 就近规格文字 → 分类钢构件明细。

背景（实测 261012 项目 2-1 钢结构图）：
  仅靠文字规格只认出 12 个构件（1.68 t），但图内有 LINE 12954 / 拉杆 7402 / 螺栓 525 /
  檩条折线 164（最长 43.2m）/ 主构件线（最长 51m）。诊断发现**图层名即构件类别**：
    SE_STEL_COLU_CON=钢柱(COLU) · SE_STEL_PRUN=檩条/支撑(Purlin) · S_STAY ROD=拉杆
    BOLT=螺栓 · S_ANCHOR BOLTS=锚栓 · 零部件/S_METAL=零件/金属件 · 0/1/objs=主干构件

流程：① 按图层收集几何（LINE/LWPOLYLINE 长度、INSERT/CIRCLE 计数）
      ② 就近（≤3m）匹配含规格的文字（parse_steel_spec）
      ③ 复用 steel_weight 的 h_beam/angle_steel/channel_steel/round_bar/plate 算单位重
      ④ 汇总 {类别: {规格: {长度_m, 数量, 单重_kg_m, 重量_t, 规格来源}}}

诚实规则：无规格可匹配时，用类别默认规格算重并把"规格来源"标为 `默认估算`（不冒充实测）。
"""
import re
from collections import defaultdict

import steel_weight as sw

# 图层名 → (类别, 默认构件形式)
LAYER_RULES = {
    'SE_STEL_COLU_CON': ('钢柱', 'h'),
    'SE_STEL_PRUN': ('檩条支撑', 'c'),
    'S_STAY ROD': ('拉杆', 'rod'),
    'BOLT': ('螺栓', 'count'),
    'S_ANCHOR BOLTS': ('锚栓', 'count'),
    '零部件': ('零件', 'plate'),
    'S_METAL': ('金属件', 'plate'),
    'objs': ('构件对象', 'h'),
    '1': ('构件线', 'h'),
    '0': ('主构件', 'h'),
}
# 类别默认规格（无文字可匹配时的兜底，标"默认估算"）
DEFAULT_SPEC = {
    'h': ('H400x200x6x8', 52.0),      # kg/m 量级
    'c': ('C250x75x20x2.0', 6.4),
    'rod': ('Φ16', 1.58),
    'plate': ('-10x200', 15.7),
}


def _pt(e):
    """实体代表点（用于就近匹配文字）。"""
    try:
        t = e.dxftype()
        if t == 'LINE':
            return ((e.dxf.start.x + e.dxf.end.x) / 2.0, (e.dxf.start.y + e.dxf.end.y) / 2.0)
        if t in ('LWPOLYLINE', 'POLYLINE'):
            pts = [(q[0], q[1]) for q in e.get_points('xy')] if t == 'LWPOLYLINE' else []
            if pts:
                return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        if t in ('TEXT', 'MTEXT'):
            return (e.dxf.insert.x, e.dxf.insert.y)
        if t == 'INSERT':
            return (e.dxf.insert.x, e.dxf.insert.y)
        if t == 'CIRCLE':
            return (e.dxf.center.x, e.dxf.center.y)
    except Exception:
        pass
    return None


def _len_mm(e):
    """LINE / LWPOLYLINE 的长度（mm）。"""
    t = e.dxftype()
    try:
        if t == 'LINE':
            p1, p2 = e.dxf.start, e.dxf.end
            return ((p2.x - p1.x) ** 2 + (p2.y - p1.y) ** 2) ** 0.5
        if t == 'LWPOLYLINE':
            pts = [(q[0], q[1]) for q in e.get_points('xy')]
            return sum(((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5
                       for i in range(len(pts) - 1))
        if t == 'ARC':
            return abs(e.dxf.end_angle - e.dxf.start_angle) * 3.141592653589793 / 180.0 * e.dxf.radius
    except Exception:
        return 0.0
    return 0.0


def _spec_kgm(spec, form):
    """规格 → 单位重 kg/m（复用 steel_weight）。

    ⚠ 实测坑（本函数曾全部返回 0）：`calc_weight_main` 返回的是 **dict**
    （{'type','weight_kgm','surface_m2m'}），不是数字 —— 按数字判断会让所有图示规格算成 0，
    结果只能靠"默认估算"（主构件 149.6t 全是假设值）。另 `parse_steel_spec` 对
    'Φ16'/'L110*70' 之类会返回 None，解包会抛异常，需兜底。
    """
    s = str(spec).strip()
    m = re.match(r'^[ΦφΦ]\s*(\d+)', s)              # 圆钢 Φ16
    if m:
        try:
            return float(sw.round_bar(float(m.group(1))))
        except Exception:
            pass
    try:
        parsed = sw.parse_steel_spec(s)
        if parsed:
            kind, params = parsed
            r = sw.calc_weight_main(kind, params)
            if isinstance(r, dict):
                w = r.get('weight_kgm') or r.get('weight') or 0
            else:
                w = r or 0
            if float(w) > 0:
                return float(w)
    except Exception:
        pass
    try:
        hit = sw.lookup(s)
        if hit and float(hit) > 0:
            return float(hit)
    except Exception:
        pass
    return 0.0


def extract(msp, tol=3000.0, min_len_mm=500.0, exclude_detail=True):
    """主入口：返回 (按类别明细, 统计摘要)。

    v6.10.10 **图名分区过滤（关键修正）**：实测本图为"图集式多图框"（2-1 钢结构有 59 个图名），
    其中 `直拉条(ZLT)详图`/`撑杆(CG)详图`/`斜拉条(XLT)详图`/`屋盖檩条详图` 等**详图是放大比例绘制**
    （1:5~1:20），其线段长度**不等于实际构件长度** —— 早期版本按图层把详图线条也当构件量长度，
    导致钢量虚高（186.3 t）。现已按图名分区，**排除"详图/大样/节点"区**，只用
    布置图/平面图/立面图/剖面图区内的几何。
    """
    # ⓪ 图名锚点（用于分区；无图名时退化为不分区）
    titles = []
    for e in msp.query('TEXT'):
        try:
            t = str(e.dxf.text).strip()
        except Exception:
            continue
        if t and len(t) < 30 and re.search(r'(平面图|立面图|剖面图|详图|系统图|布置图|大样)', t):
            titles.append((t, e.dxf.insert.x, e.dxf.insert.y))

    def _zone(x, y):
        best, bd = None, 1e18
        for t, tx, ty in titles:
            d = (x - tx) ** 2 + (y - ty) ** 2
            if d < bd:
                bd, best = d, t
        return best

    # ① 收集含规格的文字（带位置）—— **含块内文字（递归 INSERT）**：
    #    实测主构件 2877m 中 2819m 匹配不到规格，而图纸规格标注大量位于块内/详图中，
    #    只扫 msp 的 TEXT 会漏掉 → 全部退回"默认估算"。
    tag_texts = []
    _seen_txt = set()

    def _walk_texts(container, dx=0.0, dy=0.0, depth=0):
        if depth > 3:
            return
        for e in container:
            t = e.dxftype()
            if t in ('TEXT', 'MTEXT'):
                try:
                    raw = (e.dxf.text if t == 'TEXT' else e.text) or ''
                except Exception:
                    continue
                s2 = str(raw).strip()
                if not s2:
                    continue
                p = _pt(e)
                if not p:
                    continue
                x, y = p[0] + dx, p[1] + dy
                key = (s2, round(x), round(y))
                if key in _seen_txt:
                    continue
                _seen_txt.add(key)
                try:
                    parsed = sw.parse_steel_spec(s2)
                except Exception:
                    parsed = None
                if parsed:
                    tag_texts.append((s2, x, y, parsed[0], parsed[1]))
            elif t == 'INSERT':
                try:
                    blk = str(e.dxf.name)
                    ix, iy = e.dxf.insert.x, e.dxf.insert.y
                    bd = msp.doc.blocks.get(blk)
                    if bd is not None:
                        _walk_texts(bd, dx + ix, dy + iy, depth + 1)
                except Exception:
                    continue

    _walk_texts(msp)
    # ①.5 v6.10.12 **按图名区统计规格**（规格集中标注 → 逐构件就近匹配几乎全失败，
    # 故以图名区为边界：同区构件用该区出现的最高频规格）
    _zone_specs = {}
    for _txt, _tx, _ty, _k, _pr in tag_texts:
        _zn = _zone(_tx, _ty)
        if not _zn:
            continue
        _d = _zone_specs.setdefault(_zn, {})
        _d[_txt] = _d.get(_txt, 0) + 1
    _zone_default = {zn: max(d.items(), key=lambda x: x[1])[0] for zn, d in _zone_specs.items()}

    # ② 按图层收集几何
    by_cat = defaultdict(lambda: defaultdict(lambda: {'length_mm': 0.0, 'count': 0,
                                                      'spec_hits': defaultdict(float)}))
    counts_only = defaultdict(int)
    for e in msp:
        lay = str(e.dxf.layer or '')
        rule = LAYER_RULES.get(lay)
        if not rule:
            continue
        cat, form = rule
        t = e.dxftype()
        if form == 'count':
            if t in ('INSERT', 'CIRCLE'):
                counts_only[cat] += 1
            elif t == 'LINE' and _len_mm(e) < 60:      # 螺栓小线也算一个
                counts_only[cat] += 1
            continue
        L = _len_mm(e)
        if L < min_len_mm:
            continue
        p = _pt(e)
        # v6.10.10 图名分区过滤：**"详图/大样/节点"区是放大比例绘制**（1:5~1:20），
        # 其长度不可用于算量 —— 早期未过滤，把详图线条当构件量长度，导致钢量虚高（186.3t）。
        if exclude_detail and p and titles:
            _zn = _zone(p[0], p[1])
            if _zn and re.search(r'(详图|大样|节点)', _zn):
                continue
        spec = None
        if p:
            best = None
            for txt, tx, ty, kind, params in tag_texts:
                d = ((p[0] - tx) ** 2 + (p[1] - ty) ** 2) ** 0.5
                if d <= tol and (best is None or d < best[0]):
                    best = (d, txt)
            if best:
                spec = best[1]
        if not spec and p:
            _zn2 = _zone(p[0], p[1])
            if _zn2 and _zone_default.get(_zn2):
                spec = _zone_default[_zn2]          # 本图名区最高频规格
        key = spec or ('（%s默认）' % DEFAULT_SPEC.get(form, ('未知', 0))[0])
        d = by_cat[cat][key]
        d['length_mm'] += L
        d['count'] += 1

    # ③ 算重 + 汇总
    detail, summary = {}, {'总重_t': 0.0, '构件数': 0, '长度合计_m': 0.0}
    for cat, specs in by_cat.items():
        form = None
        for ly, (c0, f0) in LAYER_RULES.items():
            if c0 == cat:
                form = f0
                break
        detail[cat] = {}
        for spec, d in sorted(specs.items(), key=lambda x: -x[1]['length_mm']):
            Lm = d['length_mm'] / 1000.0
            kgm = _spec_kgm(spec.replace('（', '').replace('默认）', ''), form) or 0.0
            src = '图示规格' if not spec.startswith('（') else '默认估算'
            if src == '默认估算':
                kgm = DEFAULT_SPEC.get(form, ('', 0))[1]
            wt = Lm * kgm / 1000.0
            detail[cat][spec] = {'长度_m': round(Lm, 2), '数量': d['count'],
                                 '单重_kg_m': round(kgm, 2), '重量_t': round(wt, 4),
                                 '规格来源': src}
            summary['总重_t'] += wt
            summary['构件数'] += d['count']
            summary['长度合计_m'] += Lm
    # v6.10.15 螺栓/锚栓**算重**（原先仅计数、重量恒 0）：
    # 从图内文字提取 `nMm`（数量+规格，实测 261012 有 '4M24' 多处）→ 规格单重 × 数量。
    _BOLT_KG = {'M12': 0.13, 'M16': 0.20, 'M20': 0.36, 'M22': 0.48,
                'M24': 0.60, 'M27': 0.85, 'M30': 1.10}
    _ANCHOR_KG = {'M20': 2.10, 'M22': 2.55, 'M24': 3.06, 'M27': 3.90, 'M30': 4.80}   # L≈750mm
    # v6.10.16 锚栓**按图内长度精算**：从 'M24×750' / 'M24*750' 提取实际长度(mm)
    _bolt_len = {}
    try:
        for _e in msp.query('TEXT'):
            _t = str(_e.dxf.text or '').strip().upper()
            for _m4 in re.finditer(r'(M\d{2,3})\s*[×X*]\s*(\d{2,4})', _t):
                _g4, _L4 = _m4.group(1), int(_m4.group(2))
                if 100 <= _L4 <= 3000:
                    _bolt_len.setdefault(_g4, []).append(_L4)
    except Exception:
        _bolt_len = {}
    _bolt_qty = {}
    try:
        for _e in msp.query('TEXT'):
            _t = str(_e.dxf.text or '').strip().upper()
            for _m3 in re.finditer(r'(\d{1,3})\s*(M\d{2,3})', _t):
                _n3, _g3 = int(_m3.group(1)), _m3.group(2)
                if 1 <= _n3 <= 200:
                    _bolt_qty[_g3] = _bolt_qty.get(_g3, 0) + _n3
    except Exception:
        _bolt_qty = {}
    for cat, n in counts_only.items():
        _tbl = _ANCHOR_KG if '锚' in cat else _BOLT_KG
        _kg, _detail_spec = 0.0, {}
        for _g, _q in _bolt_qty.items():
            _u = _tbl.get(_g)
            if _u:
                # v6.10.16 锚栓：若图内给出长度（如 M24×750）→ 按圆钢体积精算（含螺母垫圈 1.15）
                if '锚' in cat and _g in _bolt_len:
                    try:
                        _Ls = _bolt_len[_g]
                        _Lavg = sum(_Ls) / float(len(_Ls))
                        _dmm = int(_g[1:])
                        _u = (3.14159265 * (_dmm ** 2) / 4.0) * _Lavg * 7.85e-9 * 1000.0 * 1.15
                    except Exception:
                        pass
                _kg += _u * _q
                _detail_spec[_g] = _q
        if _kg > 0:
            _src = '图内规格(nMm)'
        else:                                   # 无规格文字 → 类别默认单重（不冒充图内规格）
            _kg = (3.06 if '锚' in cat else 0.36) * n
            _src = '默认单重(图内无规格文字)'
        detail.setdefault(cat, {})['（计数·算重）'] = {
            '数量': n, '重量_t': round(_kg / 1000.0, 4),
            '规格来源': _src, '规格明细': _detail_spec}
        summary['总重_t'] += _kg / 1000.0
    summary['总重_t'] = round(summary['总重_t'], 3)
    summary['长度合计_m'] = round(summary['长度合计_m'], 1)
    return detail, summary


def main(path):
    import json
    import ezdxf
    doc = ezdxf.readfile(path)
    detail, summary = extract(doc.modelspace())
    print('=== 类别汇总 ===')
    for cat, specs in detail.items():
        tot = sum(v.get('重量_t', 0) or 0 for v in specs.values())
        n = sum(v.get('数量', 0) or 0 for v in specs.values())
        print('%-10s 规格种类%2d 件数%5d 重量 %8.3f t' % (cat, len(specs), n, tot))
        for spec, v in list(specs.items())[:4]:
            print('     %-26s 长%9.2fm 数%4d  %6.2f kg/m → %7.3ft  [%s]'
                  % (spec[:26], v.get('长度_m', 0), v.get('数量', 0),
                     v.get('单重_kg_m', 0), v.get('重量_t', 0), v.get('规格来源', '')))
    print('RESULTN', json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    import sys
    main(sys.argv[1])
