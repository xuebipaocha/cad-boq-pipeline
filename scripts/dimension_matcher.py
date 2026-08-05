# -*- coding: utf-8 -*-
"""标注→构件语义关联器 — v4.0 第二批 (重写版, 逻辑最简化)

DIMENSION 实体的延伸线端点(defpoint2/defpoint3) → 端点吸附构件
→ 输出标注与构件的语义关联, 推导构件尺寸。
"""
import math

MATCH_TOL = 300  # 端点吸附容差(图纸单位, mm 制 ≈ 30cm)


def _dist(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def _xy(p):
    return (float(p[0]), float(p[1]))


def _point_seg_dist(pt, p1, p2):
    """点到线段距离"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return _dist(pt, p1)
    t = ((pt[0] - p1[0]) * dx + (pt[1] - p1[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    proj = (p1[0] + t * dx, p1[1] + t * dy)
    return _dist(pt, proj)


def _entity_segments(e):
    """实体 → 线段列表 [[(x1,y1),(x2,y2)], ...]"""
    t = e.dxftype()
    try:
        if t == 'LINE':
            return [(_xy(e.dxf.start), _xy(e.dxf.end))]
        if t == 'LWPOLYLINE':
            pts = [_xy(p) for p in e.get_points('xy')]
            return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    except Exception:
        pass
    return []


def _seg_dist_to_entity(pt, segments):
    best = 1e18
    for p1, p2 in segments:
        d = _point_seg_dist(pt, p1, p2)
        if d < best:
            best = d
    return best


def _dim_direction(p1, p2):
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dx > dy * 1.5:
        return 'horizontal'
    if dy > dx * 1.5:
        return 'vertical'
    return 'aligned'


def match_dimensions(msp, tol=MATCH_TOL):
    """主入口: DIMENSION → 构件关联"""
    # 1. 收集候选构件: 直线/多段线 的线段几何
    entities = []  # [{layer, segments, length}]
    for e in msp:
        t = e.dxftype()
        if t not in ('LINE', 'LWPOLYLINE'):
            continue
        segs = _entity_segments(e)
        if not segs:
            continue
        length = sum(_dist(s[0], s[1]) for s in segs)
        if length <= 10:
            continue
        entities.append({'layer': e.dxf.layer, 'segments': segs, 'length': length})

    # 2. 遍历标注, 端点吸附
    matches = []
    for e in msp:
        if e.dxftype() != 'DIMENSION':
            continue
        try:
            p1_raw = e.dxf.defpoint2
            p2_raw = e.dxf.defpoint3
            if p1_raw is None or p2_raw is None:
                continue
            p1 = _xy(p1_raw)
            p2 = _xy(p2_raw)
            meas = float(e.get_measurement() or 0)
            if meas <= 0:
                meas = _dist(p1, p2)
            if meas <= 0:
                continue
            direction = _dim_direction(p1, p2)

            # 端点1/端点2 最近构件
            best1 = best2 = None
            for ent in entities:
                d1 = _seg_dist_to_entity(p1, ent['segments'])
                d2 = _seg_dist_to_entity(p2, ent['segments'])
                if d1 < tol and (best1 is None or d1 < best1[0]):
                    best1 = (d1, ent)
                if d2 < tol and (best2 is None or d2 < best2[0]):
                    best2 = (d2, ent)

            targets = []
            if best1 and best2 and best1[1] is best2[1]:
                targets = [best1[1]]
            else:
                if best1:
                    targets.append(best1[1])
                if best2:
                    targets.append(best2[1])

            for ent in targets:
                matches.append({
                    'layer': ent['layer'],
                    'value': round(meas, 1),
                    'type': direction,
                    'target_len': round(ent['length'], 1),
                    'x': round((p1[0] + p2[0]) / 2, 1),
                    'y': round((p1[1] + p2[1]) / 2, 1),
                })
        except Exception:
            continue
    return matches


def derive_member_sizes(matches):
    """从标注关联推导构件尺寸: {layer: {长_mm, 宽_mm, 高_mm}}"""
    from collections import defaultdict
    by_layer = defaultdict(lambda: {'h': [], 'v': []})
    for m in matches:
        if m['type'] in ('horizontal', 'aligned'):
            by_layer[m['layer']]['h'].append(m['value'])
        elif m['type'] == 'vertical':
            by_layer[m['layer']]['v'].append(m['value'])

    out = {}
    for layer, dims in by_layer.items():
        h = dims['h']
        v = dims['v']
        entry = {}
        if h:
            entry['长_mm'] = round(sum(h) / len(h), 1)
        if v:
            entry['高_mm'] = round(sum(v) / len(v), 1)
        # 柱: 双向标注 → 截面宽×高
        if '柱' in layer or 'COL' in layer.upper() or 'KZ' in layer.upper():
            if h and v:
                entry['宽_mm'] = round(sum(h) / len(h), 1)
                entry['高_mm'] = round(sum(v) / len(v), 1)
        out[layer] = entry
    return out
