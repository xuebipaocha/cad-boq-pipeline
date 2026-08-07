# -*- coding: utf-8 -*-
"""房间分区几何化 — v6.1

把图纸闭合区域识别为房间(按图层名/内部文字标签), 输出房间面积/周长,
与做法表"部位"匹配 → 生成实际分区量(地面=房间面积, 墙面=周长×层高, 天棚=面积)。

用法:
  python3 room_geometry.py 图纸.dxf                # 检测房间
  from room_geometry import detect_rooms, match_rooms_to_layers
"""
import os
import sys
import re

sys.stdout.reconfigure(encoding='utf-8')

# 房间图层/标签关键词
ROOM_KEYWORDS = ['客厅', '卧室', '卫生间', '厨房', '餐厅', '书房', '会议室', '办公室',
                 '阳台', '走廊', '过道', '门厅', '储藏', '衣帽间', '主卧', '次卧', '儿童房',
                 '浴室', '洗手间', '休息室', '接待室', '机房', '库房', '卫生间', '淋浴间']
# 排除非房间层
EXCLUDE_LAYERS = ['图框', '0', '墙体', '墙', '轴线', '标注', '尺寸', '门窗', '柱', '梁']


def _poly_area(points):
    """鞋带公式: 平面面积(m², 输入 mm)。"""
    n = len(points)
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _poly_perim(points):
    """周长(mm)。"""
    n = len(points)
    p = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        p += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return p


def detect_rooms(dxf_path, scale=1.0):
    """检测图纸闭合区域 → 房间候选列表。

    返回 [{房间名, 面积_m2, 周长_m, 来源(图层/标签), 置信度}]。
    - 图层名含房间关键词 → 房间(高置信)
    - 闭合区域内文字含房间关键词 → 房间(中置信, 按面积匹配文字位置)
    """
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    rooms = []
    seen_names = {}

    # 1. 闭合多段线按图层名识别
    for e in msp:
        if e.dxftype() != 'LWPOLYLINE' or not e.closed:
            continue
        layer = e.dxf.layer or ''
        if any(k in layer for k in EXCLUDE_LAYERS):
            continue
        pts = [(p[0], p[1]) for p in e.get_points()]
        if len(pts) < 4:
            continue
        area_m2 = _poly_area(pts) / 1e6 * (scale ** 2)
        if area_m2 < 1.0:  # 过滤过小(可能是符号)
            continue
        perim_m = _poly_perim(pts) / 1000 * scale
        name = None
        for kw in ROOM_KEYWORDS:
            if kw in layer:
                name = kw
                break
        if name:
            # 同层多个闭合取最大(最外层房间轮廓)
            if name in seen_names:
                if area_m2 > seen_names[name]['面积_m2']:
                    seen_names[name] = {'面积_m2': round(area_m2, 2), '周长_m': round(perim_m, 2)}
            else:
                seen_names[name] = {'面积_m2': round(area_m2, 2), '周长_m': round(perim_m, 2)}

    # 2. 文字标签兜底: 闭合区域内文字含房间关键词(区域=闭合多段线 bbox)
    texts = [(e.dxf.text, (e.dxf.insert.x, e.dxf.insert.y)) for e in msp if e.dxftype() == 'TEXT']
    for e in msp:
        if e.dxftype() != 'LWPOLYLINE' or not e.closed:
            continue
        layer = e.dxf.layer or ''
        if any(k in layer for k in EXCLUDE_LAYERS):
            continue
        pts = [(p[0], p[1]) for p in e.get_points()]
        if len(pts) < 4:
            continue
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        area_m2 = _poly_area(pts) / 1e6 * (scale ** 2)
        if area_m2 < 1.0:
            continue
        # 区域内文字
        inside = [t for t, (tx, ty) in texts if x0 <= tx <= x1 and y0 <= ty <= y1]
        name = None
        for t in inside:
            for kw in ROOM_KEYWORDS:
                if kw in t:
                    name = kw
                    break
            if name:
                break
        if name and name not in seen_names:
            perim_m = _poly_perim(pts) / 1000 * scale
            seen_names[name] = {'面积_m2': round(area_m2, 2), '周长_m': round(perim_m, 2)}

    for name, info in seen_names.items():
        rooms.append({'房间名': name, '面积_m2': info['面积_m2'], '周长_m': info['周长_m'],
                      '来源': '图层' if any(k in name for k in ROOM_KEYWORDS) else '标签',
                      '置信度': 0.9})
    return rooms


