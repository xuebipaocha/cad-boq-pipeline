# -*- coding: utf-8 -*-
"""DXF 扩展实体解析 — v6.10 图纸理解突破

补齐主流水线此前**零处理**的实体类型(实测确认全项目无引用):
  MLINE          多线墙   → 墙中心线/墙厚/墙长(建筑图常用画法, 此前完全丢失)
  SPLINE         样条曲线 → 曲线长度/闭合围合面积(园林、道路)
  HATCH          填充     → 填充边界面积(防水/保温/地面范围线索; 此前仅旧分析脚本用)
  LEADER / MULTILEADER    → 引线标注关联(引线端点 + 附近文字 → 做法说明挂到部位)
  ACAD_TABLE     真表格   → 表格行列与文字(门窗表/做法表若为真表格实体, 此前解析失效)
  ACAD_PROXY_ENTITY       → 天正等专业对象检测(几何语义丢失, 只能提示导出普通实体)

设计原则:
- 只读、容错: 每个实体 try/except 包裹, 取不到就如实返回空(不编造)
- 不覆盖既有几何: 本模块产出独立字段, 由调用方决定如何与既有结果互证
- 单位: 坐标按图纸单位(通常 mm), 面积 m² / 长度 m 由调用方按 $INSUNITS 换算

用法:
  from dxf_entities import extract_all
  data = extract_all(msp)   # {mline: [...], spline: [...], hatch: [...], ...}
"""
import math
import sys

sys.stdout.reconfigure(encoding='utf-8')


def _poly_area(pts):
    """鞋带公式: 闭合多边形的面积(绝对值, 图纸单位²)。"""
    if len(pts) < 3:
        return 0.0
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _poly_len(pts, closed=False):
    """折线长度。"""
    if len(pts) < 2:
        return 0.0
    tot = 0.0
    for i in range(len(pts) - 1):
        tot += math.dist(pts[i][:2], pts[i + 1][:2])
    if closed:
        tot += math.dist(pts[-1][:2], pts[0][:2])
    return tot


def _mline_vertex_xy(v):
    """取 MLINE 顶点坐标 — ezdxf 1.4 的 MLineVertex.location 是顶点位置(Vec3)。"""
    loc = getattr(v, 'location', None)
    if loc is not None:
        try:
            return (loc.x, loc.y)
        except AttributeError:
            pass
    try:
        return (v[0], v[1])
    except Exception:
        return None


def _mline_vertex_lines(v):
    """取 MLINE 顶点上各平行线的端点(厚度估计用)。

    ezdxf 1.4: MLineVertex.line_params → LineParam 序列, 含 start/end 点。
    """
    pts = []
    for lp in (getattr(v, 'line_params', None) or []):
        p = getattr(lp, 'start', None) or getattr(lp, 'end', None)
        if p is not None:
            try:
                pts.append((p.x, p.y))
                continue
            except AttributeError:
                pass
        try:
            pts.append((lp[0][0], lp[0][1]))
        except Exception:
            continue
    return pts


def _mline_style_width(doc, style_name, scale=1.0):
    """从多线样式表算墙厚 = (最大偏移 − 最小偏移) × scale。

    v6.10 实测: ezdxf 的 MLineVertex.line_params 在常见导出下取不到点坐标(元素为裸 tuple),
    而多线样式(MLINESTYLE.elements)是墙厚的**权威定义** → 作为主算法, 顶点几何作回退。
    """
    if doc is None or not style_name:
        return 0.0
    try:
        st = doc.mline_styles.get(style_name)
    except Exception:
        return 0.0
    if st is None:
        return 0.0
    offsets = []
    for el in (getattr(st, 'elements', None) or []):
        try:
            offsets.append(float(el[0] if isinstance(el, (list, tuple)) else el))
        except Exception:
            continue
    if len(offsets) < 2:
        return 0.0
    return abs(max(offsets) - min(offsets)) * abs(scale or 1.0)


def extract_mline(msp):
    """MLINE 多线墙 → [{layer, center, width, length, closed, style}]

    - 中心线/长度: 顶点位置(MLineVertex.location)
    - 墙厚: 多线样式偏移差 × scale(权威) > 顶点 line_params 几何(回退) > 0(如实)
    """
    doc = getattr(msp, 'doc', None)
    out = []
    for e in msp:
        if e.dxftype() != 'MLINE':
            continue
        try:
            verts = list(e.vertices)
            centers, widths = [], []
            for v in verts:
                xy = _mline_vertex_xy(v)
                if xy:
                    centers.append(xy)
                lpts = _mline_vertex_lines(v)
                if len(lpts) >= 2:
                    widths.append(math.dist(lpts[0], lpts[-1]))
            if len(centers) < 2:
                continue
            style = str(getattr(e.dxf, 'style_name', '') or '')
            scale = float(getattr(e.dxf, 'scale_factor', 1.0) or 1.0)
            if widths:
                width = sum(widths) / len(widths)
                width_src = '顶点几何'
            else:
                width = _mline_style_width(doc, style, scale)
                width_src = '多线样式' if width else '未取到'
            closed = bool(getattr(e.dxf, 'flags', 0) & 1)
            out.append({
                'layer': e.dxf.layer or '',
                'center': [(round(x, 1), round(y, 1)) for x, y in centers],
                'width': round(width, 1),
                'width_src': width_src,
                'length': round(_poly_len(centers, closed=closed), 1),
                'closed': closed,
                'style': style,
                'scale': scale,
            })
        except Exception:
            continue
    return out


