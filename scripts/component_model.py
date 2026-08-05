# -*- coding: utf-8 -*-
"""构件级建模 — v5.0 P1 第一批

把识图的"特征列表"(计数/样本/标注)升级为"构件对象"(每根柱/梁带编号、截面、
位置、钢筋、置信度、证据链)。七路证据融合:

  1. 平法标注(施工说明)  — 编号/跨数/截面/钢筋(最高权威)
  2. 标注关联+构件尺寸推导(dimension_matcher) — 截面/长度(次权威)
  3. 几何样本(cad_extractor 构件尺寸样本) — 截面统计值
  4. 几何(按图层聚类 msp) — 形心/面积/长度/数量
  5. 标高参数 — 层高(柱高)
  6. 面积区域 — 面积/周长(板/墙/房间)
  7. 构造层 — 板厚/墙厚(做法表优先)

设计约束:
- 置信度只进审图与报告, 严禁进算量公式
- 所有解析 try/except 兜底, 单条证据失败不影响其他构件
- 键名沿用中文风格, 数值带单位后缀
"""
import re

DEFAULT_COL_SECTION = (400, 400)
DEFAULT_BEAM_SECTION = (250, 500)
DEFAULT_BEAM_LEN = 4.5
DEFAULT_FLOOR_H = 3.0
DEFAULT_SLAB_THICK = 120
DEFAULT_WALL_THICK = 200

# 图层名 → 构件类型
COLUMN_LAYER_KW = ('柱', 'KZ', 'COL', 'GZ')
BEAM_LAYER_KW = ('梁', 'KL', 'BEAM', 'WKL', 'LL')
SLAB_LAYER_KW = ('板', 'LB', 'SLAB', 'B-')
WALL_LAYER_KW = ('墙', 'WALL', '墙体')


def _layer_is(layer, kws):
    up = (layer or '').upper()
    return any(k.upper() in up for k in kws)


def _first_float(text, pats, lo, hi, default):
    for pat in pats:
        m = re.search(pat, text)
        if m:
            try:
                v = float(m.group(1))
                if lo <= v <= hi:
                    return v
            except (ValueError, IndexError):
                pass
    return default


def _parse_flat_labels(texts):
    """平法标注(施工说明)→ dict 版钢筋结构。
    返回 (beams_dicts, columns_dicts, slabs_dicts) 三元组
    """
    from rebar_parse2 import parse_rebar_notes, rebars_to_dict
    try:
        parsed = parse_rebar_notes(texts)
        d = rebars_to_dict(parsed)
        return d['beams'], d['columns'], d['slabs']
    except Exception:
        return [], [], []


