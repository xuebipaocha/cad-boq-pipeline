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
    (['图例', '符号', '代号'], '图例表'),  # v6.3 B2: 图例表(符号↔构件)
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


def cluster_rows(texts, tol_factor=2.5):
    """按 y 聚类成行 (容差=字高×tol_factor)
    v6.4: 容差 1.5→2.5 — 大字高图纸表格行内各列文字 y 抖动可达 2~3 倍字高,
    1.5 会把同一表格行拆成多行(如 '楼1 | 木质地板 | 分段:办公室' 拆碎)。
    行距(≥3 字高)仍远大于容差, 不会误合并相邻行。
    """
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
            # v6.4: 列容差随字高自适应 — 原写死 40, 大字高图纸(如本图 h=350)
            # 同列文字 x 抖动可达数百~上千(表格绘制不齐), 40 会把表头/数据全丢弃
            tol = max(x_tol, t['h'] * 1.2, 1500)
            if abs(t['x'] - cols[best]['x']) <= tol:
                grid[ri].setdefault(best, []).append(t['text'])
    return grid


def detect_table_type(headers):
    """表头行 → 表格类型"""
    joined = ' '.join(headers)
    # v6.4: 表头词可能带空格(如 '构 造 做 法'), 去空格后再匹配
    joined_nospace = joined.replace(' ', '')
    # v6.5: 门窗表特征列(门窗号/宽/高/数量)优先 — 做法表+门窗表混排时按门窗表处理
    # (房建多视图平面图表头: 做法|厚度|材料|门窗号|宽|高|数量 — 同时含两种特征)
    if any(k in joined_nospace for k in ('门窗号', '门号', '窗号')) and any(k in joined_nospace for k in ('宽', '高', '数量', '洞口')):
        return '门窗表'
    # v6.4: 做法表特判 — '编号'+('名称'/'类别') 组合必为做法表(避免被'设备表'(名称)抢先)
    if '编号' in joined_nospace and any(k in joined_nospace for k in ('名称', '类别', '部位', '位置')):
        if any(k in joined_nospace for k in ('做法', '构造', '层次')):
            return '做法表'
    for kws, ttype in HEADER_PATTERNS:
        if any(k in joined_nospace for k in kws):
            return ttype
    return None


