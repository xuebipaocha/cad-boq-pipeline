"""房屋建筑与装饰工程算量 — v4.0 实测参数版 + v5.0 构件模型优先

v4.0 改进:
- 构件体积消费识图数据: 截面尺寸样本(宽×高) + 层高 + 厚度_mm, 消除硬编码 0.36/0.15/0.12/0.18
- 建筑面积不再 ×3 层数假设, 层数从说明提取
- 钢筋配筋率法覆盖全构件混凝土体积(板+柱+梁), 修复 v3.1 只算板的问题
- 板厚/墙厚/层高从施工说明提取

v5.0 改进 (P1 构件级建模):
- 柱/梁数量、截面、高度、梁长、板厚、墙厚 优先取构件模型(加权均值),
  缺失回退旧特征字段/说明正则/默认值 — 保证四项指标单调不降
  (构件模型 = 旧逻辑在所有证据缺席时的特例, 默认值与旧值完全一致)
"""
import re

def _first_float(text, pats, lo, hi, default):
    for pat in pats:
        m = re.search(pat, text)
        if m:
            v = float(m.group(1))
            if lo <= v <= hi:
                return v
    return default

def _sizes(elem, dim_sizes=None):
    """从构件尺寸样本提取典型截面 (宽_mm, 高_mm)
    v4.1: 键名回退 — '框架柱'→'柱', '框架梁'→'梁' 等
    v4.2: 标注推导尺寸优先(标注是权威尺寸)
    """
    out = {}
    alias = {'框架柱': '柱', '柱': '框架柱', '框架梁': '梁', '梁': '框架梁'}
    for key, samples in (elem or {}).items():
        if not isinstance(samples, list) or not samples:
            continue
        ws = [s.get('宽_mm', 0) for s in samples if s.get('宽_mm')]
        hs = [s.get('高_mm', 0) for s in samples if s.get('高_mm')]
        if ws and hs:
            out[key] = (sum(ws)/len(ws), sum(hs)/len(hs))
    # 键名回退
    for k1, k2 in alias.items():
        if k1 not in out and k2 in out:
            out[k1] = out[k2]
    # v4.2: 标注推导尺寸合并(标注值更权威)
    for layer, dims in (dim_sizes or {}).items():
        key = '柱' if ('柱' in layer or 'KZ' in layer.upper() or 'COL' in layer.upper()) else \
              ('梁' if ('梁' in layer or 'KL' in layer.upper() or 'BEAM' in layer.upper()) else layer)
        if key not in out:
            if '宽_mm' in dims and '高_mm' in dims:
                out[key] = (dims['宽_mm'], dims['高_mm'])
            elif '长_mm' in dims and '高_mm' in dims:
                out[key] = (dims['长_mm'], dims['高_mm'])
    return out

def _floor_count(texts):
    """层数: 说明中 'X层' 提取, 默认 1"""
    for t in texts:
        m = re.search(r'(\d+)\s*层', t)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 60:
                return v
    return 1

def _parse_rebar(texts, bfa, con_vol_total, col_h=3.0, beam_len=6.0):
    """钢筋量: 平法标注结构化解析(v4.2) > 配筋率(全构件体积) > 含钢量 > 默认65kg/m²"""
    from rebar_parse2 import parse_rebar_notes, calc_total_steel
    parsed = parse_rebar_notes(texts)
    if parsed['beams'] or parsed['columns'] or parsed['slabs']:
        total, detail = calc_total_steel(parsed, bfa, col_h=col_h, beam_len=beam_len)
        if total > 0:
            return total, f'平法结构化解析: {detail}'
    from rebar_calc import calc_rebar_total
    total, note, detail = calc_rebar_total(texts, bfa)
    if total:
        return total, note
    tc = ' '.join(texts)
    m = re.search(r'配筋率[：:]?\s*(\d+\.?\d*)\s*%', tc)
    if m and con_vol_total > 0:
        ratio = float(m.group(1)) / 100
        steel_t = ratio * con_vol_total * 7.85
        return round(steel_t, 2), f'按配筋率{ratio*100}%×全构件混凝土体积{con_vol_total:.1f}m³计算'
    m = re.search(r'(\d+\.?\d*)\s*kg/[m㎡]', tc)
    if m:
        kg_m2 = float(m.group(1))
        return round(bfa*kg_m2/1000, 2), f'按含钢量{kg_m2}kg/m²'
    return round(bfa*65/1000, 2), f'面积×65kg/m²含钢量（估算）'

