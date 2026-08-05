# -*- coding: utf-8 -*-
"""表格文字结构化解析器 — v4.0 第一批

从 DXF 图纸中解析文字表格（做法表/门窗表/桩表/混凝土强度表等）:
1. 收集 TEXT/MTEXT 的坐标(图纸单位)与内容
2. 行聚类 (y 容差=字高×1.2) + 列聚类 (x 容差=字高×1.5)
3. 表头关键词识别 → 表格类型判定
4. 输出结构化表格: {table_type, headers, rows, bbox}
5. 构造层生成: 做法表 → 构造层列表(名称/厚度/材料)

应用:
- 做法表 → 构造层 (消除厚度缺失)
- 门窗表 → 门窗清单
- 桩表 → 桩基参数
"""
import re

# 表头关键词 → 表格类型
HEADER_PATTERNS = [
    (['做法', '层次', '结构层'], '做法表'),
    (['门窗', '门号', '窗号', '洞口'], '门窗表'),
    (['桩号', '桩径', '桩长', '桩顶'], '桩表'),
    (['强度等级', '混凝土强度', '砼强度'], '混凝土强度表'),
    (['构件', '规格', '型号', '数量'], '构件表'),
    (['钢筋', '直径', '间距'], '钢筋表'),
    (['设备', '名称', '功率'], '设备表'),
    (['材料', '品种', '用量'], '材料表'),
]

# 做法表常见列名(用于厚度/材料提取)
THICKNESS_HEADERS = ['厚度', '厚']
MATERIAL_HEADERS = ['材料', '材质', '用料']
NAME_HEADERS = ['名称', '做法', '层次', '构造层', '部位', '项目']


def collect_texts(msp, unit_scale_mm=1.0):
    """收集 TEXT/MTEXT: (x, y, text, height_dwg)"""
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
                texts.append({'x': float(ins.x), 'y': float(ins.y),
                              'text': txt.strip(), 'h': float(h)})
        except Exception:
            continue
    return texts


def cluster_rows(texts, tol_factor=1.5):
    """按 y 聚类成行 (容差=字高×tol_factor)"""
    items = sorted(texts, key=lambda t: -t['y'])  # 从上到下
    rows = []
    for t in items:
        placed = False
        for r in rows:
            if abs(t['y'] - r['y']) <= t['h'] * tol_factor:
                r['items'].append(t)
                r['y'] = (r['y'] * (len(r['items']) - 1) + t['y']) / len(r['items'])
                placed = True
                break
        if not placed:
            rows.append({'y': t['y'], 'items': [t]})
    # 行内按 x 排序
    for r in rows:
        r['items'].sort(key=lambda t: t['x'])
    return rows


def cluster_columns(texts, tol_factor=2.0):
    """对所有文字做 x 聚类 (全局列对齐)"""
    xs = sorted(set(round(t['x'] / 5) * 5 for t in texts))
    cols = []
    for x in xs:
        placed = False
        for c in cols:
            if abs(x - c['x']) <= c.get('h', 2.5) * tol_factor:
                placed = True
                break
        if not placed:
            cols.append({'x': x, 'h': max((t['h'] for t in texts if abs(t['x'] - x) <= 10), default=2.5)})
    cols.sort(key=lambda c: c['x'])
    return cols


def build_grid(rows, cols, x_tol=1.0):
    """把行×列映射成网格 {row_i: {col_i: [texts]}}"""
    grid = {}
    for ri, r in enumerate(rows):
        grid[ri] = {}
        for t in r['items']:
            # 找最近的列
            best = min(range(len(cols)), key=lambda ci: abs(t['x'] - cols[ci]['x']))
            if abs(t['x'] - cols[best]['x']) <= 40:
                grid[ri].setdefault(best, []).append(t['text'])
    return grid


def detect_table_type(headers):
    """表头行 → 表格类型"""
    joined = ' '.join(headers)
    for kws, ttype in HEADER_PATTERNS:
        if any(k in joined for k in kws):
            return ttype
    return None