def extract_spline(msp, flatten_dist=20.0):
    """SPLINE 样条曲线 → [{layer, points, length, closed, area}]

    flatten_dist 为曲线离散化精度(图纸单位, 默认 20 = 2cm@mm 制), 越小越精确越慢。
    """
    out = []
    for e in msp:
        if e.dxftype() != 'SPLINE':
            continue
        try:
            pts = [(p[0], p[1]) for p in e.flattening(flatten_dist)]
            if len(pts) < 2:
                continue
            closed = bool(getattr(e.dxf, 'flags', 0) & 1)
            out.append({
                'layer': e.dxf.layer or '',
                'points': [(round(x, 1), round(y, 1)) for x, y in pts],
                'length': round(_poly_len(pts, closed=closed), 1),
                'area': round(_poly_area(pts), 1) if closed else 0.0,
                'closed': closed,
            })
        except Exception:
            continue
    return out


def extract_hatch(msp):
    """HATCH 填充 → [{layer, pattern, area, path_count, bbox}]

    面积 = 外环面积 − 内环(孤岛)面积; 用边界路径离散点计算。
    填充是"区域线索"(防水/保温/地面范围), 与闭合多段线互证。
    """
    import ezdxf.path as ezpath
    out = []
    for e in msp:
        if e.dxftype() != 'HATCH':
            continue
        try:
            areas, boxes = [], []
            for p in e.paths:
                try:
                    pts = [(v[0], v[1]) for v in ezpath.from_hatch_boundary_path(p).flattening(20.0)]
                except Exception:
                    pts = []
                if len(pts) < 3:
                    continue
                pts.append(pts[0])
                areas.append(_poly_area(pts))
                boxes.append((min(x for x, _ in pts), min(y for _, y in pts),
                              max(x for x, _ in pts), max(y for _, y in pts)))
            if not areas:
                continue
            total = max(areas) - sum(a for a in areas if a < max(areas))
            bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes))
            out.append({
                'layer': e.dxf.layer or '',
                'pattern': str(getattr(e.dxf, 'pattern_name', '') or ''),
                'solid': bool(getattr(e.dxf, 'solid_fill', 0)),
                'path_count': len(getattr(e, 'paths', []) or []),
                'area': round(total, 1),
                'bbox': [round(v, 1) for v in bbox],
            })
        except Exception:
            continue
    return out


def collect_closed_polys(msp):
    """图纸内闭合多段线 → [(area, bbox)]，用于与填充区域互证。"""
    out = []
    for e in msp:
        if e.dxftype() != 'LWPOLYLINE':
            continue
        try:
            if not e.closed:
                continue
            pts = [(p[0], p[1]) for p in e.get_points('xy')]
            if len(pts) < 3:
                continue
            out.append({
                'layer': e.dxf.layer or '',
                'area': round(_poly_area(pts), 1),
                'bbox': [round(min(p[0] for p in pts), 1), round(min(p[1] for p in pts), 1),
                         round(max(p[0] for p in pts), 1), round(max(p[1] for p in pts), 1)],
            })
        except Exception:
            continue
    return out


def _bbox_overlap_ratio(b1, b2):
    """两 bbox 的重叠比例(相对较小者面积) → 判断"同区域"。"""
    x0 = max(b1[0], b2[0]); y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2]); y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    a1 = max((b1[2] - b1[0]) * (b1[3] - b1[1]), 1e-6)
    a2 = max((b2[2] - b2[0]) * (b2[3] - b2[1]), 1e-6)
    return inter / min(a1, a2)