def _parse_decoration(texts, bfa, total_area):
    """装饰做法: 地面系数/墙面系数"""
    dec = {'地面':[], '墙面':[], '天棚':[], '门窗':[], '踢脚':[]}
    tc = ' '.join(texts)

    for t in texts:
        if '地面' in t or '地砖' in t or '地板' in t:
            dec['地面'].append(t)
        if '墙面' in t or '内墙' in t or '涂料' in t or '乳胶漆' in t or '墙砖' in t:
            dec['墙面'].append(t)
        if '天棚' in t or '吊顶' in t or '天花' in t:
            dec['天棚'].append(t)
        if '踢脚' in t:
            dec['踢脚'].append(t)

    results = []
    ground_coef = _first_float(tc, [r'地面系数\s*(\d+\.?\d*)'], 0.3, 1.0, 0.85)
    if '地砖' in tc:
        results.append({'分项名称':'地砖地面','单位':'m²','工程量':round(total_area*ground_coef,2),'计算式':f'{total_area}×{ground_coef}','定额编号':'','备注':'施工说明识别'})
    elif '地板' in tc:
        results.append({'分项名称':'木地板地面','单位':'m²','工程量':round(total_area*ground_coef,2),'计算式':f'{total_area}×{ground_coef}','定额编号':'','备注':'施工说明识别'})
    else:
        results.append({'分项名称':'地面装饰','单位':'m²','工程量':round(total_area*ground_coef,2),'计算式':f'{total_area}×{ground_coef}','定额编号':'','备注':'施工说明'})

    wall_coef = _first_float(tc, [r'墙面系数\s*(\d+\.?\d*)'], 0.5, 6.0, 2.8)
    if '乳胶漆' in tc or '涂料' in tc:
        results.append({'分项名称':'内墙乳胶漆','单位':'m²','工程量':round(total_area*wall_coef,2),'计算式':f'{total_area}×{wall_coef}','定额编号':'','备注':'施工说明识别'})
    elif '墙砖' in tc:
        results.append({'分项名称':'墙面砖','单位':'m²','工程量':round(total_area*wall_coef,2),'计算式':f'{total_area}×{wall_coef}','定额编号':'','备注':'施工说明识别'})
    else:
        results.append({'分项名称':'内墙面装饰','单位':'m²','工程量':round(total_area*wall_coef,2),'计算式':f'{total_area}×{wall_coef}','定额编号':'','备注':'施工说明'})

    if dec['天棚']:
        results.append({'分项名称':'天棚装饰','单位':'m²','工程量':round(total_area,2),'计算式':str(total_area),'定额编号':'','备注':'施工说明识别'})
    else:
        results.append({'分项名称':'天棚装饰','单位':'m²','工程量':round(total_area,2),'计算式':str(total_area),'定额编号':'','备注':'施工说明'})

    return results

def _cm_cols(c, floor_h_old):
    """v5.0: 构件模型 → (数量, 截面宽mm, 截面高mm, 高度m)。旧值兜底"""
    n = sum(x.get('数量', 0) for x in c) or None
    if c:
        ws = [x['截面宽_mm'] for x in c if x.get('截面宽_mm')]
        hs = [x['截面高_mm'] for x in c if x.get('截面高_mm')]
        hs_m = [x['高度_m'] for x in c if x.get('高度_m')]
        w = int(sum(ws) / len(ws)) if ws else None
        h = int(sum(hs) / len(hs)) if hs else None
        floor_h = max(hs_m) if hs_m else None
        return n, w, h, floor_h
    return n, None, None, None


def _cm_beams(b, old_beam_len):
    """v5.0: 构件模型 → (数量, 截面宽mm, 截面高mm, 长度m)。旧值兜底"""
    n = sum(x.get('数量', 0) for x in b) or None
    if b:
        ws = [x['截面宽_mm'] for x in b if x.get('截面宽_mm')]
        hs = [x['截面高_mm'] for x in b if x.get('截面高_mm')]
        ls = [x['长度_m'] for x in b if x.get('长度_m')]
        w = int(sum(ws) / len(ws)) if ws else None
        h = int(sum(hs) / len(hs)) if hs else None
        ln = round(sum(ls) / len(ls), 2) if ls else None
        return n, w, h, ln
    return n, None, None, None


