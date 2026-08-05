"""市政工程算量 — v5.12 构件化版

v5.12 P1-1 构件化:
- 构件模型优先: 道路面层/道路基层/路基/路缘石/管网 直接消费 component_model._build_civil
- 缺失回退旧字段(构造层/面积区域/线性构件) — 默认值与旧版完全一致, 保证基准单调不降
- 挖深从标高参数/说明提取, 不再固定 1m
- 余方率从构造层/说明提取, 不再固定 0.85
- 面层类(粘层/透层/封层)按 100m² 计, 其余按 m³ = 面积×实测厚度
"""
import re


def _get_depth(data):
    """挖深(m): 优先 标高参数(设计-原地面) > 识图深度字段 > 施工说明正则 > 默认 1.0"""
    elev = data.get('标高参数', {}) or {}
    if elev.get('挖深_m'):
        return abs(float(elev['挖深_m']))
    for d in data.get('面积区域', []):
        if d.get('挖深_m'):
            return float(d['挖深_m'])
    texts = ' '.join(data.get('施工说明', []))
    for pat in [r'挖深[为]?\s*(\d+\.?\d*)\s*m', r'开挖深度[为]?\s*(\d+\.?\d*)\s*m',
                r'深[度]?\s*(\d+\.?\d*)\s*m', r'(\d+\.?\d*)\s*m\s*深']:
        m = re.search(pat, texts)
        if m:
            v = float(m.group(1))
            if 0.1 <= v <= 20:
                return v
    return 1.0


def _get_remain_rate(data):
    """余方率: 从说明提取, 默认 0.85"""
    texts = ' '.join(data.get('施工说明', []))
    m = re.search(r'余方[率]?\s*(\d+\.?\d*)%', texts)
    if m:
        return min(1.0, float(m.group(1)) / 100)
    return 0.85


def _cm_total_area(cm, key):
    return sum((c.get('面积_m2') or 0) for c in (cm.get(key, []) or []))


def _cm_total_len(cm, key):
    return sum((c.get('长度_m') or 0) for c in (cm.get(key, []) or []))


def calc(data):
    r = []
    cm = data.get('构件模型') or {}
    areas = data.get('面积区域', []); total = sum(a.get('面积_m2', 0) or 0 for a in areas)
    layers = data.get('构造层', [])
    ci = data.get('市政信息', {}) or {}
    road_layers = ci.get('路面结构', []) or layers

    depth = _get_depth(data)
    remain = _get_remain_rate(data)

    # ── 路基/土方: 构件模型优先(路基.面积_m2), 回退面积区域 ──
    road_base = cm.get('路基') or []
    total = _cm_total_area(cm, '路基') or total
    if total > 0:
        r.append({'分项名称': '路床整形', '单位': 'm²', '工程量': round(total, 2), '计算式': str(total), '定额编号': ''})
        r.append({'分项名称': '挖一般土方', '单位': 'm³', '工程量': round(total * depth, 2), '计算式': f'{total}×{depth}(实测挖深)', '定额编号': ''})
        r.append({'分项名称': '余方弃置', '单位': 'm³', '工程量': round(total * depth * remain, 2), '计算式': f'{total}×{depth}×{remain}', '定额编号': ''})

    # ── 面层/基层: 构件模型优先(道路面层+道路基层), 回退构造层 ──
    layers = (cm.get('道路面层') or []) + (cm.get('道路基层') or [])
    if not layers:
        layers = road_layers if road_layers else data.get('构造层', [])

    for l in layers:
        if '编号' in l:
            # 构件模型对象: 编号/规格/厚度/面积
            name = l.get('编号', '') or l.get('名称', '')
            thick = (l.get('厚度_mm') or 0) / 1000 if l.get('厚度_mm') else 0
            area = l.get('面积_m2') or total
            mat = l.get('规格', '') or ''
            combined = name + ' ' + mat
            if l.get('类别') == '薄层' or (thick == 0 and re.search(r'粘层|透层|封层|防水层', combined)):
                r.append({'分项名称': name, '单位': '100m²', '工程量': round((area or total) / 100, 2), '计算式': f'{area or total}÷100', '定额编号': ''})
            elif thick > 0 and (area or total) > 0:
                r.append({'分项名称': name, '单位': 'm³', '工程量': round((area or total) * thick, 2), '计算式': f'{area or total}×{thick}(实测厚度)', '定额编号': ''})
            continue
        # 旧构造层 dict: 名称/厚度_mm
        name = l.get('名称', '') or l.get('层位', '')
        thick = (l.get('厚度_mm', 0) or 0) / 1000
        if thick > 0 and total > 0:
            r.append({'分项名称': f'{name}', '单位': 'm³', '工程量': round(total * thick, 2), '计算式': f'{total}×{thick}(实测厚度)', '定额编号': ''})
        elif thick == 0 and re.search(r'粘层|透层|封层|防水层', name):
            r.append({'分项名称': name, '单位': '100m²', '工程量': round(total / 100, 2), '计算式': f'{total}÷100', '定额编号': ''})

    # ── 路缘石: 构件模型优先, 回退线性构件 ──
    curbs = cm.get('路缘石') or []
    if not curbs:
        for item in data.get('线性构件', []):
            if any(k in item.get('名称', '') for k in ['侧石', '缘石', '路缘']):
                curbs.append(item)
    for item in curbs:
        r.append({'分项名称': item.get('编号', '') or item.get('名称', ''), '单位': 'm',
                  '工程量': item.get('长度_m', 0), '计算式': str(item.get('长度_m', 0)), '定额编号': ''})

    # ── 管网(管道/线缆): 构件模型优先, 回退线性构件 ──
    pipes = cm.get('管网') or []
    if not pipes:
        pipes = [i for i in data.get('线性构件', []) if i.get('类型') == '管道']
    for p in pipes:
        nm = p.get('编号', '') or p.get('名称', '')
        ln = p.get('长度_m', 0) or 0
        if ln > 0:
            r.append({'分项名称': f'{nm}', '单位': 'm', '工程量': round(ln, 2),
                      '计算式': str(ln), '定额编号': '', '备注': '构件模型/线性构件'})

    return r