def cross_check_hatch_polys(msp, hatches, tol=0.05):
    """HATCH 填充面积 ↔ 同区域闭合多段线面积 互证(v6.10)。

    填充是"区域线索"(防水/保温/地面范围), 与闭合多段线是两条独立证据:
    一致 → 相互印证(可提高可信度); 偏差 > tol → 标"待核"(不覆盖任何一方)。
    """
    polys = collect_closed_polys(msp)
    checks = []
    for i, h in enumerate(hatches):
        hb = h.get('bbox')
        if not hb:
            continue
        best, best_r = None, 0.0
        for p in polys:
            r = _bbox_overlap_ratio(hb, p['bbox'])
            if r > best_r:
                best, best_r = p, r
        if best is None or best_r < 0.6:
            checks.append({'填充序号': i, '互证': '无同区域闭合多段线', '偏差': None})
            continue
        ha, pa = h.get('area', 0.0), best['area']
        dev = abs(ha - pa) / pa if pa else None
        checks.append({
            '填充序号': i,
            '图层': h.get('layer', ''),
            '填充面积': ha,
            '多段线面积': pa,
            '偏差': round(dev, 4) if dev is not None else None,
            '互证': ('一致' if (dev is not None and dev <= tol) else '待核'),
        })
    return checks


def extract_leaders(msp, tol=3000.0):
    """LEADER / MULTILEADER 引线标注 → [{layer, type, vertices, tip, nearby_text}]

    tip = 引线末端(箭头指向的几何位置); nearby_text = 引线末端附近最近的 TEXT/MTEXT。
    v6.10 实测: 容差 800mm 会漏配(合成图引线末端与文字相距 2343mm) → 默认放宽到 3000mm,
    真实图纸引线-文字距离通常在数百毫米量级, 3000 兼顾漏配与误配。
    """
    texts = []
    try:
        for e in msp:
            t = e.dxftype()
            if t == 'TEXT':
                texts.append((e.dxf.insert.x, e.dxf.insert.y, e.dxf.text))
            elif t == 'MTEXT':
                texts.append((e.dxf.insert.x, e.dxf.insert.y, e.text))
    except Exception:
        texts = []

    def _near(x, y, tol=tol):
        best, bd = '', None
        for tx, ty, tt in texts:
            d = math.dist((x, y), (tx, ty))
            if bd is None or d < bd:
                best, bd = tt, d
        return best if (bd is not None and bd <= tol) else ''

    out = []
    for e in msp:
        t = e.dxftype()
        if t not in ('LEADER', 'MULTILEADER'):
            continue
        try:
            pts = []
            if t == 'LEADER':
                pts = [(v[0], v[1]) for v in (e.vertices or [])]
            else:
                ctx = getattr(e, 'context', None)
                for attr in ('mtext', 'block'):
                    obj = getattr(ctx, attr, None) if ctx else None
                    if obj is not None and hasattr(obj, 'dxf') and hasattr(obj.dxf, 'insert'):
                        pts.append((obj.dxf.insert.x, obj.dxf.insert.y))
                try:
                    pts = [(v[0], v[1]) for v in e.get_mleader_geometry()] if hasattr(e, 'get_mleader_geometry') else pts
                except Exception:
                    pass
            if len(pts) < 2:
                continue
            tip = pts[-1]
            out.append({
                'layer': e.dxf.layer or '',
                'type': t,
                'vertices': [(round(x, 1), round(y, 1)) for x, y in pts],
                'tip': [round(tip[0], 1), round(tip[1], 1)],
                'nearby_text': _near(tip[0], tip[1]),
            })
        except Exception:
            continue
    return out


def _group_rows(texts, y_tol=200.0):
    """把 (x, y, text) 按 y 聚类成行(容差 y_tol), 行内按 x 排序 → 二维文本网格。"""
    if not texts:
        return []
    items = sorted(texts, key=lambda t: -t[1])
    rows, cur, cur_y = [], [], None
    for x, y, t in items:
        if cur_y is None or abs(y - cur_y) <= y_tol:
            cur.append((x, t))
            cur_y = y if cur_y is None else cur_y
        else:
            rows.append([t2 for _, t2 in sorted(cur, key=lambda p: p[0])])
            cur, cur_y = [(x, t)], y
    if cur:
        rows.append([t2 for _, t2 in sorted(cur, key=lambda p: p[0])])
    return rows