def split_text_columns(texts):
    """v6.4: 多栏排版切分 — 设计说明页常为多栏(目录栏/正文栏/做法表栏/门窗栏)横向排列,
    全局行聚类会把不同栏的 y 相近文字拼成'混合行', 导致表格解析错乱。
    按 x 间隙(离群阈值: 中位间隙×10 且 ≥5000 图纸单位)切分为纵向栏, 每栏独立解析。
    单栏图纸(间隙均匀) → 仍返回整组, 行为不变。
    """
    if len(texts) < 8:
        return [texts]
    xs = sorted(t['x'] for t in texts)
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return [texts]
    gaps.sort()
    med = gaps[len(gaps) // 2]
    # v6.4: 保守阈值(原 max(med*10,5000) 会把列距大的表格(如做法表 编号→做法 列距 8千)误切成多栏)
    threshold = max(med * 10, 20000)
    xs_u = sorted(set(xs))
    bounds = [xs_u[0]]
    for i in range(1, len(xs_u)):
        if xs_u[i] - xs_u[i - 1] > threshold:
            bounds.append((xs_u[i - 1] + xs_u[i]) / 2)
    bounds.append(xs_u[-1])
    cols = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        grp = [t for t in texts if lo <= t['x'] <= hi]
        if len(grp) >= 4:  # 栏内文字过少不成表
            cols.append(grp)
    return cols or [texts]


def parse_tables(msp, unit_scale_mm=1.0):
    """主入口: 解析图纸中的所有文字表格
    v4.1.2: 支持同 y 区域多个表格(做法表+门窗表并存)
    v6.4: 先按 x 栏分区(多栏排版), 每栏独立聚类解析
    """
    texts = collect_texts(msp, unit_scale_mm)
    if len(texts) < 4:
        return []

    tables = []
    for texts_col in split_text_columns(texts):
        tables.extend(_parse_tables_in_column(texts_col))
    return tables


def _parse_tables_in_column(texts):
    """单栏内的表格解析(原 parse_tables 主体, 抽取为独立函数)"""
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
        # v6.4: 剔除长句列(>20字符) — 多栏排版下表头行常混入跨栏正文长句,
        # 这些不是表头字段, 剔除后剩余短词列才是真表头
        cells = {k: v for k, v in cells.items() if len(' '.join(v)) <= 20}
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
            # v6.4: 断点用列距中位数×4(原用众数, 列距分散时众数失真会把表头切断)
            gaps_sorted = sorted(gaps)
            median_gap = gaps_sorted[len(gaps_sorted) // 2]
            break_gap = max(median_gap * 4, 3000)
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
            # v6.4: 表头行必须是真表头(短字段词), 排除图名/目录行(如 '设计说明 材料做法表 | A1')
            if seg_type == '做法表' and not any(
                    any(k in h for k in ('编号', '名称', '类别', '部位', '位置', '层次', '构造', '做法')) for h in seg_headers):
                continue
            # v6.4: 做法表段内剔除左侧杂质列(跨栏短词/段落标题, 如 '框架结构'/'（四）防水、防潮工程'),
            # 保留 编号列 及其左侧1列(类别) 到末尾
            if seg_type == '做法表':
                bi = next((i for i, h in enumerate(seg_headers) if '编号' in h), None)
                if bi is not None and bi > 0:
                    keep_from = max(0, bi - 1)
                    seg = seg[keep_from:]
                    seg_headers = seg_headers[keep_from:]
            seg_x_lo = cols[seg[0]]['x'] - 300
            seg_x_hi = cols[seg[-1]]['x'] + 300
            seg_cols = set(seg)
            seg_cols_sorted = sorted(seg, key=lambda k: cols[k]['x'])
            pos_key = (round(seg_x_lo / 500), seg_type)
            if pos_key in used_header_positions:
                continue
            used_header_positions.add(pos_key)
            data_rows = []
            # v6.4: 数据行 y 范围 — 表格数据在表头下方有限高度内(150 倍表头字高),
            # 防止把表头下方更远处的其他表格(如门窗表)文字收进来(x 列重叠时必混)
            hdr_h = max((t['h'] for t in rows[ri]['items']), default=2.5)
            span_limit = max(hdr_h * 150, 30000)
            empty_run = 0
            for rj in range(ri + 1, len(rows)):
                if rows[ri]['y'] - rows[rj]['y'] > span_limit:
                    continue  # 距表头过远(其他表格区域)
                cells_j = grid.get(rj, {})
                if not cells_j:
                    # v6.4: 原逻辑遇空行即 break — 全局聚类下表格行之间常有'空行'
                    # (该 y 处无本表文字但被其他区域文字占位), 会过早截断数据行
                    empty_run += 1
                    if empty_run >= 5:
                        break
                    continue
                empty_run = 0
                in_range = any(seg_x_lo <= t['x'] <= seg_x_hi for t in rows[rj]['items'])
                if not in_range:
                    continue
                # v6.4: 表头/数据列 x 存在系统性漂移(梯形表格: 表头'名称'列 x=2091505,
                # 数据'木质地板'列 x=2090770), 严格 k in seg_cols 匹配会丢数据。
                # 数据行列数 ≥ 表头列数一半 → 按行内 x 顺序与表头列一一对齐;
                # 否则(行缺列多)退化为最近表头列匹配。
                row_cols = sorted((k for k in cells_j
                                   if seg_x_lo <= cols[k]['x'] <= seg_x_hi),
                                  key=lambda k: cols[k]['x'])
                if len(row_cols) >= max(len(seg_cols_sorted) // 2, 2):
                    # v6.4: 编号列锚定 — 做法表数据行可能缺'类别'列(如 楼2 行只有5列),
                    # 顺序对齐会整体左移错位; 用编号样式(楼1/墙1/棚1)锚定后再对齐
                    seg_cells = {}
                    if seg_type == '做法表':
                        hbi = next((i for i, h in enumerate(seg_headers) if '编号' in h), None)
                        bi_data = next((i for i, k in enumerate(row_cols)
                                        if re.fullmatch(r'[\u4e00-\u9fa5]{1,3}\d{1,2}',
                                                        ' '.join(cells_j[k])[:8].strip())), None)
                        if hbi is not None and bi_data is not None:
                            offset = hbi - bi_data
                            for i, sk in enumerate(seg_cols_sorted):
                                j = i - offset
                                if 0 <= j < len(row_cols):
                                    seg_cells[sk] = cells_j[row_cols[j]]
                    if not seg_cells:
                        for i, sk in enumerate(seg_cols_sorted):
                            if i < len(row_cols):
                                seg_cells[sk] = cells_j[row_cols[i]]
                else:
                    seg_cells = {sk: cells_j[row_cols[0]] for sk in seg_cols_sorted
                                 if row_cols and abs(cols[row_cols[0]]['x'] - cols[sk]['x']) <= 2500}
                if not seg_cells:
                    continue
                # v6.4: 做法表数据行须有有效编号(楼1/墙1/棚1 样式), 碎片行(如 '面'/'* 1.5厚...')不收
                if seg_type == '做法表':
                    hbi = next((i for i, h in enumerate(seg_headers) if '编号' in h), None)
                    if hbi is not None and hbi < len(seg_cols_sorted):
                        id_key = seg_cols_sorted[hbi]
                        id_val = ' '.join(seg_cells.get(id_key, []) or []).strip()
                        if not re.fullmatch(r'[\u4e00-\u9fa5]{1,3}\d{1,2}', id_val):
                            continue
                data_rows.append({
                    'y': rows[rj]['y'],
                    # v6.4: 严格按表头列序补全(缺列填空) — 保证 cells 与 headers 长度/位置对齐,
                    # table_to_layers 等下游按列索引取值(缺列行会整体左移错位)
                    'cells': [' '.join(seg_cells.get(k, [])) for k in seg_cols_sorted],
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
