"""面积/单位基准统一模块 — v4.0

统一全链路面积裁决逻辑：
- 面积来源优先级: 闭合多段线(排除图框/标题栏/指北针等非实体层) > 文字面积标注 > 其他
- 单位换算感知 $INSUNITS: 毫米制图 mm²→m² (÷1e6)；米制图直接 m²；英寸/英尺换算
- 多区域支持: 输出面积列表与总面积
"""
import re

# 应排除在面积统计之外的图层（图框/标题栏/辅助层）
EXCLUDE_LAYER_KW = ['图框', '标题', 'PUB_TITLE', '指北针', '北针', '图例', 'LEGEND',
                    'A-STAIR', '标注', 'DIM', '轴号', 'AXIS', '说明', 'TEXT', '文字', '注释',
                    'text']  # v5.10: 小写 'text' 层(真实图纸图签外框层, 9.8×39.5m 非建筑轮廓)

# $INSUNITS 值: 0无 1英寸 2英尺 4毫米 5厘米 6米
def unit_scale(insunits):
    """返回 (到毫米的倍数, 单位名)"""
    return {1: (25.4, '英寸'), 2: (304.8, '英尺'), 4: (1.0, '毫米'),
            5: (10.0, '厘米'), 6: (1000.0, '米')}.get(insunits, (1.0, '毫米'))


def is_excluded_layer(layer_name):
    return any(k in (layer_name or '') for k in EXCLUDE_LAYER_KW)


def is_frame_poly(area_m2, layer_name, poly_areas):
    """判断是否为图框轮廓:
    - 图层含图框/标题关键词
    - 0 层 → 一律视为图框(v5.3: 工程制图实体轮廓不会画在 0 层,
      0 层只承载图框/目录框/辅助线; 真实图纸 0 层目录框 623.7 vs 实体 593
      仅 1.05 倍, 房建基准图 0 层图框 651 vs 板 360 为 1.81 倍 —
      阈值法无法同时覆盖, 0 层全排除最干净)
    """
    if is_excluded_layer(layer_name):
        return True
    if (layer_name or '') == '0':
        return True
    return False


def shoelace_area(points):
    """鞋带公式面积(图纸单位²)"""
    if len(points) < 3:
        return 0.0
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def to_m2(area_dwg, insunits=4):
    """图纸单位² → m²"""
    mm_per_unit = unit_scale(insunits)[0]
    return area_dwg * (mm_per_unit ** 2) / 1e6


def length_to_m(length_dwg, insunits=4):
    """图纸单位 → m"""
    mm_per_unit = unit_scale(insunits)[0]
    return length_dwg * mm_per_unit / 1000.0


def resolve_area(total_area_m2, closed_polys, texts, insunits=4, layer_filter=True):
    """统一面积裁决:
    - closed_polys: [{area_m2, layer, perimeter_m}] 已换算好的
    - texts: 图纸文字列表
    - 返回 (area_m2, source, notes)
    """
    notes = []
    if layer_filter:
        polys = [p for p in closed_polys if not is_excluded_layer(p.get('layer', ''))]
    else:
        polys = closed_polys

    poly_area = max((p.get('area_m2', 0) for p in polys), default=0)
    text_area = 0.0
    for t in texts:
        # 优先匹配明确的面积标注 (m2/平方米), 排除长度/厚度
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:m\s*[2²]|㎡|平方米)', t, re.I)
        if m:
            v = float(m.group(1))
            if 1 <= v <= 2e7:  # 1 m² ~ 2千万 m² 合理范围
                text_area = max(text_area, v)
        # v5.3: 无²后缀的"面积"语境 — 真实图纸常写 '建筑面积 XXXm'
        # '总维修面积约 176m' (m 后无²但明确是面积)
        else:
            m2 = re.search(r'(?:面积|建筑面积|维修面积)[^0-9]{0,6}(\d+(?:\.\d+)?)\s*m\b', t)
            if m2:
                v = float(m2.group(1))
                if 1 <= v <= 2e7:
                    text_area = max(text_area, v)

    area, source = 0.0, ''
    if poly_area > 0:
        area = poly_area
        source = '闭合多段线'
    elif text_area > 0:
        area = text_area
        source = '文字面积标注'
    if poly_area and text_area:
        ratio = text_area / poly_area
        if not (0.75 <= ratio <= 1.25):
            notes.append(f'文字面积({text_area:.0f}m²)与多段线面积({poly_area:.0f}m²)偏差{(abs(ratio)-1)*100:.0f}%')
    return area, source, notes


def collect_closed_polys(msp, insunits=4, min_area_m2=1.0, near_close_tol=0.01):
    """统一闭合多段线扫描(v5.13 工作流 P0: 合并 analyze_cad 主流程/验证流程/step1 的重复扫描)。
    返回 [{layer, area_m2, perimeter_m, vertices, insunits}], 已换算 m²。
    - 显式闭合 LWPOLYLINE 或 近闭合(首尾间隙 < tol×周长, 工程图差 1mm 常见)
    - 面积 < min_area_m2 过滤(默认 1m²)
    与 analyze_cad.analyze 的 closed_polylines 输出完全一致, 供所有调用方复用。
    """
    from units import shoelace_area, to_m2, length_to_m  # noqa: 自引用保证接口稳定
    out = []
    if msp is None:
        return out
    try:
        for e in msp.query('LWPOLYLINE'):
            pts = list(e.get_points('xy'))
            if len(pts) < 3:
                continue
            is_closed = bool(e.closed)
            if not is_closed:
                first, last = pts[0], pts[-1]
                gap = ((first[0] - last[0]) ** 2 + (first[1] - last[1]) ** 2) ** 0.5
                perim_est = sum(((pts[(j + 1) % len(pts)][0] - pts[j][0]) ** 2 +
                                 (pts[(j + 1) % len(pts)][1] - pts[j][1]) ** 2) ** 0.5
                                for j in range(len(pts) - 1))
                if perim_est > 0 and gap / perim_est < near_close_tol:
                    is_closed = True
                    pts = pts[:-1] if gap < 10 else pts
            if not is_closed:
                continue
            area_m2 = to_m2(shoelace_area(pts), insunits)
            if area_m2 < min_area_m2:
                continue
            n = len(pts)
            perim = sum(((pts[(j + 1) % n][0] - pts[j][0]) ** 2 +
                         (pts[(j + 1) % n][1] - pts[j][1]) ** 2) ** 0.5 for j in range(n))
            out.append({
                'layer': e.dxf.layer,
                'area_m2': round(area_m2, 1),
                'perimeter_m': round(length_to_m(perim, insunits), 1),
                'vertices': n,
                'insunits': insunits,
            })
    except Exception:
        pass
    return out
