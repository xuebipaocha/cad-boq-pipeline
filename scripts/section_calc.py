# -*- coding: utf-8 -*-
"""剖面联动算量 — v4.0 第二批

机制:
1. 剖面图识别: 图内文字含 '剖面'/'1-1'/'A-A' 等, 或图层含 '剖面'/'SECTION'
2. 断面轮廓提取: 剖面图内闭合多段线 → 断面面积
3. 平面长度匹配: 剖面图外的平面图中, 沿剖切线方向的构件长度
4. 体积 = 断面面积 × 平面长度 (挡土墙/基础/管沟/路缘石等)

剖面图的几何关系:
- 剖面图通常与平面图同图幅, 以 '1-1'/'A-A' 剖切线符号标注
- 断面面积 = 剖面轮廓内面积
- 长度 = 平面图中对应构件的长度
"""
import re

SECTION_KW = ['剖面', '断面', 'SECTION', 'SEC-']
# v6.6: 剖切线符号(1-1/A-A 等)必须独立 token 匹配 —
# 原 '2-2' 子串误匹配规范编号 'GB50352-2019'/'GB55022-2021' 里的 "2-2",
# 把规范引用文字误判为剖面标记
SECTION_MARK_RE = re.compile(r'(?<![0-9A-Za-z\u4e00-\u9fa5])(?:1-1|2-2|3-3|A-A|B-B|C-C|1\-1剖面|2\-2剖面|3\-3剖面)(?![0-9A-Za-z])')


def _has_section_mark(txt):
    """文字是否含剖面标记(长词直接命中 / 剖切线符号独立 token)。"""
    if any(k in txt for k in SECTION_KW):
        return True
    return SECTION_MARK_RE.search(txt) is not None


def find_sections(msp):
    """识别剖面图: 返回 [{name, texts, y_range}] 按文字聚集区分"""
    texts = []
    for e in msp.query('TEXT MTEXT'):
        try:
            if e.dxftype() == 'TEXT':
                ins = e.dxf.insert
                txt = e.dxf.text or ''
                h = e.dxf.height or 2.5
            else:
                ins = e.dxf.insert
                txt = e.text or ''
                h = (e.dxf.char_height or 2.5) * (e.dxf.line_spacing_factor or 1.0)
            if txt.strip():
                # v6.6: 必须带 'h'(字高)键 — v6.4 cluster_rows 改字高自适应容差后
                # 要求 t['h'], 此处漏同步导致 KeyError('h') 连坐整个剖面联动
                texts.append({'x': float(ins.x), 'y': float(ins.y),
                              'text': txt.strip(), 'h': float(h)})
        except Exception:
            continue

    # 找剖面标记文字
    marks = [t for t in texts if _has_section_mark(t['text'])]
    if not marks:
        return []

    # 按 y 聚类: 剖面图文字聚集区
    from table_parser import cluster_rows
    rows = cluster_rows(texts)
    # 标记文字所在行 → 剖面图区域
    sections = []
    for m in marks:
        for r in rows:
            if abs(m['y'] - r['y']) <= 600:
                # 该行附近的文字集合 = 一个剖面图
                area_texts = [t for t in texts if abs(t['y'] - m['y']) < 5000]
                if area_texts:
                    name = m['text'][:20]
                    if not any(s['name'] == name for s in sections):
                        sections.append({
                            'name': name,
                            'y': m['y'],
                            'mark_x': m['x'],
                            'texts': [t['text'] for t in area_texts[:10]],
                        })
                break
    return sections


def profile_area(msp, section):
    """剖面图内闭合多段线 → 断面面积(m²)
    v4.2.1: 断面轮廓需与剖面标记 x/y 都接近(同一区域), 排除平面图轮廓
    """
    y = section['y']
    best = 0.0
    best_layer = ''
    for e in msp.query('LWPOLYLINE'):
        if not e.closed:
            continue
        try:
            # v6.6: 排除 0 层/图框层 — 断面取到图框轮廓会产出 623.7m²×4m=2494.8m³
            # 这类垃圾体积(真实图纸实测, 0 层是图框专用层不承载实体)
            layer = e.dxf.layer or ''
            if layer == '0' or '图框' in layer or 'TITLE' in layer.upper():
                continue
            pts = list(e.get_points('xy'))
            if len(pts) < 3:
                continue
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            # 剖面标记文字 x 位置(取 section 附近文字的 x 均值)
            mark_x = section.get('mark_x', 0)
            if abs(cy - y) > 8000 or (mark_x and abs(cx - mark_x) > 30000):
                continue
            a = 0
            n = len(pts)
            for j in range(n):
                x1, y1 = pts[j]
                x2, y2 = pts[(j + 1) % n]
                a += x1 * y2 - x2 * y1
            area = abs(a) / 2 / 1e6  # mm²→m²
            if area > best:
                best = area
                best_layer = e.dxf.layer
        except Exception:
            continue
    return best, best_layer


def planar_length(msp, section, layer_kw=None):
    """平面图中沿剖切线方向的构件长度(m)
    v4.2.1: 取平面区域(与剖面 y 距离远)内, 同图层平行线的平均长度
    """
    y = section['y']
    segs = []
    for e in msp.query('LINE'):
        try:
            if e.dxf.layer.startswith('图框') or 'TITLE' in e.dxf.layer.upper():
                continue
            if layer_kw and not any(k in e.dxf.layer for k in layer_kw):
                continue
            length = e.dxf.start.distance(e.dxf.end)
            # 平面图区域(与剖面 y 距离 > 30m)
            if abs(e.dxf.start.y - y) > 30000 and length > 1000:
                segs.append(length)
        except Exception:
            continue
    if not segs:
        return 0.0
    # 取中位数(排除零散短线) — v6.6: 同图层过滤后 segs 即目标构件线,
    # 2 条线中位数(挡土墙 2×50m)合理; 原 '<3 不取' 会误伤有效用例
    segs.sort()
    return segs[len(segs) // 2] / 1000


def section_quantity(msp, section, layer_kw=None):
    """剖面联动算量: 断面面积 × 平面长度"""
    area, layer = profile_area(msp, section)
    if area <= 0:
        return None
    # v6.6: 平面长度按断面图层关键词过滤(挡土墙断面→挡土墙) — 原 layer_kw
    # 恒 None, 全图无关 LINE 混入中位数(船体大楼取到 4.0m 办公室隔墙线)
    if not layer_kw and layer:
        layer_kw = re.sub(r'[断剖]面.*$', '', layer).strip() or None
    length = planar_length(msp, section, layer_kw)
    return {
        '剖面': section['name'],
        '断面面积_m2': round(area, 3),
        '断面图层': layer,
        '平面长度_m': round(length, 1),
        '体积_m3': round(area * length, 2),
    }


def calc_section_quantities(msp):
    """主入口: 全部剖面联动算量"""
    results = []
    sections = find_sections(msp)
    for s in sections:
        q = section_quantity(msp, s)
        if q and q['体积_m3'] > 0:
            results.append(q)
    return results
