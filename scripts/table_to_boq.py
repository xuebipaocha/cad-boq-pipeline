# -*- coding: utf-8 -*-
"""表格→清单联动 — v4.0 第二批

门窗表/桩表/构件表 → 自动生成算量分项:
- 门窗表: 门窗号/洞口宽/洞口高/数量 → 门窗樘数 + 洞口面积
- 桩表: 桩号/桩径/桩长/数量 → 桩根数 + 桩体积
- 做法表 → 构造层分项已在 table_parser 处理
"""
import re


def parse_window_table(table):
    """门窗表 → [{门窗号, 宽_mm, 高_mm, 数量, 面积_m2}]"""
    if not table or table.get('type') != '门窗表':
        return []
    headers = table['headers']
    # 列索引: 门窗号/宽/高/数量
    def _col(kws):
        for i, h in enumerate(headers):
            if any(k in h for k in kws):
                return i
        return None
    idx_name = _col(['门窗号', '门号', '窗号', '编号', 'TYPE'])
    idx_w = _col(['宽', 'WIDTH', 'W'])
    idx_h = _col(['高', 'HEIGHT', 'H'])
    idx_qty = _col(['数量', 'QTY', '数'])

    windows = []
    for row in table['rows']:
        cells = row['cells']
        if idx_name is None or idx_name >= len(cells):
            continue
        name = cells[idx_name].strip()
        if not re.match(r'^[A-Za-z]?\d+', name):
            continue
        try:
            w = float(cells[idx_w]) if idx_w is not None and idx_w < len(cells) else 0
            h = float(cells[idx_h]) if idx_h is not None and idx_h < len(cells) else 0
            qty = int(float(cells[idx_qty])) if idx_qty is not None and idx_qty < len(cells) else 1
        except (ValueError, IndexError):
            continue
        if w <= 0 or h <= 0 or qty <= 0:
            continue
        windows.append({
            '门窗号': name,
            '宽_mm': w,
            '高_mm': h,
            '数量': qty,
            '面积_m2': round(w * h / 1e6, 2),
        })
    return windows


def parse_pile_table(table):
    """桩表 → [{桩号, 桩径_mm, 桩长_m, 数量}]"""
    if not table or table.get('type') != '桩表':
        return []
    headers = table['headers']
    def _col(kws):
        for i, h in enumerate(headers):
            if any(k in h for k in kws):
                return i
        return None
    idx_name = _col(['桩号', '编号', 'NAME'])
    idx_d = _col(['桩径', '直径', 'DIA'])
    idx_l = _col(['桩长', '长度', 'LEN'])
    idx_qty = _col(['数量', '根数', 'QTY'])

    piles = []
    for row in table['rows']:
        cells = row['cells']
        if idx_name is None or idx_name >= len(cells):
            continue
        try:
            d = float(cells[idx_d]) if idx_d is not None and idx_d < len(cells) else 0
            l = float(cells[idx_l]) if idx_l is not None and idx_l < len(cells) else 0
            qty = int(float(cells[idx_qty])) if idx_qty is not None and idx_qty < len(cells) else 1
        except (ValueError, IndexError):
            continue
        if d <= 0 or l <= 0:
            continue
        piles.append({
            '桩号': cells[idx_name].strip(),
            '桩径_mm': d,
            '桩长_m': l,
            '数量': qty,
        })
    return piles


def table_to_boq_items(tables):
    """表格 → 算量分项列表"""
    items = []
    for t in tables:
        # 门窗表
        windows = parse_window_table(t)
        if windows:
            total_qty = sum(w['数量'] for w in windows)
            total_area = sum(w['面积_m2'] * w['数量'] for w in windows)
            items.append({
                '分项名称': '门窗安装(门窗表)',
                '单位': '樘',
                '工程量': total_qty,
                '计算式': f'门窗表合计{total_qty}樘',
                '定额编号': '',
                '备注': '门窗表',
                '明细': windows,
            })
            items.append({
                '分项名称': '门窗洞口面积(门窗表)',
                '单位': 'm²',
                '工程量': round(total_area, 2),
                '计算式': f'门窗表洞口面积合计{total_area:.2f}m²',
                '定额编号': '',
                '备注': '门窗表',
            })
        # 桩表
        piles = parse_pile_table(t)
        if piles:
            total = sum(p['数量'] for p in piles)
            items.append({
                '分项名称': '桩基(桩表)',
                '单位': '根',
                '工程量': total,
                '计算式': f'桩表合计{total}根',
                '定额编号': '',
                '备注': '桩表',
                '明细': piles,
            })
    return items