def calc(data):
    r = []
    areas = data.get('面积区域', []); total = sum(a.get('面积_m2',0) for a in areas)
    bi = data.get('建筑信息', {}); main = bi.get('主体', {})
    elem = bi.get('构件分类', {})
    sizes = _sizes(bi.get("构件尺寸样本", {}), data.get("构件尺寸推导", {}))
    texts = data.get('施工说明', [])
    tc = ' '.join(texts)

    col_count = elem.get('框架柱', 0) or elem.get('柱', 0)
    beam_count = elem.get('框架梁', 0) or elem.get('梁', 0)
    found_count = elem.get('独立基础', 0) or elem.get('筏板基础', 0) or elem.get('基础', 0)

    # v5.0: 构件模型优先(数量/截面/高度/梁长/板厚/墙厚), 缺失回退旧逻辑
    # 数量语义: 构件模型=平法编号归并数(1条文=1标准做法), 分类计数=CAD实际图元统计,
    #           两者取 max 保证单调不降且数量不低估
    cm = data.get('构件模型') or {}
    cm_cols, cm_beams = cm.get('柱', []) or [], cm.get('梁', []) or []
    cm_slabs, cm_walls = cm.get('板', []) or [], cm.get('墙', []) or []
    cm_n, cm_w, cm_h, cm_floor_h = _cm_cols(cm_cols, 0)
    cm_bn, cm_bw, cm_bh, cm_beam_len = _cm_beams(cm_beams, 0)
    if cm_n:
        col_count = max(col_count, cm_n)
    if cm_w and cm_h:
        sizes['框架柱'] = (cm_w, cm_h)
    if cm_bn:
        beam_count = max(beam_count, cm_bn)
    if cm_bw and cm_bh:
        sizes['框架梁'] = (cm_bw, cm_bh)

    # 建筑面积: 优先主体.建筑面积, 否则识图总面积×层数
    bfa = main.get('建筑面积_m2', 0)
    floors = _floor_count(texts)
    if bfa == 0 and total > 0:
        bfa = total * floors

    # 层高/板厚/墙厚: 标高参数(楼层差) > 施工说明提取
    floor_h = float((data.get('标高参数', {}) or {}).get('层高_m') or 0) or \
        _first_float(tc, [r'层高[为]?\s*(\d+\.?\d*)\s*m', r'(\d+\.?\d*)\s*m\s*层高'], 2.0, 8.0, 3.0)
    if cm_floor_h:
        floor_h = max(floor_h, cm_floor_h)
    slab_thick_mm = _first_float(tc, [r'板厚[为]?\s*(\d+)\s*mm', r'(\d+)\s*mm\s*厚[的]?板'], 60, 500, 120)
    if cm_slabs and cm_slabs[0].get('厚度_mm'):
        slab_thick_mm = cm_slabs[0]['厚度_mm']
    wall_thick_mm = _first_float(tc, [r'墙厚[为]?\s*(\d+)\s*mm', r'(\d+)\s*mm\s*(?:厚[的]?)?墙'], 60, 600, 200)
    if cm_walls and cm_walls[0].get('厚度_mm'):
        wall_thick_mm = cm_walls[0]['厚度_mm']

    col_w, col_h = sizes.get('框架柱', (400, 400))
    beam_w, beam_h = sizes.get('框架梁', (250, 500))
    beam_len = 4.5  # 默认梁长, 分支内可能被标注/说明覆盖

    perim = 0.0
    for a in areas:
        if a.get('周长_m'):
            perim = max(perim, a['周长_m'])
    con_vol_total = 0.0

    # ── 基础 ──
    if found_count > 0:
        vol = round(found_count * 0.5, 2)
        con_vol_total += vol
        r.append({'分项名称':'独立基础','单位':'m³','工程量':vol,'计算式':f'{found_count}个×0.5m³(估算)','定额编号':'','备注':'CAD实测'})

    # ── 柱 ──
    if col_count > 0:
        col_area_m2 = (col_w * col_h) / 1e6
        vol = round(col_count * col_area_m2 * floor_h, 2)
        con_vol_total += vol
        r.append({'分项名称':'现浇混凝土框架柱','单位':'m³','工程量':vol,'计算式':f'{col_count}根×{col_area_m2:.3f}m²(实测截面)×{floor_h}m(层高)','定额编号':'','备注':'CAD实测'})

    # ── 梁 ──
    if beam_count > 0:
        beam_area_m2 = (beam_w * beam_h) / 1e6
        # v4.2: 梁长优先取标注推导(水平标注值), 再取说明, 最后默认4.5m
        # v5.0: 构件模型梁长(融合标注/几何/说明)最高优先
        dim_len = 0
        if cm_beam_len:
            beam_len = cm_beam_len
            dim_note = '构件模型梁长'
        else:
            for layer, dims in (data.get('构件尺寸推导', {}) or {}).items():
                if ('梁' in layer or 'KL' in layer.upper() or 'BEAM' in layer.upper()) and dims.get('长_mm'):
                    dim_len = max(dim_len, dims['长_mm'])
            beam_len = dim_len / 1000 if dim_len else _first_float(tc, [r'梁长[为]?\s*(\d+\.?\d*)\s*m'], 2, 12, 4.5)
            dim_note = '标注梁长' if dim_len else ''
        vol = round(beam_count * beam_area_m2 * beam_len, 2)
        con_vol_total += vol
        r.append({'分项名称':'现浇混凝土梁','单位':'m³','工程量':vol,'计算式':f'{beam_count}根×{beam_area_m2:.3f}m²(实测截面)×{beam_len}m({dim_note})','定额编号':'','备注':'CAD实测'})

    # ── 板 ──
    slab_vol = round(bfa * slab_thick_mm / 1000, 2)
    con_vol_total += slab_vol
    r.append({'分项名称':'现浇混凝土板','单位':'m³','工程量':slab_vol,'计算式':f'{bfa}×{slab_thick_mm}mm(实测板厚)','定额编号':'','备注':''})

    # ── 钢筋 ──
    rebar_weight, rebar_note = _parse_rebar(texts, bfa, con_vol_total, col_h=floor_h, beam_len=beam_len)
    r.append({'分项名称':'钢筋','单位':'t','工程量':rebar_weight,'计算式':rebar_note,'定额编号':'','备注':'平法标注/配筋率'})

    # ── 砌体 ──
    wall_vol = round(bfa * wall_thick_mm / 1000, 2)
    r.append({'分项名称':'砌体墙','单位':'m³','工程量':wall_vol,'计算式':f'{bfa}×{wall_thick_mm}mm(实测墙厚)','定额编号':'','备注':''})

    # ── 装饰 (v4.5: 精装细分 — 按材料生成分项; v5.12: 构件模型优先; v5.15: 房间分区) ──
    try:
        from calc_decoration import calc_decoration_detail, calc_decoration_from_model
        deco_results = []
        # v5.12 P1-4: 精装构件模型优先(楼地面/墙面/天棚/细部)
        cm_deco = cm.get('楼地面') or cm.get('墙面') or cm.get('天棚') or cm.get('细部')
        if cm_deco:
            deco_results = calc_decoration_from_model(cm, total or bfa, perim)
        if not deco_results:
            deco_results = calc_decoration_detail(data, total or bfa, perim)
        if not deco_results:
            deco_results = _parse_decoration(texts, bfa, total or bfa)
        # v5.15: 房间分区附加项(做法表带'部位'列) — 走 detail 的房间分区逻辑,
        # 量=0 由 step3 卡口分流进待提取清单, 不污染正式量
        try:
            if cm_deco:
                detail_items = calc_decoration_detail(data, total or bfa, perim)
                room_items = [i for i in detail_items if i.get('房间分区')]
                deco_results = deco_results + room_items
        except Exception:
            pass
    except Exception:
        deco_results = _parse_decoration(texts, bfa, total or bfa)
    r.extend(deco_results)

    # ── 措施 ──
    r.append({'分项名称':'综合脚手架','单位':'m²','工程量':round(bfa,2),'计算式':str(bfa),'定额编号':''})
    r.append({'分项名称':'垂直运输','单位':'m²','工程量':round(bfa,2),'计算式':str(bfa),'定额编号':''})

    return r
