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


# ── v6.0 P1: 构件扣减 — 板扣洞 / 墙扣门窗 / 梁柱扣减 ──

def _deduct_slab_holes(data, bfa):
    """板扣洞: 板洞面积(电梯井/楼梯间/设备孔洞)从板面积扣减。
    数据来源: ①施工说明/表格中的'洞口/孔洞/电梯井/楼梯间'面积标注
              ②构件模型板的 洞口列表(预留)
    返回 (洞口面积_m2, 备注)。
    """
    texts = ' '.join(data.get('施工说明', []) or [])
    hole_area = 0.0
    # 说明中显式洞口面积: '洞口面积 XX m²' / '开洞 XXm2' / '电梯井 XX'
    m = re.search(r'(?:洞口|孔洞|开洞|电梯井|楼梯间)[面积]*(?:为|:)?\s*(\d+\.?\d*)\s*m[²2]', texts)
    if m:
        hole_area = float(m.group(1))
    # 表格洞口: 构件模型板带 洞口面积
    for slab in (data.get('构件模型', {}) or {}).get('板', []) or []:
        if slab.get('洞口面积_m2'):
            hole_area = max(hole_area, float(slab['洞口面积_m2']))
    if hole_area >= bfa * 0.9:
        return 0.0, ''  # 异常: 洞口>90%面积, 视为噪声
    if hole_area > 0:
        return round(hole_area, 2), f'板扣洞{round(hole_area,2)}m²'
    return 0.0, ''


def _deduct_window_doors(data):
    """墙扣门窗: 门窗表洞口总面积(宽×高×数量)从墙体体积扣减。
    数据来源: pid['门窗'] (step1 门窗表解析)。
    返回 (洞口面积_m2, 备注)。
    """
    wd = data.get('门窗') or []
    if not wd:
        return 0.0, ''
    total = sum(float(x.get('洞口面积_m2', 0)) * int(x.get('数量', 1) or 1) for x in wd)
    if total <= 0:
        return 0.0, ''
    detail = '、'.join(f"{x.get('门窗号','')}×{x.get('数量',1)}" for x in wd[:6])
    return round(total, 2), f'墙扣门窗{round(total,2)}m²({detail})'

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

