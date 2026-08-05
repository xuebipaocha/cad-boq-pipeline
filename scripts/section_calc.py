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

SECTION_KW = ['剖面', '断面', 'SECTION', 'SEC-', '1-1', '2-2', '3-3', 'A-A', 'B-B', 'C-C']


def find_sections(msp):
    """识别剖面图: 返回 [{name, texts, y_range}] 按文字聚集区分"""
    texts = []
    for e in msp.query('TEXT MTEXT'):
        try:
            if e.dxftype() == 'TEXT':
                ins = e.dxf.insert
                txt = e.dxf.text or ''
            else:
                ins = e.dxf.insert
                txt = e.text or ''
            if txt.strip():
                texts.append({'x': float(ins.x), 'y': float(ins.y), 'text': txt.strip()})
        except Exception:
            continue

    # 找剖面标记文字
    marks = [t for t in texts if any(k in t['text'] for k in SECTION_KW)]
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
    # 取中位数(排除零散短线)
    segs.sort()
    return segs[len(segs) // 2] / 1000


def section_quantity(msp, section, layer_kw=None):
    """剖面联动算量: 断面面积 × 平面长度"""
    area, layer = profile_area(msp, section)
    if area <= 0:
        return None
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