def _collect_geometry(msp):
    """按图层聚类几何: 每图层 bbox 形心/宽度/高度/闭合面积/线长。
    msp 可为 None(单测/无 msp 时降级为纯文本证据)。
    """
    out = {}  # layer -> {'centroid': (x,y), 'w': mm, 'h': mm, 'area': m2, 'len': m, 'closed': n}
    if msp is None:
        return out
    try:
        for e in msp:
            layer = e.dxf.layer or ''
            g = out.setdefault(layer, {'centroid': (0.0, 0.0), 'w': 0, 'h': 0,
                                       'area': 0.0, 'len': 0.0, 'closed': 0})
            try:
                etype = e.dxftype()
                if etype == 'LWPOLYLINE':
                    pts = list(e.get_points('xy'))
                    if len(pts) >= 2:
                        xs = [p[0] for p in pts]
                        ys = [p[1] for p in pts]
                        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                        g['centroid'] = (cx, cy)
                        g['w'] = max(g['w'], max(xs) - min(xs))
                        g['h'] = max(g['h'], max(ys) - min(ys))
                        # 开口多段线: 各段长度累加(梁/墙线)
                        plen = 0.0
                        for i in range(len(pts) - 1):
                            x1, y1 = pts[i]
                            x2, y2 = pts[i + 1]
                            plen += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                        if plen > 0:
                            g['len'] = max(g['len'], plen)
                        if e.closed:
                            g['closed'] += 1
                            # 鞋带公式求面积(图纸单位, 调用方转 m²)
                            a = 0.0
                            for i in range(len(pts)):
                                x1, y1 = pts[i]
                                x2, y2 = pts[(i + 1) % len(pts)]
                                a += x1 * y2 - x2 * y1
                            g['area'] = max(g['area'], abs(a) / 2.0)
                elif etype == 'LINE':
                    x1, y1 = e.dxf.start.x, e.dxf.start.y
                    x2, y2 = e.dxf.end.x, e.dxf.end.y
                    g['len'] = max(g['len'], ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _unit_scale_m(pid):
    """图纸单位 → 米换算系数(与 analyze_cad 口径一致: insunits 4=mm, 6=m, 1=inch)"""
    meta = pid.get('图纸元数据', {})
    unit = str(meta.get('单位', 'mm'))
    if unit == 'mm' or '毫米' in unit:
        return 1000.0
    if unit == 'm':
        return 1.0
    ins = int(meta.get('insunits', 4) or 4)
    return {1: 39.3701, 4: 1000.0, 6: 1.0}.get(ins, 1000.0)


def _confidence(section_src, len_src, has_rebar, pos_src):
    """乘法合成置信度(0~1)。只进审图/报告, 严禁进算量公式。"""
    f = {'平法标注': 1.0, '标注推导': 0.7, '几何样本': 0.4, '几何': 1.0, '默认': 0.2}.get
    cf = f(section_src, 0.2)
    cf *= f(len_src, 0.2)
    if has_rebar:
        cf *= 1.0
    else:
        cf *= 0.5
    cf *= {'几何形心': 1.0, '标注中点': 0.7, '图块坐标': 0.4, '无': 0.4}.get(pos_src, 0.4)
    return round(min(max(cf, 0.05), 1.0), 2)


def _merge_same(specimens, tol_pct=0.02):
    """同编号+同截面(±1%)+同长(±2%)归并, 数量累加。specimens: [dict]"""
    merged = []
    for s in specimens:
        hit = None
        for m in merged:
            if m['编号'] != s['编号']:
                continue
            w1, w2 = m.get('截面宽_mm'), s.get('截面宽_mm')
            h1, h2 = m.get('截面高_mm'), s.get('截面高_mm')
            same_sec = (w1 and w2 and abs(w1 - w2) / max(w1, w2) <= 0.01
                        and h1 and h2 and abs(h1 - h2) / max(h1, h2) <= 0.01)
            same_len = True
            if m.get('长度_m') and s.get('长度_m'):
                l1, l2 = m['长度_m'], s['长度_m']
                same_len = abs(l1 - l2) / max(l1, l2) <= tol_pct
            if same_sec and same_len:
                hit = m
                break
        if hit:
            hit['数量'] += s['数量']
            # 截面/长度取证据更优者
            if s.get('截面来源') and (s['截面来源'] != '默认' or hit.get('截面来源') == '默认'):
                hit['截面宽_mm'] = s['截面宽_mm']
                hit['截面高_mm'] = s['截面高_mm']
                hit['截面来源'] = s['截面来源']
            if s.get('长度_m') and s.get('长度来源') and \
                    (s['长度来源'] != '默认' or hit.get('长度来源') == '默认'):
                hit['长度_m'] = s['长度_m']
                hit['长度来源'] = s['长度来源']
            if s.get('位置') and not hit.get('位置'):
                hit['位置'] = s['位置']
            for ev in s.get('证据', []):
                if ev not in hit['证据']:
                    hit['证据'].append(ev)
            hit['置信度'] = round(max(hit['置信度'], s['置信度']), 2)
        else:
            merged.append(dict(s))
    return merged


def _build_columns(pid, geom, scale):
    """柱构件: 平法(KZ/GZ/Z) > 标注推导 > 几何样本 > 默认"""
    cols = []
    texts = pid.get('施工说明', [])
    _, col_rebars, _ = _parse_flat_labels(texts)
    rebar_by_name = {c['编号']: c for c in col_rebars if c.get('编号')}

    # 1. 平法标注(每条文一条构件)
    for cr in col_rebars:
        name = cr.get('编号') or '柱-?'
        w, h = cr.get('截面宽_mm', DEFAULT_COL_SECTION[0]), cr.get('截面高_mm', DEFAULT_COL_SECTION[1])
        col = {
            '编号': name, '数量': 1, '截面宽_mm': w, '截面高_mm': h,
            '高度_m': None, '层': '',
            '钢筋': {'纵筋': cr.get('纵筋', []), '箍筋': cr.get('箍筋', {}), '来源': '平法标注'},
            '截面来源': '平法标注', '位置来源': '无', '位置': None,
            '置信度': _confidence('平法标注', '默认', True, '无'),
            '证据': [f"施工说明#平法标注 {name}"],
        }
        cols.append(col)

    # 2. 几何样本 + 标注推导(无平法时)
    samples = (pid.get('建筑信息', {}) or {}).get('构件尺寸样本', {}) or {}
    dim_sizes = pid.get('构件尺寸推导', {}) or {}
    dims = pid.get('标注关联', []) or []
    if not cols:
        for layer, dims_d in dim_sizes.items():
            if not _layer_is(layer, COLUMN_LAYER_KW):
                continue
            if dims_d.get('宽_mm') and dims_d.get('高_mm'):
                name = re.sub(r'\D+', '', layer) or None
                name = f"柱-{name}" if name else '柱-1'
                cols.append({
                    '编号': name, '数量': 1,
                    '截面宽_mm': int(dims_d['宽_mm']), '截面高_mm': int(dims_d['高_mm']),
                    '高度_m': None, '层': '',
                    '钢筋': {}, '截面来源': '标注推导', '位置来源': '无', '位置': None,
                    '置信度': _confidence('标注推导', '默认', False, '无'),
                    '证据': [f"构件尺寸推导#{layer}"],
                })
        if not cols:
            for key in ('框架柱', '柱'):
                ss = samples.get(key) or []
                if ss:
                    ws = [s.get('宽_mm', 0) for s in ss if s.get('宽_mm')]
                    hs = [s.get('高_mm', 0) for s in ss if s.get('高_mm')]
                    if ws and hs:
                        cols.append({
                            '编号': '柱-1', '数量': len(ss),
                            '截面宽_mm': int(sum(ws) / len(ws)), '截面高_mm': int(sum(hs) / len(hs)),
                            '高度_m': None, '层': '', '钢筋': {},
                            '截面来源': '几何样本', '位置来源': '无', '位置': None,
                            '置信度': _confidence('几何样本', '默认', False, '无'),
                            '证据': ['建筑信息.构件尺寸样本#柱'],
                        })
                        break

    # 3. 位置: 标注关联中柱图层的标注中点
    if dims and cols:
        for dm in dims:
            layer = dm.get('layer', '')
            if not _layer_is(layer, COLUMN_LAYER_KW) or not cols[0].get('位置'):
                continue
            if dm.get('x') is not None and dm.get('y') is not None:
                cols[0]['位置'] = {'x_m': round(dm['x'] / scale, 2), 'y_m': round(dm['y'] / scale, 2)}
                cols[0]['位置来源'] = '标注中点'
                cols[0]['置信度'] = round(min(cols[0]['置信度'] * 1.2, 0.99), 2)

    # 4. 高度: 层高证据(标高参数 > 说明 > 默认)
    floor_h = float((pid.get('标高参数', {}) or {}).get('层高_m') or 0) or \
        _first_float(' '.join(texts), [r'层高[为]?\s*(\d+\.?\d*)\s*m'], 2.0, 8.0, DEFAULT_FLOOR_H)
    for c in cols:
        c['高度_m'] = round(floor_h, 2)
    return cols


def _build_beams(pid, geom, scale):
    """梁构件: 平法(KL/WKL/LL) > 标注推导(水平标注) > 几何线段 > 说明 > 默认4.5"""
    beams = []
    texts = pid.get('施工说明', [])
    beam_rebars, _, _ = _parse_flat_labels(texts)
    rebar_by_name = {b['编号']: b for b in beam_rebars if b.get('编号')}

    dims = pid.get('标注关联', []) or []
    dim_sizes = pid.get('构件尺寸推导', {}) or {}
    samples = (pid.get('建筑信息', {}) or {}).get('构件尺寸样本', {}) or {}

    # 图层名 → 编号(梁-KL1 层 → KL1)
    def _name_from_layer(layer):
        m = re.search(r'([A-Z]{1,3}\d+)', (layer or '').upper())
        if m:
            return m.group(1)
        return None

    # 标注水平长度按图层聚合(同层取 max)
    layer_dim_len = {}
    for dm in dims:
        layer = dm.get('layer', '')
        if not _layer_is(layer, BEAM_LAYER_KW):
            continue
        if dm.get('type') == 'horizontal' and dm.get('value'):
            layer_dim_len[layer] = max(layer_dim_len.get(layer, 0), float(dm['value']) / scale)

    # 几何线段长度按图层聚合
    layer_geo_len = {}
    for layer, g in geom.items():
        if _layer_is(layer, BEAM_LAYER_KW) and g['len'] > 0:
            layer_geo_len[layer] = max(layer_geo_len.get(layer, 0), g['len'] / scale)

    # 1. 平法标注
    for br in beam_rebars:
        name = br.get('编号') or '梁-?'
        w, h = br.get('截面宽_mm', DEFAULT_BEAM_SECTION[0]), br.get('截面高_mm', DEFAULT_BEAM_SECTION[1])
        # 长度: 标注水平值 > 几何线段 > 说明 > 默认
        ln_src, ln = '默认', None
        for layer in dim_sizes:
            if _layer_is(layer, BEAM_LAYER_KW) and dim_sizes[layer].get('长_mm'):
                ln = float(dim_sizes[layer]['长_mm']) / scale
                ln_src = '标注推导'
                break
        if ln is None:
            for layer, v in layer_dim_len.items():
                if _layer_is(layer, BEAM_LAYER_KW) and v > 0:
                    ln = v
                    ln_src = '标注'
                    break
        if ln is None:
            for layer, v in layer_geo_len.items():
                if v > 0:
                    ln = v
                    ln_src = '几何'
                    break
        if ln is None:
            ln = _first_float(' '.join(texts), [r'梁长[为]?\s*(\d+\.?\d*)\s*m'], 2, 60, DEFAULT_BEAM_LEN)
            if abs(ln - DEFAULT_BEAM_LEN) < 1e-9:
                ln_src = '默认'
            else:
                ln_src = '施工说明'
        beams.append({
            '编号': name, '数量': 1, '跨数': br.get('跨数', 1),
            '截面宽_mm': w, '截面高_mm': h, '长度_m': round(ln, 2), '层': '',
            '钢筋': {'上部筋': br.get('上部筋', []), '下部筋': br.get('下部筋', []),
                    '构造筋': br.get('构造筋', []), '箍筋': br.get('箍筋', {}), '来源': '平法标注'},
            '截面来源': '平法标注', '长度来源': ln_src, '位置来源': '无', '位置': None,
            '置信度': _confidence('平法标注', ln_src, True, '无'),
            '证据': [f"施工说明#平法标注 {name}"],
        })

    # 2. 无平法: 几何/标注证据
    if not beams:
        for layer in dim_sizes:
            if not _layer_is(layer, BEAM_LAYER_KW):
                continue
            dd = dim_sizes[layer]
            w, h = dd.get('宽_mm'), dd.get('高_mm')
            if not (w and h):
                continue
            name = _name_from_layer(layer) or '梁-1'
            ln = float(dd.get('长_mm', 0)) / scale if dd.get('长_mm') else DEFAULT_BEAM_LEN
            beams.append({
                '编号': name, '数量': 1, '跨数': 1, '截面宽_mm': int(w), '截面高_mm': int(h),
                '长度_m': round(ln, 2), '层': '', '钢筋': {},
                '截面来源': '标注推导', '长度来源': '标注推导' if dd.get('长_mm') else '默认',
                '位置来源': '无', '位置': None,
                '置信度': _confidence('标注推导', '标注推导' if dd.get('长_mm') else '默认', False, '无'),
                '证据': [f"构件尺寸推导#{layer}"],
            })
        if not beams:
            for key in ('框架梁', '梁'):
                ss = samples.get(key) or []
                if ss:
                    ws = [s.get('宽_mm', 0) for s in ss if s.get('宽_mm')]
                    hs = [s.get('高_mm', 0) for s in ss if s.get('高_mm')]
                    if ws and hs:
                        ln = DEFAULT_BEAM_LEN
                        for layer, v in layer_dim_len.items():
                            if v > 0:
                                ln = v
                                break
                        beams.append({
                            '编号': '梁-1', '数量': len(ss), '跨数': 1,
                            '截面宽_mm': int(sum(ws) / len(ws)), '截面高_mm': int(sum(hs) / len(hs)),
                            '长度_m': round(ln, 2), '层': '', '钢筋': {},
                            '截面来源': '几何样本', '长度来源': '标注' if ln != DEFAULT_BEAM_LEN else '默认',
                            '位置来源': '无', '位置': None,
                            '置信度': _confidence('几何样本', '标注' if ln != DEFAULT_BEAM_LEN else '默认', False, '无'),
                            '证据': ['建筑信息.构件尺寸样本#梁'],
                        })
                        break
    return beams


def _build_slabs(pid, geom, scale):
    """板构件: 平法板厚 > 构造层(钢筋混凝土楼板) > 说明 > 默认120; 面积=面积区域合计"""
    texts = pid.get('施工说明', [])
    _, _, slab_rebars = _parse_flat_labels(texts)
    areas = pid.get('面积区域', []) or []
    total_area = sum(a.get('面积_m2', 0) for a in areas)

    thick, thick_src = None, ''
    if slab_rebars:
        thick = slab_rebars[0].get('厚度_mm')
        thick_src = '平法标注'
    if not thick:
        for l in pid.get('构造层', []) or []:
            nm = l.get('名称', '')
            if ('钢筋混凝土' in nm or '楼板' in nm or '混凝土板' in nm) and l.get('厚度_mm'):
                thick = int(l['厚度_mm'])
                thick_src = '构造层'
                break
    if not thick:
        tc = ' '.join(texts)
        thick = int(_first_float(tc, [r'板厚[为]?\s*(\d+)\s*mm', r'(\d+)\s*mm\s*厚[的]?板'], 60, 500, DEFAULT_SLAB_THICK))
        thick_src = '施工说明' if thick != DEFAULT_SLAB_THICK else '默认'

    # 面积来源: 闭合多段线(面积区域) — 与旧算量同源
    pos = None
    for a in areas:
        if a.get('位置'):
            pos = a['位置']
            break
    slab = {
        '编号': '板-1', '数量': 1, '厚度_mm': thick, '面积_m2': round(total_area, 2),
        '配筋': slab_rebars[0] if slab_rebars else {},
        '厚度来源': thick_src, '面积来源': areas[0].get('面积来源', '闭合多段线') if areas else '',
        '位置': pos, '位置来源': '几何形心' if pos else '无',
        '置信度': _confidence(thick_src, '几何', bool(slab_rebars), '几何形心' if pos else '无'),
        '证据': [f"面积区域#{areas[0].get('名称', '主区域')}"] if areas else [],
    }
    if slab_rebars:
        slab['证据'].append('施工说明#平法板标注')
    return [slab] if total_area > 0 else []


def _build_walls(pid, geom, scale):
    """墙构件: 厚度(说明/构造层 > 几何样本 > 默认200); 长度=面积区域周长(首轮近似)"""
    texts = pid.get('施工说明', [])
    areas = pid.get('面积区域', []) or []
    total_area = sum(a.get('面积_m2', 0) for a in areas)
    perim = max([a.get('周长_m', 0) for a in areas], default=0.0)

    thick, thick_src = None, ''
    tc = ' '.join(texts)
    m = re.search(r'墙厚[为]?\s*(\d+)\s*mm', tc)
    if m:
        thick = int(m.group(1))
        thick_src = '施工说明'
    if not thick:
        for l in pid.get('构造层', []) or []:
            nm = l.get('名称', '')
            if '墙' in nm and l.get('厚度_mm'):
                thick = int(l['厚度_mm'])
                thick_src = '构造层'
                break
    if not thick:
        samples = (pid.get('建筑信息', {}) or {}).get('构件尺寸样本', {}) or {}
        for key in ('墙体', '墙'):
            ss = samples.get(key) or []
            # 几何样本的"高"可能是板边界(如15000mm), 过滤不合理墙厚(>600mm 弃)
            hs = [s.get('高_mm', 0) for s in ss if s.get('高_mm') and 60 <= s.get('高_mm', 0) <= 600]
            if hs:
                thick = int(sum(hs) / len(hs))
                thick_src = '几何样本'
                break
    if not thick:
        thick = DEFAULT_WALL_THICK
        thick_src = '默认'

    wall = {
        '编号': 'W-1', '数量': 1, '厚度_mm': thick, '长度_m': round(perim, 2),
        '面积_m2': round(total_area, 2), '材料': '',
        '厚度来源': thick_src, '长度来源': '周长', '位置来源': '无', '位置': None,
        '置信度': _confidence(thick_src, '几何', False, '无'),
        '证据': [f"面积区域#{areas[0].get('名称', '主区域')}周长"] if areas else [],
    }
    # 材料从构造层/说明提取
    for l in pid.get('构造层', []) or []:
        if '墙' in l.get('名称', '') and l.get('材料'):
            wall['材料'] = l['材料']
            break
    if not wall['材料']:
        m = re.search(r'([一-龥]*砌块[一-龥]*)', tc)
        if m:
            wall['材料'] = m.group(1)
    return [wall] if total_area > 0 else []


def _build_rooms(pid, geom, scale):
    """房间: 面积区域 → 房间对象(首轮单区域, 做法列表预留)"""
    areas = pid.get('面积区域', []) or []
    rooms = []
    for a in areas:
        r = {
            '编号': 'R1' if not rooms else f'R{len(rooms) + 1}',
            '面积_m2': a.get('面积_m2', 0), '周长_m': a.get('周长_m', 0),
            '做法': [], '面积来源': a.get('面积来源', '闭合多段线'),
            '位置': a.get('位置'), '位置来源': '几何形心' if a.get('位置') else '无',
            '置信度': 1.0,
            '证据': [f"面积区域#{a.get('名称', '主区域')}"],
        }
        rooms.append(r)
    return rooms


def build_component_model(pid, msp=None):
    """七路证据 → 构件对象清单。pid = 识图结果 dict(构建完成后调用)。
    房建: 柱/梁/板/墙/房间 + 装饰; 安装: 设备/管道等8类(分系统);
    钢构: 钢构件; 市政: 道路/路基/路缘石/管网; 园林: 乔木/灌木/草坪/种植土。
    """
    specialty = pid.get('专业类型', '')
    if specialty == '房屋建筑与装饰工程':
        scale = _unit_scale_m(pid)
        geom = _collect_geometry(msp)
        model = {
            '柱': _merge_same(_build_columns(pid, geom, scale)),
            '梁': _merge_same(_build_beams(pid, geom, scale)),
            '板': _build_slabs(pid, geom, scale),
            '墙': _build_walls(pid, geom, scale),
            '房间': _build_rooms(pid, geom, scale),
        }
        deco = _build_decoration(pid)
        model.update(deco)
        return model
    if specialty == '安装工程':
        return _build_mep(pid, msp)
    if specialty == '钢结构工程':
        return _build_steel(pid, msp)
    if specialty == '市政工程':
        return _build_civil(pid)
    if specialty == '园林绿化工程':
        return _build_garden(pid)
    return {'柱': [], '梁': [], '板': [], '墙': [], '房间': []}


# ───────────────────────── 安装工程(分系统构件化 v5.12 P1-3) ─────────────────────────

# 图块明细类别 → 构件类别(与 calc_mep 消费的安装信息键一致)
MEP_CATEGORY_MAP = {
    '设备': '设备', '阀门': '阀门', '灯具': '灯具', '开关插座': '开关插座',
    '配电箱柜': '配电箱柜', '卫生器具': '卫生器具', '消防设施': '消防设施',
}

# 系统归属(构件模型键 → 系统名)
MEP_SYSTEM_LABEL = {
    '给排水': ('给水', '排水', '雨水', '污水', '给水管', '排水管', 'PPR', 'UPVC', 'HDPE', '衬塑'),
    '电气': ('电气', '强电', '弱电', '照明', '线缆', '电缆', '桥架', '配管', 'SC', 'JDG', 'BV', 'YJV', 'VV'),
    '暖通': ('暖通', '通风', '空调', '风管', '风机', '新风', '排风'),
    '消防': ('消防', '喷淋', '消火栓', '报警', '烟感', '探头', '防火'),
}


def _system_of(name, spec=''):
    """按关键词判定构件系统归属(默认给排水)。"""
    text = (name or '') + ' ' + (spec or '')
    for sys_name, kws in MEP_SYSTEM_LABEL.items():
        if any(k in text for k in kws):
            return sys_name
    return '给排水'


def _build_mep(pid, msp):
    """安装构件: 图块明细 + 线性构件 + 安装信息 → 构件对象。
    输出 8 类计数键 + 分系统构件(电气电缆/桥架/配管/风管/喷淋管道):
    {设备, 阀门, 灯具, 开关插座, 配电箱柜, 卫生器具, 消防设施, 管道,
     电缆, 桥架, 配管, 风管, 喷淋管道}
    """
    out = {k: [] for k in ('设备', '阀门', '灯具', '开关插座', '配电箱柜',
                           '卫生器具', '消防设施', '管道',
                           '电缆', '桥架', '配管', '风管', '喷淋管道')}
    blocks = pid.get('图块明细', {}) or {}
    scale = _unit_scale_m(pid)
    from block_enhanced import readable_name  # v5.10: 加密块名可读化(天正HC编码)
    for cat, key in MEP_CATEGORY_MAP.items():
        for it in blocks.get(cat, []) or []:
            name = it.get('name', '')
            disp_name = readable_name(name, it.get('layer', ''), cat)
            spec = it.get('spec', '')
            cnt = it.get('count', 1)
            pos = None
            if it.get('x') is not None and it.get('y') is not None:
                pos = {'x_m': round(it['x'] / scale, 2), 'y_m': round(it['y'] / scale, 2)}
            out[key].append({
                '编号': disp_name, '规格': spec, '数量': cnt,
                '位置': pos, '位置来源': '图块坐标' if pos else '无',
                '系统': _system_of(disp_name, spec),
                '置信度': round(_confidence('几何样本', '默认', bool(spec), '图块坐标' if pos else '无'), 2),
                '证据': [f"图块明细.{cat}#{name}"],
            })
    # 管道: 线性构件(管径/系统)
    for p in pid.get('线性构件', []) or []:
        if p.get('类型') != '管道':
            continue
        name = p.get('名称', '管道')
        dia = p.get('管径', '')
        out['管道'].append({
            '编号': name, '规格': dia, '数量': 1, '长度_m': p.get('长度_m', 0),
            '位置': None, '位置来源': '无', '系统': p.get('系统', '') or _system_of(name, dia),
            '置信度': round(_confidence('几何', '几何', bool(dia), '无'), 2),
            '证据': [f"线性构件#{name}"],
        })

    # 电气线缆 → 电缆构件(v5.10 分流, 不进管道口径)
    for c in pid.get('电气线缆', []) or []:
        name = c.get('名称', '') or c.get('系统', '') + '线缆'
        out['电缆'].append({
            '编号': name[:20], '规格': c.get('管径', '') or '', '数量': 1,
            '长度_m': c.get('长度_m', 0) or 0, '型号': c.get('系统', '') or '',
            '位置': None, '位置来源': '无', '系统': '电气',
            '置信度': round(_confidence('几何', '几何', False, '无'), 2),
            '证据': [f"电气线缆#{name}"],
        })

    # 安装信息兜底: 电缆/桥架/管线/暖通/消防(识别层已填充的键)
    info = pid.get('安装信息', {}) or {}
    for c in info.get('电缆', []) or []:
        if not out['电缆']:
            out['电缆'].append({
                '编号': '电缆', '规格': c.get('型号', ''), '数量': 1,
                '长度_m': c.get('长度_m', 0) or 0, '型号': c.get('型号', ''),
                '位置': None, '位置来源': '无', '系统': '电气',
                '置信度': 0.6, '证据': ['安装信息#电缆'],
            })
    for t in info.get('桥架', []) or []:
        out['桥架'].append({
            '编号': '桥架', '规格': t.get('规格', '') or '', '数量': 1,
            '长度_m': t.get('长度_m', 0) or 0,
            '位置': None, '位置来源': '无', '系统': '电气',
            '置信度': 0.6, '证据': ['安装信息#桥架'],
        })
    for t in info.get('管线', []) or []:
        out['配管'].append({
            '编号': '电线管', '规格': t.get('规格', '') or '', '数量': 1,
            '长度_m': t.get('长度_m', 0) or 0,
            '位置': None, '位置来源': '无', '系统': '电气',
            '置信度': 0.6, '证据': ['安装信息#管线'],
        })
    hvac = info.get('暖通', {}) or {}
    for d in hvac.get('风管', []) or []:
        out['风管'].append({
            '编号': '风管', '规格': d.get('规格', '') or '', '数量': 1,
            '面积_m2': d.get('面积_m2', 0) or 0, '长度_m': d.get('长度_m', 0) or 0,
            '位置': None, '位置来源': '无', '系统': '暖通',
            '置信度': 0.6, '证据': ['安装信息.暖通#风管'],
        })
    fire = info.get('消防', {}) or {}
    for p in fire.get('喷淋管道', []) or []:
        out['喷淋管道'].append({
            '编号': '喷淋管道', '规格': p.get('管径', '') or '', '数量': 1,
            '长度_m': p.get('长度_m', 0) or 0,
            '位置': None, '位置来源': '无', '系统': '消防',
            '置信度': 0.6, '证据': ['安装信息.消防#喷淋管道'],
        })
    return out


# ───────────────────────── 钢结构工程 ─────────────────────────

def _build_steel(pid, msp):
    """钢构件: 钢结构.构件(文字提取) → 构件对象(补位置/置信度/证据链)"""
    out = {'钢构件': []}
    mems = (pid.get('钢结构', {}) or {}).get('构件', []) or []
    for m in mems:
        name = m.get('名称', '钢构件')
        params = m.get('截面参数', '')
        if isinstance(params, (list, tuple)):
            params = '×'.join(str(p) for p in params)
        out['钢构件'].append({
            '编号': name,
            '规格': f"{m.get('截面类型', '')}{params}".strip(),
            '长度_m': m.get('长度_m', 0),
            '数量': 1, '位置': None, '位置来源': '无',
            '置信度': round(_confidence('几何样本', '几何' if m.get('长度_m') else '默认', False, '无'), 2),
            '证据': [f"钢结构.构件#{name}"],
        })
    return out


# ───────────────────────── 市政工程构件化(v5.12 P1-1) ─────────────────────────

# 构造层类型判定关键词
CIVIL_SURFACE_KW = ('沥青', '混凝土面层', '面层', '罩面')
CIVIL_BASE_KW = ('基层', '稳定', '级配', '碎石', '底基层', '垫层')
CIVIL_THIN_KW = ('透层', '粘层', '封层', '防水层')  # 无厚度类, 按 100m² 计
CIVIL_CURB_KW = ('侧石', '缘石', '路缘', '边石', '平石')
CIVIL_PIPE_KW = ('管道', '管线', '给水', '排水', '雨水', '污水', '电力', '通信')


def _build_civil(pid, msp=None):
    """市政构件: 构造层→道路面层/基层; 面积区域→路基; 线性构件→路缘石/管网。
    输出: {'道路面层':[...], '道路基层':[...], '路基':[...], '路缘石':[...], '管网':[...]}
    """
    out = {'道路面层': [], '道路基层': [], '路基': [], '路缘石': [], '管网': []}
    areas = pid.get('面积区域', []) or []
    total = sum(a.get('面积_m2', 0) or 0 for a in areas)
    perim = max([a.get('周长_m', 0) or 0 for a in areas], default=0.0)

    # 构造层 → 面层/基层(含透层等无厚度类)
    for l in pid.get('构造层', []) or []:
        name = l.get('名称', '') or ''
        mat = l.get('材料', '') or ''
        thick = l.get('厚度_mm', 0) or 0
        src = l.get('厚度来源', '') or '未识别'
        combined = name + ' ' + mat
        if any(k in combined for k in CIVIL_THIN_KW) or thick == 0 and any(k in combined for k in ('透层', '粘层', '封层')):
            out['道路基层'].append({
                '编号': name[:20], '规格': mat, '数量': 1,
                '厚度_mm': 0, '面积_m2': round(total, 2),
                '类别': '薄层', '厚度来源': src,
                '位置': None, '位置来源': '无',
                '置信度': round(_confidence('几何样本' if total else '默认', '几何', False, '无'), 2),
                '证据': [f"构造层#{name}"],
            })
            continue
        if thick > 0 and any(k in combined for k in ('沥青', '混凝土', '面层', '碎石', '稳定', '级配', '基层', '垫层', '水稳')):
            is_surface = any(k in combined for k in CIVIL_SURFACE_KW) or '沥青' in combined or ('混凝土' in combined and '基层' not in combined and '底基层' not in combined)
            key = '道路面层' if is_surface else '道路基层'
            out[key].append({
                '编号': name[:20], '规格': mat, '数量': 1,
                '厚度_mm': float(thick), '面积_m2': round(total, 2),
                '类别': '面层' if key == '道路面层' else '基层',
                '厚度来源': src,
                '位置': None, '位置来源': '无',
                '置信度': round(_confidence('几何样本' if total else '默认', '几何', False, '无'), 2),
                '证据': [f"构造层#{name}"],
            })

    # 路基: 面积区域(挖深由算量层从标高/说明提取, 构件只带面积)
    if total > 0:
        out['路基'].append({
            '编号': '路基-1', '规格': '', '数量': 1,
            '面积_m2': round(total, 2), '周长_m': round(perim, 2),
            '位置': areas[0].get('位置'), '位置来源': '几何形心' if areas[0].get('位置') else '无',
            '置信度': 1.0,
            '证据': [f"面积区域#{areas[0].get('名称', '主区域')}"],
        })

    # 线性构件 → 路缘石/管网(含管道/线缆)
    for item in pid.get('线性构件', []) or []:
        nm = item.get('名称', '') or ''
        typ = item.get('类型', '') or ''
        ln = item.get('长度_m', 0) or 0
        if any(k in nm for k in CIVIL_CURB_KW):
            out['路缘石'].append({
                '编号': nm[:20], '规格': '', '数量': 1, '长度_m': round(ln, 2),
                '类别': '路缘石',
                '位置': None, '位置来源': '无',
                '置信度': round(_confidence('几何', '几何', False, '无'), 2),
                '证据': [f"线性构件#{nm}"],
            })
        elif typ == '管道' or any(k in nm for k in CIVIL_PIPE_KW):
            out['管网'].append({
                '编号': nm[:20], '规格': item.get('管径', '') or '', '数量': 1,
                '长度_m': round(ln, 2), '系统': item.get('系统', '') or '',
                '类别': '管道',
                '位置': None, '位置来源': '无',
                '置信度': round(_confidence('几何', '几何', False, '无'), 2),
                '证据': [f"线性构件#{nm}"],
            })
    return out


# ───────────────────────── 园林绿化工程构件化(v5.12 P1-2) ─────────────────────────

GARDEN_SHRUB_KW = ('灌木', '灌木丛', 'shrub', 'SHRUB', '绿篱', '地被', 'BOX')


def _build_garden(pid, msp=None):
    """园林构件: 图块明细/园林信息→乔木/灌木; 面积区域→草坪/绿地; 构造层→种植土。
    输出: {'乔木':[...], '灌木':[...], '草坪':[...], '种植土':[...], '硬景':[...]}
    """
    out = {'乔木': [], '灌木': [], '草坪': [], '种植土': [], '硬景': []}
    areas = pid.get('面积区域', []) or []
    total = sum(a.get('面积_m2', 0) or 0 for a in areas)

    # 苗木: 图块明细优先, 园林信息兜底
    def _plant(kw):
        return any(k in kw for k in GARDEN_SHRUB_KW)
    trees, shrubs = 0, 0
    bd = pid.get('图块明细', {}) or {}
    for item in bd.get('苗木', []) or []:
        nm = item.get('name', '') or ''
        cnt = item.get('count', 0) or 0
        if _plant(nm):
            shrubs += cnt
        else:
            trees += cnt
    gi = pid.get('园林信息', {}) or {}
    for t in gi.get('苗木', {}).get('乔木', []) or []:
        trees = max(trees, t.get('数量', 0) or 0)
    for s in gi.get('苗木', {}).get('灌木', []) or []:
        shrubs = max(shrubs, s.get('数量', 0) or 0)
    if trees > 0:
        out['乔木'].append({'编号': '乔木', '规格': '', '数量': trees,
                            '位置': None, '位置来源': '无',
                            '置信度': 0.7, '证据': ['图块明细/园林信息#苗木']})
    if shrubs > 0:
        out['灌木'].append({'编号': '灌木', '规格': '', '数量': shrubs,
                            '位置': None, '位置来源': '无',
                            '置信度': 0.7, '证据': ['图块明细/园林信息#苗木']})

    # 草坪/绿地: 面积区域 × 草坪系数(算量层提取), 构件只带总面积
    if total > 0:
        out['草坪'].append({'编号': '草坪-1', '规格': '', '数量': 1,
                            '面积_m2': round(total, 2),
                            '位置': areas[0].get('位置'), '位置来源': '几何形心' if areas[0].get('位置') else '无',
                            '置信度': 1.0, '证据': [f"面积区域#{areas[0].get('名称', '主区域')}"]})
        out['种植土'].append({'编号': '种植土-1', '规格': '', '数量': 1,
                              '面积_m2': round(total, 2), '深度_m': None,
                              '位置': None, '位置来源': '无',
                              '置信度': 0.8, '证据': ['面积区域#主区域']})

    # 种植土深度: 构造层(种植土) → 构件带深度(算量层直接用)
    for l in pid.get('构造层', []) or []:
        if '种植土' in (l.get('名称', '') or ''):
            t = l.get('厚度_mm')
            if t:
                out['种植土'][0]['深度_m'] = round(float(t) / 1000, 2)
                out['种植土'][0]['证据'].append(f"构造层#{l.get('名称', '')}")
            break

    # 硬景: 铺装面积/路缘石
    hard = gi.get('硬景', {}) or {}
    pave = hard.get('铺装_m2', 0) or 0
    curb = hard.get('路缘石_m', 0) or 0
    for item in pid.get('线性构件', []) or []:
        nm = item.get('名称', '') or ''
        if '缘石' in nm or '侧石' in nm:
            curb = max(curb, item.get('长度_m', 0) or 0)
    if pave > 0:
        out['硬景'].append({'编号': '硬景铺装', '规格': '', '数量': 1,
                            '面积_m2': round(pave, 2),
                            '位置': None, '位置来源': '无',
                            '置信度': 0.8, '证据': ['园林信息.硬景#铺装_m2']})
    if curb > 0:
        out['硬景'].append({'编号': '路缘石', '规格': '', '数量': 1,
                            '长度_m': round(curb, 2),
                            '位置': None, '位置来源': '无',
                            '置信度': 0.8, '证据': ['园林信息.硬景#路缘石_m']})
    return out


# ───────────────────────── 精装修工程构件化(v5.12 P1-4) ─────────────────────────
# 位置词(仅在含位置词时才算该部位材料; 材料表从 calc_decoration 导入保证同口径)
DECO_FLOOR_POS = ('地面', '地板', '地砖', '石材', '地毯', '自流平', '架空', '防静电', '楼面', '楼地面')
DECO_WALL_POS = ('墙面', '墙纸', '壁纸', '瓷砖', '墙砖', '石材', '干挂', '木饰面', '乳胶漆', '内墙')
DECO_CEIL_POS = ('天棚', '吊顶', '铝扣板', '石膏板', '天花')

def _build_decoration(pid, msp=None):
    """精装构件: 构造层/做法表 + 施工说明 → 楼地面/墙面/天棚/细部 构件(带材料/厚度/面积/周长)。
    收集口径与 calc_decoration.calc_decoration_detail 完全一致:
    - 构造层+施工说明两路收集
    - 同一文本可命中多个材料组(如 自流平+实木复合地板 同层)
    构件带 材料名(分组名), 算量层直接消费不再二次匹配。
    输出: {'楼地面':[...], '墙面':[...], '天棚':[...], '细部':[...]}
    """
    out = {'楼地面': [], '墙面': [], '天棚': [], '细部': []}
    areas = pid.get('面积区域', []) or []
    total = sum(a.get('面积_m2', 0) or 0 for a in areas)
    perim = max([a.get('周长_m', 0) or 0 for a in areas], default=0.0)
    texts = pid.get('施工说明', []) or []
    layers = pid.get('构造层', []) or []
    floor_h = float((pid.get('标高参数', {}) or {}).get('层高_m') or 0) or 3.0

    # 收集器: 材料名 → (计数, 备注文本)。与 calc_decoration_detail 同口径。
    floor_mats, wall_mats, ceil_mats, detail_mats = {}, {}, {}, {}

    # 材料表与 calc_decoration 完全一致(避免口径漂移)
    from calc_decoration import (FLOOR_MATERIALS, WALL_MATERIALS,
                                 CEILING_MATERIALS, DETAIL_MATERIALS)

    def _collect(text, table, pos_kws, mats):
        for kws, name, unit in table:
            if any(k in text for k in kws) and any(k in text for k in pos_kws):
                mats[name] = mats.get(name, 0) + 1

    for l in layers:
        combined = (l.get('名称', '') or '') + ' ' + (l.get('材料', '') or '')
        if not combined.strip():
            continue
        _collect(combined, DETAIL_MATERIALS, ('踢脚', '门套', '门框', '窗台', '踏步', '窗帘盒', '石膏线'), detail_mats)
        _collect(combined, FLOOR_MATERIALS, DECO_FLOOR_POS, floor_mats)
        _collect(combined, WALL_MATERIALS, DECO_WALL_POS, wall_mats)
        _collect(combined, CEILING_MATERIALS, DECO_CEIL_POS, ceil_mats)
    for t in texts:
        if not t.strip():
            continue
        _collect(t, DETAIL_MATERIALS, ('踢脚', '门套', '门框', '窗台', '踏步', '窗帘盒', '石膏线'), detail_mats)
        _collect(t, FLOOR_MATERIALS, DECO_FLOOR_POS, floor_mats)
        _collect(t, WALL_MATERIALS, DECO_WALL_POS, wall_mats)
        _collect(t, CEILING_MATERIALS, DECO_CEIL_POS, ceil_mats)

    # 材料名 → 构件对象(每个材料一个构件, 数量=命中次数)
    for mname, cnt in sorted(floor_mats.items()):
        out['楼地面'].append({
            '编号': f'楼地面-{mname}', '规格': '', '数量': cnt,
            '面积_m2': round(total, 2),
            '类别': mname, '材料名': mname, '单位': 'm²',
            '位置': None, '位置来源': '无',
            '置信度': 0.8, '证据': ['构造层/施工说明#楼地面'],
        })
    for mname, cnt in sorted(wall_mats.items()):
        out['墙面'].append({
            '编号': f'墙面-{mname}', '规格': '', '数量': cnt,
            '面积_m2': round(total * 2.8, 2),
            '类别': mname, '材料名': mname, '单位': 'm²',
            '周长_m': round(perim, 2), '层高_m': floor_h,
            '位置': None, '位置来源': '无',
            '置信度': 0.8, '证据': ['构造层/施工说明#墙面'],
        })
    for mname, cnt in sorted(ceil_mats.items()):
        out['天棚'].append({
            '编号': f'天棚-{mname}', '规格': '', '数量': cnt,
            '面积_m2': round(total, 2),
            '类别': mname, '材料名': mname, '单位': 'm²',
            '位置': None, '位置来源': '无',
            '置信度': 0.8, '证据': ['构造层/施工说明#天棚'],
        })
    for mname, cnt in sorted(detail_mats.items()):
        out['细部'].append({
            '编号': f'细部-{mname}', '规格': '', '数量': cnt,
            '周长_m': round(perim, 2), '面积_m2': round(total, 2),
            '类别': mname, '材料名': mname, '单位': 'm' if mname in ('踢脚线', '门套', '窗台板', '窗帘盒', '石膏装饰线') else 'm²',
            '位置': None, '位置来源': '无',
            '置信度': 0.8, '证据': ['构造层/施工说明#细部'],
        })
    return out