def parse_tables(msp, unit_scale_mm=1.0):
    """主入口: 解析图纸中的所有文字表格
    v4.1.2: 支持同 y 区域多个表格(做法表+门窗表并存)
    """
    texts = collect_texts(msp, unit_scale_mm)
    if len(texts) < 4:
        return []

    rows = cluster_rows(texts)
    if len(rows) < 2:
        return []
    cols = cluster_columns([t for r in rows for t in r['items']])
    grid = build_grid(rows, cols)

    tables = []
    used_header_positions = set()
    # 找表头行: 行内含 ≥2 个不同列的文字, 且匹配表头关键词
    for ri in range(min(len(rows), len(grid))):
        cells = grid.get(ri, {})
        if len(cells) < 2:
            continue
        headers = [' '.join(v) for k, v in sorted(cells.items())]
        ttype = detect_table_type(headers)
        if not ttype:
            continue
        # v4.1.2: 同一行可能有多个表头(做法表+门窗表并存), 按表头 x 间隙分段
        # v4.1.4: 分段阈值自适应 — 表内列间距的众数 ×3 为断点
        header_keys = sorted(cells.keys())
        header_xs = [cols[k]['x'] for k in header_keys]
        gaps = [header_xs[i] - header_xs[i-1] for i in range(1, len(header_xs)) if header_xs[i] - header_xs[i-1] > 0]
        if gaps:
            from collections import Counter
            common_gap = Counter(round(g / 500) * 500 for g in gaps).most_common(1)[0][0] or 1000
            break_gap = max(common_gap * 3, 3000)
        else:
            break_gap = 3000
        segments = []
        cur = [header_keys[0]]
        for i in range(1, len(header_keys)):
            if header_xs[i] - header_xs[i-1] > break_gap:
                segments.append(cur)
                cur = [header_keys[i]]
            else:
                cur.append(header_keys[i])
        segments.append(cur)
        for seg in segments:
            if len(seg) < 2:
                continue  # v4.1.3: 单列不成表
            seg_headers = [' '.join(cells[k]) for k in seg]
            seg_type = detect_table_type(seg_headers)
            if not seg_type:
                continue
            seg_x_lo = cols[seg[0]]['x'] - 300
            seg_x_hi = cols[seg[-1]]['x'] + 300
            seg_cols = set(seg)
            pos_key = (round(seg_x_lo / 500), seg_type)
            if pos_key in used_header_positions:
                continue
            used_header_positions.add(pos_key)
            data_rows = []
            for rj in range(ri + 1, len(rows)):
                cells_j = grid.get(rj, {})
                if not cells_j:
                    if data_rows:
                        break
                    continue
                in_range = any(seg_x_lo <= t['x'] <= seg_x_hi for t in rows[rj]['items'])
                if not in_range:
                    continue
                # v4.1.3: 只取本段列范围的数据(排除其他表/标高的列)
                seg_cells = {k: v for k, v in cells_j.items() if k in seg_cols or
                             (k not in seg_cols and cols[k]['x'] <= seg_x_hi and cols[k]['x'] >= seg_x_lo)}
                if not seg_cells:
                    continue
                data_rows.append({
                    'y': rows[rj]['y'],
                    'cells': [' '.join(v) for k, v in sorted(seg_cells.items())],
                    'grid': seg_cells,
                })
            if not data_rows:
                continue
            tables.append({
                'type': seg_type,
                'headers': seg_headers,
                'header_row': ri,
                'rows': data_rows,
                'y_range': (rows[ri]['y'], rows[max(ri, len(rows) - 1)]['y']),
                'x_range': (seg_x_lo, seg_x_hi),
            })
    return tables


def table_to_layers(table):
    """做法表 → 构造层列表 [{名称, 厚度_mm, 材料}]"""
    if not table or table.get('type') != '做法表':
        return []
    headers = table['headers']
    # 列索引: 名称列/厚度列/材料列 (名称列优先匹配不含'材料'的列)
    name_idx = next((i for i, h in enumerate(headers) if any(k in h for k in NAME_HEADERS)), 0)
    thick_idx = next((i for i, h in enumerate(headers) if any(k in h for k in THICKNESS_HEADERS)), None)
    mat_idx = next((i for i, h in enumerate(headers) if any(k in h for k in MATERIAL_HEADERS)), None)
    # v5.15 精装房间分区: 房间/部位列(做法表 '房间' 列头)
    room_idx = next((i for i, h in enumerate(headers)
                     if any(k in h for k in ('房间', '部位', '区域', '位置'))), None)
    # 名称列若与材料列同列(表头同时含'名称'与'材料'), 优先用更具体的
    if mat_idx is not None and name_idx == mat_idx:
        # '材料' 列更具体: 名称列取别的
        for i, h in enumerate(headers):
            if i != mat_idx and any(k in h for k in NAME_HEADERS):
                name_idx = i
                break
    layers = []
    for row in table['rows']:
        cells = row['cells']
        if name_idx >= len(cells):
            continue
        name = cells[name_idx]
        if not name or len(name) < 2:
            continue
        # v4.3: 行有效性过滤 — 排除面积标注/平法标注/门窗号等非构造层行
        if re.search(r'面积\s*\d+', name) or re.search(r'[A-Z]{1,3}\d+\s*\d+[×xX]\d+', name) \
           or re.match(r'^[A-Za-z]{1,3}\d{3,4}$', name) or any(c in name for c in '▲▼'):
            continue
        thick = None
        if thick_idx is not None and thick_idx < len(cells):
            m = re.search(r'(\d+(?:\.\d+)?)', cells[thick_idx])
            if m:
                v = float(m.group(1))
                # 单位判断: 若单元格含 cm/厘米 → ×10; 默认 mm
                if re.search(r'cm|厘米', cells[thick_idx]):
                    v *= 10
                thick = round(v, 1)
        mat = ''
        if mat_idx is not None and mat_idx < len(cells):
            mcell = cells[mat_idx]
            # v4.1.1: 材料列容错 — 排除纯数字/标高值(如 '52.30'), 空则取名称中的材料词
            if not re.fullmatch(r'[+-]?\d+\.?\d*', mcell) and not any(c in mcell for c in '▼▲▽△'):
                mat = mcell
            if not mat:
                from step1_materials import extract_material_name
                mat = extract_material_name(name)
        room = ''
        if room_idx is not None and room_idx < len(cells):
            rc = cells[room_idx]
            if rc and not re.fullmatch(r'[+-]?\d+\.?\d*', rc):
                room = rc.strip()
        layers.append({'名称': name, '厚度_mm': thick, '材料': mat, '部位': room,
                       '厚度来源': '做法表提取' if thick else '未识别'})
    return layers
