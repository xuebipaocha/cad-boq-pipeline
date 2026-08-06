# -*- coding: utf-8 -*-
"""局部小说明提取与部位关联 — v6.3 A1+A2

每张图纸上除了设计说明正文，还有大量"局部小说明"(角落注释/局部做法/节点要求)，
如: "种植土回填 60cm" / "电缆桥架300×150 热镀锌" / "花岗岩铺装 广场"。
这些注释藏着局部做法与规格，是算量与组价的重要依据。

功能:
1. 提取局部注释块(短句独立文字, 非表格/非长文说明)
2. 部位关联: 注释位置 → 最近闭合区域(房间/地块) → 绑定部位
3. 结构化输出: {注释, 类型, 部位, 关联区域面积}

用法:
  from local_notes import extract_local_notes
  notes = extract_local_notes(dxf_path, rooms)
"""
import re
import os

# 局部注释内容特征(与设计说明/表格区分)
NOTE_FEATURES = [
    (r'(?:[\u4e00-\u9fa5A-Za-z]{2,}|[\d]{2,})[\d×xX*]', '规格'),      # 含规格: 桥架200×100 / 300×300 / PPR-DN50
    (r'(?:厚度|厚)[为:：]?\s*[\d.]+', '厚度'),                        # 厚度50cm
    (r'(?:面积|占比|比例)[为:：]?\s*[\d.]+', '面积/占比'),             # 面积70%
    (r'^\d+\.\s*[\u4e00-\u9fa5]', '做法层次'),                        # 1.界面剂 2.自流平...
    (r'(?:铺装|铺设|栽植|回填|安砌|安装|敷设|拆除)', '做法'),           # 做法动词
    (r'(?:不锈钢|花岗岩|热镀锌|镀锌|混凝土|石材|木材|抛釉|釉面|实木)', '材质'),  # 材质
]
# 排除(表格/图名/断面号等)
EXCLUDE_PAT = [r'^[A-Za-z]-\s*[A-Za-z]$', r'^B-B$', r'^\d+$', r'^[\u4e00-\u9fa5]{2,6}$',
               r'图名|比例|图号|设计|审核|校对|日期|阶段']
# 做法动词(部位关联用)
ACTION_KW = ['回填', '铺装', '铺设', '栽植', '安砌', '安装', '敷设', '拆除', '浇筑', '砌筑']


def _point_in_poly(pt, poly):
    """射线法: 点是否在多边形内。"""
    x, y = pt
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def _nearest_room(pt, rooms):
    """最近区域: 点在区域内 → 直接命中; 否则最近距离。"""
    x, y = pt
    best, best_d = None, 1e18
    for r in rooms:
        cx, cy = r.get('中心_x', r.get('cx', 0)), r.get('中心_y', r.get('cy', 0))
        d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
        if d < best_d:
            best_d = d
            best = r
    return best


def extract_local_notes(dxf_path, rooms=None, scale=1.0):
    """提取局部小说明并关联部位。

    dxf_path: 图纸路径
    rooms: 区域列表 [{房间名, 面积_m2, 周长_m, 中心_x, 中心_y}]
    返回: [{注释, 类型, 部位, 关联面积_m2, x, y}]
    """
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # 收集闭合区域(房间候选, 含中心坐标)
    polys = []
    if rooms is None:
        rooms = []
    for e in msp:
        if e.dxftype() != 'LWPOLYLINE' or not e.closed:
            continue
        pts = [(p[0], p[1]) for p in e.get_points()]
        if len(pts) < 4:
            continue
        polys.append(pts)
        # 中心坐标
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        # 面积(鞋带)
        area = abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                       for i in range(len(pts)))) / 2.0 / 1e6
        if area < 1.0:
            continue
        # 区域名: 图层名(若含语义) 或 '区域-N'; 排除图框/超大区域(>500m²)
        layer = e.dxf.layer or ''
        if any(k in layer for k in ('图框', '0', '标注', '轴线')):
            continue
        if area > 500:
            continue
        rname = layer if layer else f'区域{len(rooms) + 1}'
        rooms.append({'房间名': rname, '面积_m2': round(area, 2), '周长_m': 0,
                      '中心_x': cx, '中心_y': cy})

    notes = []
    for e in msp:
        if e.dxftype() not in ('TEXT', 'MTEXT'):
            continue
        try:
            if e.dxftype() == 'TEXT':
                txt = e.dxf.text or ''
                pos = (e.dxf.insert.x, e.dxf.insert.y)
            else:
                txt = e.text or ''
                pos = (e.dxf.insert.x, e.dxf.insert.y)
        except Exception:
            continue
        txt = (txt or '').strip()
        # 长度过滤: 局部注释通常 4~60 字(短于说明, 长于图名)
        if not (4 <= len(txt) <= 60):
            continue
        if any(re.search(p, txt) for p in EXCLUDE_PAT):
            continue
        # 特征判定: 含规格/厚度/面积/做法/材质 之一
        ntype = None
        for pat, tname in NOTE_FEATURES:
            if re.search(pat, txt):
                ntype = tname
                break
        if not ntype:
            continue
        # 部位关联: 注释位置 → 最近房间(或点在闭合区域内)
        room_name, room_area = '', 0.0
        best_room, best_d = None, 1e18
        for r in rooms:
            cx, cy = r.get('中心_x', 0), r.get('中心_y', 0)
            d = ((cx - pos[0]) ** 2 + (cy - pos[1]) ** 2) ** 0.5
            if d < best_d:
                best_d, best_room = d, r
        if best_room:
            room_name = best_room.get('房间名', '')
            room_area = best_room.get('面积_m2', 0)
        notes.append({
            '注释': txt, '类型': ntype,
            '部位': room_name, '关联面积_m2': room_area,
            'x': round(pos[0], 1), 'y': round(pos[1], 1),
        })
    # 去重
    seen, out = set(), []
    for n in notes:
        k = (n['注释'], n['部位'])
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 2:
        print('用法: python local_notes.py 图纸.dxf')
        sys.exit(1)
    notes = extract_local_notes(sys.argv[1])
    print(f'局部小说明 {len(notes)} 条:')
    for n in notes:
        print(f"  [{n['类型']}] {n['注释'][:40]} @ 部位:{n['部位'] or '无'}")
