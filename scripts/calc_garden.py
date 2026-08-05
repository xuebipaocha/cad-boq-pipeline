"""园林绿化工程算量 — v5.12 构件化版

v5.12 P1-2 构件化:
- 构件模型优先: 乔木/灌木/草坪/种植土/硬景 直接消费 component_model._build_garden
- 缺失回退旧字段(图块明细/园林信息/面积区域) — 默认值与旧版完全一致
- 种植土深度从构件(构造层提取), 不再固定 0.5m
- 草坪系数从说明提取, 默认 0.6
"""
import re


def _count_from_blocks(data):
    """旧路径回退: 图块明细(苗木类) > CAD分析.blocks > 建筑信息.图块 > 文字'株'数"""
    best = 0
    shrub = 0
    bd = data.get('图块明细', {}) or {}
    for item in bd.get('苗木', []) or []:
        name = item.get('name', '')
        cnt = item.get('count', 0)
        if any(k in name for k in ['灌木', '灌木丛', 'shrub', 'SHRUB', '绿篱', '地被', 'BOX']):
            shrub = max(shrub, cnt)
        else:
            best += cnt
    for path in [('CAD分析', 'blocks'), ('建筑信息', '图块')]:
        obj = data
        try:
            for p in path:
                obj = obj.get(p, {})
            tb = obj.get('tree_blocks', {}) if isinstance(obj, dict) else {}
            if isinstance(tb, dict):
                for name, cnt in tb.items():
                    if any(k in name for k in ['灌木', '灌木丛', 'shrub', 'SHRUB', '绿篱', '地被']):
                        shrub = max(shrub, cnt)
                    else:
                        best = max(best, cnt)
        except Exception:
            pass
    text_n = 0
    for t in data.get('施工说明', []):
        ms = re.findall(r'(\d+)\s*株', t)
        if ms:
            text_n = max(text_n, int(ms[0]))
    best = max(best, text_n)
    return best, shrub


def calc(data):
    r = []
    cm = data.get('构件模型') or {}
    areas = data.get('面积区域', []); total = sum(a.get('面积_m2', 0) or 0 for a in areas)
    gi = data.get('园林信息', {}) or {}

    # ── 乔木/灌木: 构件模型优先, 回退园林信息/图块 ──
    def _cm_count(key):
        items = cm.get(key) or []
        return sum(i.get('数量', 0) or 0 for i in items)

    tree_count = _cm_count('乔木')
    shrubs = _cm_count('灌木')
    if not (tree_count or shrubs):
        trees_old = gi.get('苗木', {}).get('乔木', []) or []
        shrubs_old = gi.get('苗木', {}).get('灌木', []) or []
        tree_count = sum(t.get('数量', 0) for t in trees_old if isinstance(t, dict))
        shrubs = sum(t.get('数量', 0) for t in shrubs_old if isinstance(t, dict))
        block_trees, block_shrubs = _count_from_blocks(data)
        tree_count = max(tree_count, block_trees)
        shrubs = max(shrubs, block_shrubs)

    # ── 面积: 构件模型优先(草坪/种植土), 回退面积区域 ──
    lawns = cm.get('草坪') or []
    soil = cm.get('种植土') or []
    if lawns:
        total = sum(l.get('面积_m2', 0) or 0 for l in lawns)
    if not total:
        total = sum(a.get('面积_m2', 0) or 0 for a in areas)

    # ── 硬景: 构件模型优先, 回退园林信息 ──
    hard_items = cm.get('硬景') or []
    hard = gi.get('硬景', {}) or {}
    pave_area = hard.get('铺装_m2', 0) or 0
    curb_len = hard.get('路缘石_m', 0) or 0
    for h in hard_items:
        if h.get('编号') == '硬景铺装':
            pave_area = max(pave_area, h.get('面积_m2', 0) or 0)
        elif h.get('编号') == '路缘石':
            curb_len = max(curb_len, h.get('长度_m', 0) or 0)

    # ── 种植土深度: 构件(构造层提取)优先, 回退构造层/说明 ──
    depth = 0.5
    if soil and soil[0].get('深度_m'):
        depth = soil[0]['深度_m']
    else:
        for l in data.get('构造层', []):
            if '种植土' in (l.get('名称', '') or ''):
                t = l.get('厚度_mm')
                if t:
                    depth = t / 1000
                    break
        for t in data.get('施工说明', []):
            m = re.search(r'种植土[^0-9]{0,6}(\d+\.?\d*)\s*(?:cm|厘米)', t)
            if m:
                depth = float(m.group(1)) / 100
                break

    if total > 0:
        r.append({'分项名称': '绿地平整', '单位': 'm²', '工程量': round(total, 2), '计算式': f'{total}', '定额编号': '', '备注': 'CAD实测面积'})
        r.append({'分项名称': '种植土回填', '单位': 'm³', '工程量': round(total * depth, 2), '计算式': f'{total}×{depth}m(实测深度)', '定额编号': '', '备注': ''})

    if tree_count > 0:
        r.append({'分项名称': '乔木栽植', '单位': '株', '工程量': tree_count, '计算式': f'CAD识别{tree_count}株', '定额编号': '', '备注': 'CAD图块识别'})
    else:
        r.append({'分项名称': '乔木栽植', '单位': '株', '工程量': 0, '计算式': '待CAD图块/人工输入', '定额编号': ''})

    if shrubs > 0:
        r.append({'分项名称': '灌木栽植', '单位': '株', '工程量': shrubs, '计算式': f'CAD识别{shrubs}株', '定额编号': '', '备注': 'CAD图块识别'})
    else:
        r.append({'分项名称': '灌木栽植', '单位': '株', '工程量': 0, '计算式': '待CAD图块识别', '定额编号': ''})

    if pave_area > 0:
        r.append({'分项名称': '硬景铺装', '单位': 'm²', '工程量': round(pave_area, 2), '计算式': f'{pave_area}', '定额编号': '', '备注': 'CAD实测'})
    if curb_len > 0:
        r.append({'分项名称': '路缘石安砌', '单位': 'm', '工程量': round(curb_len, 2), '计算式': f'{curb_len}', '定额编号': '', '备注': 'CAD实测'})

    # 草坪系数: 说明提取或默认 0.6
    grass_coef = 0.6
    for t in data.get('施工说明', []):
        m = re.search(r'草坪[^0-9]{0,4}(\d+\.?\d*)%', t)
        if m:
            grass_coef = min(1.0, float(m.group(1)) / 100)
            break
    r.append({'分项名称': '草坪铺设', '单位': 'm²', '工程量': round(total * grass_coef, 2) if total else 0, '计算式': f'{total}×{grass_coef}', '定额编号': ''})

    return r