def match_rooms_to_layers(rooms, construction_layers):
    """房间 ↔ 做法表构造层 匹配: 构造层带'部位'列(房间名)时, 分配实际面积。

    返回 [{部位, 材料, 做法, 面积_m2, 周长_m, 匹配房间}]。
    """
    out = []
    for l in construction_layers:
        room = (l.get('部位') or '').strip()
        if not room:
            continue
        # 部位可能含多个房间(如 '客厅/卧室' 或 '卫生间、厨房')
        room_names = re.split(r'[/、,，和及与]', room)
        matched = []
        total_area = 0.0
        for rn in room_names:
            rn = rn.strip()
            if not rn:
                continue
            for r in rooms:
                if r['房间名'] in rn or rn in r['房间名']:
                    matched.append(r)
                    total_area += r['面积_m2']
                    break
        if matched:
            out.append({
                '部位': room, '材料': l.get('材料', ''), '做法': l.get('名称', ''),
                '面积_m2': round(total_area, 2),
                '周长_m': round(sum(r['周长_m'] for r in matched), 2),
                '匹配房间': [r['房间名'] for r in matched],
            })
    return out


def axis_zone_partition(axis_map, zone_ranges, poly_points, scale=1.0):
    """v6.5: 轴线分区量取 — 长廊式大进深厂房按轴线分区计算面积/周长/进深。

    axis_map: {轴线号(str): 坐标值(float)} — 轴线坐标字典(如 '1':0, '8':42000, 'A':0, 'G':18000)
    zone_ranges: [{'分区': '128轴分段部', '轴1': '1', '轴2': '8', '轴A': 'A', '轴B': 'G'}, ...]
      — 分区 range 串: 轴号区间(横向 1-8 / 纵向 A-G)
    poly_points: 房间多边形顶点 [(x, y), ...] (CAD 坐标系)
    scale: 图纸比例(默认 1.0)

    返回: [{'分区': str, '面积_m2': float, '周长_m': float, '进深_m': float, '轴跨': str}]
    """
    zones = []
    for zr in zone_ranges or []:
        # 从 axis_map 取坐标(轴号可能有 ①② 或 1-8 两种写法), mm→m
        x1 = axis_map.get(str(zr.get('轴1', '')), None)
        x2 = axis_map.get(str(zr.get('轴2', '')), None)
        y1 = axis_map.get(str(zr.get('轴A', '')), None)
        y2 = axis_map.get(str(zr.get('轴B', '')), None)
        # 兼容: 轴A/轴B 为 None 时用横向轴跨的 1/3 估进深
        if x1 is None or x2 is None:
            continue
        width = abs(float(x2) - float(x1)) * scale / 1000.0
        # 进深: 有纵向轴则用纵向跨度, 否则从多边形包围盒估
        if y1 is not None and y2 is not None:
            depth = abs(float(y2) - float(y1)) * scale / 1000.0
        else:
            if not poly_points:
                continue
            ys = [p[1] for p in poly_points]
            depth = (max(ys) - min(ys)) * scale / 1000.0
        area = round(width * depth, 2)
        perim = round(2 * (width + depth), 2)
        zones.append({
            '分区': zr.get('分区', f'{zr.get("轴1","")}-{zr.get("轴2","")}轴'),
            '面积_m2': area,
            '周长_m': perim,
            '进深_m': round(depth, 2),
            '轴跨': f"{zr.get('轴1','')}-{zr.get('轴2','')}",
        })
    return zones


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python3 room_geometry.py 图纸.dxf')
        sys.exit(1)
    rooms = detect_rooms(sys.argv[1])
    print(f'检测到 {len(rooms)} 个房间:')
    for r in rooms:
        print(f"  {r['房间名']}: {r['面积_m2']}m² 周长{r['周长_m']}m")