def _parse_rebar(texts, bfa, con_vol_total, col_h=3.0, beam_len=6.0, views=None):
    """钢筋量: 平法标注结构化解析(v4.2) > 配筋率(全构件体积) > 含钢量 > 默认65kg/m²
    v6.9.5 思维层②: 返回附'参考对比' — 平法/配筋率/含钢量多法互证(偏差>30%提示)。
    """
    from rebar_parse2 import parse_rebar_notes, calc_total_steel

    def _concrete_grade(texts):
        # v6.9.8: 混凝土等级从施工说明提取(C25/C30/C35/C40) → 16G101 锚固查表
        tc = ' '.join(texts or [])
        m = re.search(r'\bC([234]\d)\b', tc)
        return f'C{m.group(1)}' if m else 'C30'
    # v6.10.7 优先: **图幅分区配筋**(按图名把构件与其配筋配对) —— 实测可读出梁201/柱59,
    # 而单纯 parse_rebar_notes(施工说明) 只读到板筋 7 组 → 钢筋长期停留在含钢量估算。
    parsed = None
    if views:
        try:
            from rebar_parse2 import build_parsed_from_views, parse_rebar_by_view
            _vp = build_parsed_from_views(parse_rebar_by_view(views))
            if _vp and (_vp['beams'] or _vp['columns'] or _vp['slabs']):
                parsed = _vp
        except Exception as _e_v:
            print('  图幅配筋解析跳过: %s' % _e_v)
    if parsed is None:
        parsed = parse_rebar_notes(texts)
    if parsed['beams'] or parsed['columns'] or parsed['slabs']:
        total, detail = calc_total_steel(parsed, bfa, col_h=col_h, beam_len=beam_len,
                                         concrete=_concrete_grade(texts))
        if total > 0:
            # 参考法: 含钢量(kg/m² 说明值或 65 默认)
            tc = ' '.join(texts)
            m = re.search(r'(\d+\.?\d*)\s*kg/[m㎡]', tc)
            ref_kg = float(m.group(1)) if m else 65
            ref = round(bfa * ref_kg / 1000, 2)
            note = f'平法结构化解析: {detail}'
            if abs(total - ref) / max(total, ref) > 0.3:
                note += f'；⚠含钢量法参考 {ref}t(偏差{(total-ref)/max(total,ref)*100:.0f}%), 需复核'
            else:
                note += f'；含钢量法印证 {ref}t(偏差{(total-ref)/max(total,ref)*100:.0f}%)'
            return total, note
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
        results.append({'分项名称':'地砖地面','单位':'m²','工程量':round(total_area*ground_coef,2),'计算式':f'{total_area}×{ground_coef}(系数估算)','定额编号':'','备注':'施工说明识别'})
    elif '地板' in tc:
        results.append({'分项名称':'木地板地面','单位':'m²','工程量':round(total_area*ground_coef,2),'计算式':f'{total_area}×{ground_coef}(系数估算)','定额编号':'','备注':'施工说明识别'})
    else:
        results.append({'分项名称':'地面装饰','单位':'m²','工程量':round(total_area*ground_coef,2),'计算式':f'{total_area}×{ground_coef}(系数估算)','定额编号':'','备注':'施工说明'})

    wall_coef = _first_float(tc, [r'墙面系数\s*(\d+\.?\d*)'], 0.5, 6.0, 2.8)
    if '乳胶漆' in tc or '涂料' in tc:
        results.append({'分项名称':'内墙乳胶漆','单位':'m²','工程量':round(total_area*wall_coef,2),'计算式':f'{total_area}×{wall_coef}(系数估算)','定额编号':'','备注':'施工说明识别'})
    elif '墙砖' in tc:
        results.append({'分项名称':'墙面砖','单位':'m²','工程量':round(total_area*wall_coef,2),'计算式':f'{total_area}×{wall_coef}(系数估算)','定额编号':'','备注':'施工说明识别'})
    else:
        results.append({'分项名称':'内墙面装饰','单位':'m²','工程量':round(total_area*wall_coef,2),'计算式':f'{total_area}×{wall_coef}(系数估算)','定额编号':'','备注':'施工说明'})

    if dec['天棚']:
        results.append({'分项名称':'天棚装饰','单位':'m²','工程量':round(total_area,2),'计算式':str(total_area)+'(估算)','定额编号':'','备注':'施工说明识别'})
    else:
        results.append({'分项名称':'天棚装饰','单位':'m²','工程量':round(total_area,2),'计算式':str(total_area)+'(估算)','定额编号':'','备注':'施工说明'})

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
    # v6.10.7 ④ 平法/配筋解析专用文字池: 汇总**全图文字**(含平法配筋标注 %%c6@100/200、块内、
    # 标注文本)。实测教训: 原只喂"施工说明"(80 条) → _parse_rebar 读不到配筋 → 退回
    # 65kg/m² 含钢量估算; 而全图文字 4513 条里配筋标注就在其中。
    # 注: 仅用于平法解析, 不并入 tc(避免 4513 条文字污染其它关键词判断语境)。
    _flat_rebar = list(texts)
    for _x in (data.get('全图文字') or []):
        _flat_rebar.append(str(_x.get('文本', _x)) if isinstance(_x, dict) else str(_x))

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
    # v6.10.7 门槛: 只认**带真实坐标(位置非空)**的柱 —— 几何建模产物;
    # 参数式推导的柱 position 为 null → 保持原口径(基准用例不受影响)。
    _geom_cols = [x for x in cm_cols
                   if isinstance(x.get('位置'), (list, dict)) and x.get('位置')]
    if _geom_cols:
        # v6.10.7 ②几何建模优先: 有真实轮廓坐标时**以模型为准**(cm_n), 不用 CAD 图元计数
        # (实测 000007: 图元计数 101 / 模型 91 / 几何轮廓 88 → 三值不一致, 统一取几何建模值)
        col_count = cm_n
    elif cm_n:
        col_count = max(col_count, cm_n)
    if cm_w and cm_h:
        sizes['框架柱'] = (cm_w, cm_h)
    if cm_bn:
        beam_count = max(beam_count, cm_bn)
    if cm_bw and cm_bh:
        sizes['框架梁'] = (cm_bw, cm_bh)

    # 建筑面积（v6.10.7 口径归正）: 按 GB/T 50353-2013 规则 —— 逐层外墙围合轮廓求和。
    # 单图幅图纸: 图上仅一层平面 → "单层轮廓 × 层数"等价于逐层求和(成立);
    # **多图幅混排图纸: 几何常取到剖面/详图轮廓 → 禁用按层数放大**(实测 000008 几何 47m²
    # × 4 层 = 188m², 而真实建筑面积 2411.48m²), 改为标待核, 不产出错数。
    bfa = main.get('建筑面积_m2', 0)
    floors = _floor_count(texts)
    if bfa == 0 and total > 0:
        if (data.get('图幅面积参考') or {}).get('幅数'):
            bfa = 0
            print('  建筑面积: 多图幅混排 → 不按层数放大(几何值不可靠), 标待核实')
        else:
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
        # v6.9.8 ⑨: 挖基础土方分级 — 有基础证据时按 基础体积×1.15(工作面+放坡)
        r.append({'分项名称':'挖基础土方','单位':'m³','工程量':round(vol * 1.15, 2),
                  '计算式':f'基础体积{vol}×1.15(工作面+放坡系数,估算)', '定额编号':'',
                  '备注':'估算', '数据来源':'估算'})
    else:
        r.append({'分项名称':'挖基础土方','单位':'m³','工程量':0,
                  '计算式':'待提取: 无基础图证据, 不得按建筑面积估算土方',
                  '定额编号':'', '备注':'待提取', '数据来源':'待提取'})

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
        # v6.10.7 门槛式几何优先: **有 BEAM 轴线几何**时, 平均梁长 = 几何总长/梁根数;
        # 无几何(基准简单图)则保持构件模型值 —— 无条件替换会使基准退化(实测 100%→98%)。
        _bgm2 = (cm.get('梁几何') or {})
        if _bgm2.get('总长_m') and cm_bn:
            beam_len = round(_bgm2['总长_m'] / cm_bn, 2)
        elif cm_beam_len:
            beam_len = cm_beam_len
            dim_note = '构件模型梁长'
        else:
            for layer, dims in (data.get('构件尺寸推导', {}) or {}).items():
                if ('梁' in layer or 'KL' in layer.upper() or 'BEAM' in layer.upper()) and dims.get('长_mm'):
                    dim_len = max(dim_len, dims['长_mm'])
            beam_len = dim_len / 1000 if dim_len else _first_float(tc, [r'梁长[为]?\s*(\d+\.?\d*)\s*m'], 2, 12, 4.5)
            dim_note = '标注梁长' if dim_len else ''
        # v6.10.7 ②梁量按**几何轴线总长**计(GB/T 50854: 梁按体积 m3):
        # 实测 000007: BEAM 轴线合计 747.9m, 而"根数×长度"式给 267×1.13m(失真)。
        _bg = (cm.get('梁几何') or {})
        # v6.10.7 门槛: 几何总长需与梁模型坐标一致(或段数≥3)才启用, 防简单图误切
        # 判据: 几何总长存在即用(基准简单图无 BEAM 几何 → 自动回退参数式)
        _beam_geom_ok = bool(_bg.get('总长_m'))
        if _beam_geom_ok:
            vol = round(_bg['总长_m'] * beam_area_m2, 2)
            calc_beam = (f"梁轴线几何总长 {_bg['总长_m']}m({_bg['段数']} 段, "
                         f"最长 {_bg['最长_m']}m) × 平均截面 {beam_area_m2:.3f}m²")
        else:
            vol = round(beam_count * beam_area_m2 * beam_len, 2)
            calc_beam = f'{beam_count}根×{beam_area_m2:.3f}m²×{beam_len}m({dim_note})'

        # v6.0 P1-c: 梁柱扣减 — 梁端伸入柱内重叠体积(每根梁按两端入柱扣减)
        deduct_note = ''
        if col_count > 0 and col_w > 0 and col_h > 0:
            overlap_per_beam = (min(beam_w, col_w) / 1000) * (beam_h / 1000) * (col_w / 1000)
            overlap = round(overlap_per_beam * beam_count * 2, 3)  # 两端
            if overlap > 0 and overlap < vol * 0.3:
                vol = round(vol - overlap, 2)
                if vol < 0:
                    vol = 0.0
                deduct_note = f'-梁柱重叠{overlap}m³'
        con_vol_total += vol
        r.append({'分项名称':'现浇混凝土梁','单位':'m³','工程量':vol,
                  '计算式':calc_beam + deduct_note, '定额编号':'',
                  '备注':'CAD实测(几何建模)', '数据来源':'实测'})

    # ── 板 (v6.0: 扣洞口 — 板洞/楼梯井等) ──
    # v6.10.7 ①板混凝土按**板面积**×板厚(GB/T 50854: 板按体积) —— 原用建筑面积(bfa)
    # 会把'整栋建筑面积'当板面积(实测 000007: 1276.8×120mm=153m³, 严重偏大)。
    _geom_slabs = [x for x in cm_slabs
                   if x.get('位置') or any(k in str(x.get('面积来源') or '')
                                           for k in ('几何', '多图幅', '实测', '轮廓'))]
    _slab_area_m2 = (sum((x.get('面积_m2') or 0) for x in _geom_slabs)
                     if _geom_slabs else bfa)
    slab_vol = round(_slab_area_m2 * slab_thick_mm / 1000, 2)
    hole_area, hole_note = _deduct_slab_holes(data, bfa)
    if hole_area > 0:
        slab_vol = round(slab_vol - hole_area * slab_thick_mm / 1000, 2)
        if slab_vol < 0:
            slab_vol = 0.0
        calc_note = f'{bfa}×{slab_thick_mm}mm(实测板厚)-{hole_area}m²洞口×{slab_thick_mm}mm'
    else:
        calc_note = f'{bfa}×{slab_thick_mm}mm(实测板厚)'
    con_vol_total += slab_vol
    r.append({'分项名称':'现浇混凝土板','单位':'m³','工程量':slab_vol,'计算式':calc_note,'定额编号':'','备注':hole_note})

    # v6.9.7 ⑥: 模板接触面积(造价人: 混凝土量→模板量→措施费, 三表联动)
    # 柱=周长×高×n, 梁=2×(宽+高)×长×n, 板=板面积×层数, 墙=2×墙面积; 无构件证据不估算
    tmpl = []
    try:
        if cm_n and col_w and col_h:
            tmpl.append(('柱模板', round(2 * (col_w + col_h) / 1000 * floor_h * cm_n, 2),
                         f'{cm_n}根×周长{2*(col_w+col_h)/1000:.2f}m×层高{floor_h}m'))
        if cm_bn and beam_w and beam_h:
            _bg2 = (cm.get('梁几何') or {})
            _bl_m = (_bg2.get('总长_m') or 0) or (beam_len * cm_bn)
            tmpl.append(('梁模板', round(2 * (beam_w + beam_h) / 1000 * _bl_m, 2),
                         f'梁轴线总长{_bl_m:.2f}m × 2×(宽{beam_w}+高{beam_h})/1000'))
        if total > 0 and floors:
            _slab_a = (sum((x.get('面积_m2') or 0) for x in _geom_slabs)
                       if _geom_slabs else total)
            tmpl.append(('板模板', round(_slab_a, 2),
                         f'板面积{_slab_a:.2f}m²(构件模型, 底模; 原"总面积×层数"已废'))
        if cm_walls:
            wall_vol = sum(float(w.get('体积_m3', 0) or 0) for w in cm_walls)
            wall_th = (cm_walls[0].get('厚度_mm') or wall_thick_mm or 200) / 1000
            if wall_vol > 0 and wall_th > 0:
                tmpl.append(('墙模板', round(2 * wall_vol / wall_th if wall_th else 0, 2),
                             f'2×墙体积{wall_vol:.1f}m³÷墙厚{wall_th*1000:.0f}mm'))
    except Exception:
        tmpl = []
    for tname, tq, tnote in tmpl:
        r.append({'分项名称': tname, '单位': 'm²', '工程量': tq,
                  '计算式': f'{tnote}(模板接触面积)', '定额编号': '', '备注': 'CAD实测'})

    # ── 钢筋 ──
    # v6.10.7 板筋量 = 含量 × **板面积**(不是建筑面积) —— 建筑面积口径修正后多图幅图 bfa=0,
    # 会使板筋归零(实测主链钢筋 33.35t vs 直调 83.21t, 差的就是板筋)。
    _bfa_rebar = bfa or sum((x.get('面积_m2') or 0) for x in cm_slabs) or total
    rebar_weight, rebar_note = _parse_rebar(_flat_rebar, _bfa_rebar, con_vol_total,
                                            col_h=floor_h, beam_len=beam_len,
                                            views=data.get('全图文字'))
    r.append({'分项名称':'钢筋','单位':'t','工程量':rebar_weight,'计算式':rebar_note,'定额编号':'','备注':'平法标注/配筋率'})

    # ── 砌体 (v6.0: 扣门窗洞口) ──
    wall_vol = round(bfa * wall_thick_mm / 1000, 2)
    wd_area, wd_note = _deduct_window_doors(data)
    if wd_area > 0:
        wall_vol = round(wall_vol - wd_area * wall_thick_mm / 1000, 2)
        if wall_vol < 0:
            wall_vol = 0.0
        wall_note = f'{bfa}×{wall_thick_mm}mm(实测墙厚)-{wd_area}m²门窗洞口×{wall_thick_mm}mm'
    else:
        wall_note = f'{bfa}×{wall_thick_mm}mm(实测墙厚)'
    r.append({'分项名称':'砌体墙','单位':'m³','工程量':wall_vol,'计算式':wall_note,'定额编号':'','备注':wd_note})

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

    # ── 措施 (v6.1 C方案: measure_rule_items 规则表驱动, 按专业+公式计算) ──
    try:
        from pipeline.db import get_liaoning_conn
        conn = get_liaoning_conn()
        measure_rules = conn.execute(
            "SELECT measure_type, item_name, unit, formula, factor, applicable_specialties, note "
            "FROM measure_rule_items ORDER BY id").fetchall()
        conn.close()
        specialty = data.get('专业类型', '')
        # 公式上下文(安全求值)
        ctx = {
            'bfa': float(bfa or 0), 'area': float(total or 0), 'perim': float(perim or 0),
            'floor_h': float(floor_h or 0), 'floors': float(floors or 1),
            'con_vol': float(con_vol_total or 0), 'con_vol_total': float(con_vol_total or 0),
            'col_count': float(col_count or 0), 'beam_count': float(beam_count or 0),
            'beam_len': float(beam_len or 0), 'col_w': float(col_w or 0), 'col_h': float(col_h or 0),
            'beam_w': float(beam_w or 0), 'beam_h': float(beam_h or 0),
        }
        import math as _m
        for _r in measure_rules:
            _t, _name, _unit, _formula, _factor, _specs, _note = _r
            # 专业过滤
            if _specs and specialty not in _specs:
                continue
            try:
                _v = float(eval(_formula, {'__builtins__': {}}, dict(ctx))) * float(_factor or 1.0)
            except Exception:
                _v = 0.0
            if _v <= 0 and '1' not in _formula:
                continue
            r.append({'分项名称': _name, '单位': _unit, '工程量': round(_v, 2),
                     '计算式': f'{_formula}({_note})', '定额编号': '', '备注': '措施规则'})
    except Exception as e:
        # 回退: 原硬编码
        r.append({'分项名称': '综合脚手架', '单位': 'm²', '工程量': round(bfa, 2), '计算式': str(bfa), '定额编号': ''})
        r.append({'分项名称': '垂直运输', '单位': 'm²', '工程量': round(bfa, 2), '计算式': str(bfa), '定额编号': ''})

    return r