def extract_tables(msp):
    """真表格 → 原生 ACAD_TABLE(若 ezdxf 支持) + 匿名表块(*T 前缀 INSERT + 块内 TEXT 网格)。

    实测发现: ezdxf 1.4.4 **不支持 ACAD_TABLE** 实体类(entities.Table 不存在, 无法创建/读取)。
    而真实图纸里 ACAD_TABLE 导出 DXF 后即"匿名块(如 *T1) + 块内文字网格"形态 ——
    本项目按该结构解析, 既覆盖真实图纸, 也不受 ezdxf 版本限制。
    """
    out = []
    # ① 原生 TABLE / ACAD_TABLE(ezdxf 版本支持时)
    for e in msp:
        if e.dxftype() not in ('TABLE', 'ACAD_TABLE'):
            continue
        try:
            rows = int(getattr(e.dxf, 'n_rows', 0) or 0)
            cols = int(getattr(e.dxf, 'n_cols', 0) or 0)
            ins = getattr(e.dxf, 'insert', None)
            cells = []
            for r in range(rows):
                row = []
                for c in range(cols):
                    try:
                        row.append(str(getattr(e.cell(r, c), 'text', '') or ''))
                    except Exception:
                        row.append('')
                if any(row):
                    cells.append(row)
            out.append({'layer': e.dxf.layer or '', 'kind': 'ACAD_TABLE',
                        'rows': rows, 'cols': cols,
                        'insert': [round(ins.x, 1), round(ins.y, 1)] if ins else None,
                        'cells': cells, 'cell_text_available': bool(cells)})
        except Exception:
            continue

    # ② 匿名表块(*T 前缀 INSERT + 块内 TEXT/MTEXT)
    doc = getattr(msp, 'doc', None)
    for e in msp:
        if e.dxftype() != 'INSERT':
            continue
        try:
            name = str(getattr(e.dxf, 'name', '') or '')
            if not name.upper().startswith('*T'):
                continue
            block = doc.blocks.get(name) if doc is not None else None
            if block is None:
                continue
            texts = []
            for sub in block:
                t = sub.dxftype()
                if t in ('TEXT', 'MTEXT'):
                    ins = sub.dxf.insert
                    val = sub.dxf.text if t == 'TEXT' else sub.text
                    if val:
                        texts.append((ins.x, ins.y, str(val)))
            if not texts:
                continue
            rows = _group_rows(texts)
            out.append({
                'layer': e.dxf.layer or '', 'kind': '匿名表块', 'name': name,
                'rows': len(rows),
                'cols': max((len(r) for r in rows), default=0),
                'cells': rows, 'cell_text_available': True,
                'insert': [round(e.dxf.insert.x, 1), round(e.dxf.insert.y, 1)],
            })
        except Exception:
            continue
    return out


def extract_proxy(msp):
    """ACAD_PROXY_ENTITY 检测(天正等专业对象) → 统计 + 图层分布 + 类名

    天正墙/门窗/房间等自定义实体导出 DXF 后成为 proxy 实体, 几何语义丢失。
    检测到即提示"建议导出 T3/普通实体格式", 这是诚实的能力边界提示。
    """
    n = 0
    layers = {}
    names = {}
    for e in msp:
        try:
            if e.dxftype() != 'ACAD_PROXY_ENTITY':
                continue
            n += 1
            lay = e.dxf.layer or ''
            layers[lay] = layers.get(lay, 0) + 1
            cn = str(getattr(e.dxf, 'class_name', '') or getattr(e, 'dxf', None) and
                     getattr(e.dxf, 'class_name', '') or '')
            if cn:
                names[cn] = names.get(cn, 0) + 1
        except Exception:
            continue
    return {'count': n, 'layers': dict(sorted(layers.items(), key=lambda kv: -kv[1])),
            'classes': dict(sorted(names.items(), key=lambda kv: -kv[1]))}


def extract_all(msp, flatten_dist=20.0):
    """一次解析全部扩展实体 → dict。各类型独立容错(失败返回空列表, 不影响其他)。"""
    result = {'mline': [], 'spline': [], 'hatch': [], 'leader': [], 'table': [], 'proxy': {}}
    for key, fn in (('mline', extract_mline), ('spline', extract_spline),
                    ('hatch', extract_hatch), ('leader', extract_leaders),
                    ('table', extract_tables)):
        try:
            result[key] = fn(msp) if key != 'spline' else fn(msp, flatten_dist)
        except Exception as ex:
            result[f'{key}_error'] = str(ex)[:200]
    try:
        result['proxy'] = extract_proxy(msp)
    except Exception as ex:
        result['proxy_error'] = str(ex)[:200]
    # v6.10: 填充区域 ↔ 闭合多段线面积互证(两条独立证据, 一致则印证, 偏差大标待核)
    try:
        result['hatch_check'] = cross_check_hatch_polys(msp, result['hatch'])
    except Exception as ex:
        result['hatch_check'] = []
        result['hatch_check_error'] = str(ex)[:200]
    result['summary'] = {
        '多线墙': len(result['mline']),
        '样条曲线': len(result['spline']),
        '填充区域': len(result['hatch']),
        '填充互证一致': sum(1 for c in result['hatch_check'] if c.get('互证') == '一致'),
        '引线标注': len(result['leader']),
        '真表格': len(result['table']),
        '天正对象': (result['proxy'] or {}).get('count', 0),
    }
    return result


if __name__ == '__main__':
    import json
    import ezdxf
    doc = ezdxf.readfile(sys.argv[1])
    data = extract_all(doc.modelspace())
    print(json.dumps({'summary': data['summary'],
                      'mline': data['mline'][:3], 'spline': data['spline'][:2],
                      'hatch': data['hatch'][:3], 'leader': data['leader'][:3],
                      'table': data['table'][:2], 'proxy': data['proxy']},
                     ensure_ascii=False, indent=2))
