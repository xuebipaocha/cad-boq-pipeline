"""
Step 1: 识图 — 准确性增强版

改进：
- 专业识别改为加权评分，输出候选和置信度。
- 面积优先使用闭合多段线，文字面积作为交叉验证。
- 从文字/分项名称提取厚度和材料，构造层不再全部 None。
- 将管道、路缘石、墙线等写入统一线性构件结构。
"""
import sys, os, json, re, importlib.util
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from units import resolve_area, is_excluded_layer, is_frame_poly

SPECIALTY_KEYWORDS = {
    '园林绿化工程': {'绿化':3,'苗木':3,'乔木':3,'灌木':3,'草坪':2,'园林':3,'景观':2,'种植':2},
    '安装工程': {'给水':3,'排水':3,'电气':3,'消防':3,'通风':2,'空调':2,'管道':2,'电缆':2,'桥架':2,'配电箱':2},
    '市政工程': {'道路':4,'路面':4,'路基':3,'桥梁':3,'管网':3,'路灯':2,'人行道':3,'侧石':3,'沥青':3,'水稳':3},
    '钢结构工程': {'钢梁':4,'钢柱':4,'H型':4,'钢结构':5,'吊车梁':4,'钢板':3,'檩条':3},
    '房屋建筑与装饰工程': {'建筑':2,'结构':2,'柱':2,'梁':2,'板':2,'砌体':2,'抹灰':2,'防水':2,'装饰':3,'门窗':2},
}
MATERIALS = ['沥青混凝土','水泥稳定碎石','级配碎石','混凝土','钢筋混凝土','砂浆','砌体','钢筋','防水卷材','乳化沥青','石油沥青','种植土','钢板','H型钢']

# ── v5.4 工程性质判定: 新建 / 大修与改造 ──
# 大修/改造信号(施工说明中的关键词, 权重为出现频次倍率)
# 注意: '修改' 不入词表 — 它是图纸修订记录(2026.2.26修改), 非工程改造信号
RENOVATION_KEYWORDS = {
    '拆除': 3, '维修': 2, '更换': 2, '大修': 3, '翻新': 2,
    '改造': 2, '既有': 1, '原有': 1, '加固': 2, '恢复': 1,
}
NEW_BUILD_KEYWORDS = {'新建': 3, '首建': 2, '施工图': 1}


def _detect_rooms_pid(dwg_file):
    """v6.1: 房间分区几何化 — 闭合区域→房间列表(面积/周长)。失败返回 []。"""
    try:
        from room_geometry import detect_rooms
        rooms = detect_rooms(dwg_file)
        if rooms:
            print(f'  房间: {len(rooms)} 个 ({", ".join(r["房间名"] for r in rooms)})')
        return rooms
    except Exception:
        return []


def _parse_legends_pid(msp):
    """v6.3 B2: 图例表解析(符号↔构件)。失败返回 []。"""
    try:
        if msp is None:
            return []
        from legend_parser import parse_legends
        legends = parse_legends(msp)
        if legends:
            print(f'  图例: {len(legends)} 条')
        return legends
    except Exception:
        return []


def _extract_title_block_pid(dwg_file):
    """v6.3 C2: 图签提取 — 图名/图号/比例(右下角图签区文字)。失败返回 {}。"""
    try:
        import ezdxf
        doc = ezdxf.readfile(dwg_file)
        msp = doc.modelspace()
        # 收集文字(位置)
        texts = []
        for e in msp:
            if e.dxftype() not in ('TEXT', 'MTEXT'):
                continue
            try:
                txt = (e.dxf.text if e.dxftype() == 'TEXT' else e.text) or ''
            except Exception:
                continue
            if txt.strip():
                texts.append((txt.strip(), e.dxf.insert.x, e.dxf.insert.y))
        if not texts:
            return {}
        # 图签区 = 右下角 20% 区域
        xs = [t[1] for t in texts]
        ys = [t[2] for t in texts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        tb_x0 = x_min + (x_max - x_min) * 0.6
        tb_y0 = y_min + (y_max - y_min) * 0.65
        tb_texts = [t for t in texts if t[1] >= tb_x0 and t[2] >= tb_y0]
        if len(tb_texts) < 3:
            # 回退: 全图找 图名/图号 关键词
            tb_texts = texts
        out = {'图名': '', '图号': '', '比例': ''}
        for t, x, y in tb_texts:
            # 图名: 含'图'结尾的长文字(如 '一层平面图')
            if not out['图名'] and 4 <= len(t) <= 20 and ('图' in t or '表' in t or '详' in t):
                out['图名'] = t
            # 图号: 形如 A-01 / 建施-01 / 图号:xxx
            m = re.search(r'([A-Za-z\u4e00-\u9fa5]{1,4}[-－]\d{1,3})', t)
            if m and not out['图号']:
                out['图号'] = m.group(1)
            # 比例: 1:100 / 1:50
            m = re.search(r'(1\s*[:：]\s*\d{1,4})', t)
            if m and not out['比例']:
                out['比例'] = m.group(1)
        return out
    except Exception:
        return {}


def _extract_local_notes_pid(dwg_file):
    """v6.3: 局部小说明提取+部位关联。失败返回 []。"""
    try:
        from local_notes import extract_local_notes
        notes = extract_local_notes(dwg_file)
        if notes:
            print(f'  局部注释: {len(notes)} 条')
        return notes
    except Exception:
        return []


def _parse_design_notes_pid(texts):
    """v6.2: 设计说明专项解析 — 材料规格/施工范围/做法层次/工程概况。失败返回 {}。"""
    try:
        from design_notes import parse_design_notes
        notes = parse_design_notes(texts or [])
        if notes.get('检测到设计说明') or notes.get('材料规格'):
            specs = notes.get('材料规格', [])
            print(f'  设计说明: {len(specs)} 个材料规格, {len(notes.get("做法层次", []))} 组做法, 概况{sum(1 for v in notes.get("工程概况", {}).values() if v)}项')
        return notes
    except Exception:
        return {}


def detect_project_nature(texts):
    """工程性质判定: 新建 / 大修与改造。
    加权打分: 大修信号(拆除/维修/更换...) vs 新建信号。
    判定规则: 大修总分 ≥4 才判大修与改造(保守阈值, 弱信号如'更换×1'
    默认新建 — 避免带修订记录的新建设计图误判)。
    """
    hay = ' '.join(texts or [])
    ren_score = 0
    ren_hits = []
    for kw, w in RENOVATION_KEYWORDS.items():
        n = hay.count(kw)
        if n > 0:
            ren_score += n * w
            ren_hits.append(f'{kw}×{n}')
    new_score = 0
    new_hits = []
    for kw, w in NEW_BUILD_KEYWORDS.items():
        n = hay.count(kw)
        if n > 0:
            new_score += n * w
            new_hits.append(f'{kw}×{n}')
    if ren_score >= 4:
        return '大修与改造', {'分数': ren_score, '证据': ren_hits, '新建分数': new_score}
    return '新建', {'分数': new_score, '证据': new_hits, '大修分数': ren_score}


def detect_specialty_detail(layers, texts):
    hay = (' '.join(layers) + ' ' + ' '.join(texts)).lower()
    scores = {}
    for sp, kws in SPECIALTY_KEYWORDS.items():
        score = 0
        hits = []
        for kw, w in kws.items():
            if kw.lower() in hay:
                score += w; hits.append(kw)
        scores[sp] = {'score': score, 'hits': hits}
    ranked = sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)
    best, info = ranked[0]
    total = sum(v['score'] for v in scores.values()) or 1
    confidence = round(info['score'] / total, 3) if info['score'] else 0
    if info['score'] == 0:
        best = '房屋建筑与装饰工程'; confidence = 0.2
    candidates = [{'专业': sp, '分数': data['score'], '命中': data['hits']} for sp, data in ranked if data['score'] > 0]
    return best, confidence, candidates


def detect_specialty(layers, texts):
    """兼容旧调用：只返回专业名称。"""
    return detect_specialty_detail(layers, texts)[0]


def extract_thickness(text):
    if not text: return None
    pats = [r'(\d+(?:\.\d+)?)\s*(?:mm|MM|毫米)\s*厚', r'厚\s*(\d+(?:\.\d+)?)\s*(?:mm|MM|毫米)',
            r'h\s*=\s*(\d+(?:\.\d+)?)\s*(?:mm|MM)?', r'(\d+(?:\.\d+)?)\s*cm\s*厚',
            r'(\d+(?:\.\d+)?)\s*厘米\s*厚', r'厚\s*(\d+(?:\.\d+)?)\s*cm',
            r'(\d+(?:\.\d+)?)\s*cm(?:的|厚)?[^0-9]', r'(\d+(?:\.\d+)?)\s*mm(?:的|厚)?[^0-9]']
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            val = float(m.group(1))
            if 'cm' in m.group(0).lower() or '厘米' in m.group(0):
                val *= 10
            return round(val, 2)
    m = re.search(r'(\d+(?:\.\d+)?)\s*m\s*厚', text, re.I)
    if m:
        return round(float(m.group(1)) * 1000, 2)
    return None


def extract_material(text):
    for mat in MATERIALS:
        if mat in (text or ''):
            return mat
    return ''


def build_layers(qty_items, raw_texts):
    """构造层: v4.0 优先从施工说明逐行提取(名称/厚度/材料), qty_items 仅作兜底"""
    layers = []
    all_text = '\n'.join(raw_texts)
    # 施工说明行切层: 每行含厚度或材料关键词的行即为一个构造层
    # v4.0: 排除 '安装/铺设/施工' 类做法行(如 '侧石安装 C30混凝土' 不是构造层)
    # v4.1: 排除 标高文字(▲/▼/标高/结构标高) 与 表格外的散落文字
    # v4.3: 排除 面积标注行('建筑面积360m2') 与 纯平法标注行(KZ1 500×500...)
    seen = set()
    EXCLUDE_ACT = ['安装', '铺装', '施工', '做法', '采用', '使用', '砌筑', '抹灰', '涂刷']
    ELEV_KW = ['▲', '▼', '△', '▽', '标高', '高程', '结构标高', '建筑标高']
    import re as _re
    for line in raw_texts:
        if any(k in line for k in ELEV_KW):
            continue
        # v6.6: 施工说明长段落/规范引用/条款序号不是构造层 —
        # 真实图纸设计说明常为整段文字, 原逻辑把'9）《抹灰砂浆技术规程》…'、
        # '2. 走廊及楼梯间混凝土地面采用…' 全收成构造层(污染厚度均值)
        if len(line) > 40:
            continue
        if re.search(r'GB\s?\d|JGJ|JG/T|规范|规程|图集|02J\d', line):
            continue
        if re.match(r'^[（(]?\d+[)）]?[.、．]\s*\S{8,}', line):
            # v6.6: 条款序号(1. / （1）/ 1、)才排除 — 数字后直接跟 cm/mm 是厚度写法
            # ('20cm级配碎石下基层'/'4cm细粒式沥青混凝土' 是构造层, 不得误伤)
            continue
        # v6.6: 构造层名称是材料/做法短语, 不含句子标点 — 段落性说明
        # ('五、主要材料及构造设计…。'/'WMM5、DMM5M5 混合砂浆'/'楼2PVC…，走廊…')
        # 混入会污染清单特征(step4 把全部构造层拼进 features)
        if any(c in line for c in '。；，、；（）'):
            continue
        # v4.3.1: 面积标注行排除(无论是否混入其他词)
        if _re.search(r'面积\s*\d+\.?\d*\s*m\s*[2²]', line):
            continue
        # v4.3.2: 平法标注行排除(含Φ且含×, 通常是纯配筋标注)
        if _re.search(r'[ΦφΦ]\s*\d+\s*@', line) and ('×' in line or 'x' in line) and '厚' not in line:
            continue
        if _re.search(r'[A-Z]{1,3}\d+\s*\(?\d*\)?\s*\d+[×xX]\d+', line) and 'Φ' in line:
            continue
        if any(k in line for k in EXCLUDE_ACT) and not any(k in line for k in ['厚', 'cm', 'mm', '基层', '面层', '垫层']):
            continue
        if any(k in line for k in ['厚', '沥青', '水稳', '稳定', '混凝土', '基层', '面层', '垫层',
                                   '级配', '碎石', '砂', '土', '透层', '粘层', '封层', 'cm', 'mm']):
            name = line[:30]
            if name in seen:
                continue
            seen.add(name)
            th = extract_thickness(line)
            mat = extract_material(line)
            layers.append({'名称': name, '厚度_mm': th, '材料': mat,
                           '厚度来源': '施工说明提取' if th else '未识别'})
    # 兜底: qty_items
    if not layers:
        for item in qty_items:
            name = item.get('name','') or item.get('名称','')
            text = name + ' ' + all_text
            th = extract_thickness(name) or extract_thickness(all_text)
            mat = extract_material(name) or extract_material(all_text)
            layers.append({'名称': name, '厚度_mm': th, '材料': mat, '厚度来源': '图纸文字/名称提取' if th else '未识别'})
    return layers


def choose_area(result, insunits=4):
    """v4.0: 统一面积裁决 — 闭合多段线优先(排除图框层), 文字面积交叉验证, 单位感知
    v5.3: 文字面积标注(无²后缀写法)作为最高权威 —
    真实图纸的闭合轮廓常被辅助线污染, 而图签明确写建筑面积;
    但仅当文字面积 ≥ 闭合轮廓面积时采用(防小图签数字误报)。
    v5.9: 交叉验证裁决 — 文字面积 vs 几何验证闭合面积差异 > 5 倍时,
    面积标记"待核"并输出验证提示(不再静默采用)。
    """
    q = result.get('quantity', {})
    text_area = q.get('total_area_m2') or 0
    polys = result.get('key_entities', {}).get('closed_polylines', []) or []
    poly_areas = [p for p in polys if p.get('area_m2', 0) > 1 and not is_excluded_layer(p.get('layer', ''))]
    # v4.3: 0层图框排除(面积显著大于其他轮廓)
    all_areas = [p.get('area_m2', 0) for p in poly_areas]
    poly_areas = [p for p in poly_areas if not is_frame_poly(p.get('area_m2', 0), p.get('layer', ''), all_areas)]
    area, source, notes = resolve_area(text_area, poly_areas, [], insunits)
    if not area and text_area:
        area, source = text_area, '文字面积标注'
    # v5.3: 文字面积权威 — 图签建筑面积 > 闭合轮廓(防辅助线污染)
    poly_best = max((p.get('area_m2', 0) for p in poly_areas), default=0)
    if text_area > 0 and text_area >= poly_best and poly_best > 0 and text_area >= 2 * poly_best:
        area, source = text_area, '文字面积标注(图签权威)'
        notes.append(f'图签面积({text_area:.0f}m²)大于闭合轮廓({poly_best:.0f}m²), 采用图签值')
    # v5.9: 几何验证独立交叉 — 差异 > 5 倍 → 面积待核
    sv = result.get('svg_validation', {}) or {}
    if sv.get('available') and sv.get('largest_closed') and area and sv['largest_closed'] > 0:
        ratio = area / sv['largest_closed']
        if ratio > 5 or ratio < 0.2:
            notes.append(f'面积交叉验证差异大: 采用值{area:.0f}m² vs 几何闭合{sv["largest_closed"]:.0f}m² (比值{ratio:.1f}), 面积待核')
            # v5.15.1 修复: 交叉验证差异>5倍时回退闭合轮廓值(防新手图纸乱标面积污染)
            # 图签面积可能被"建筑面积2000m²"这类随手标注污染, 几何闭合是实际轮廓
            poly_best2 = max((p.get('area_m2', 0) for p in poly_areas), default=0)
            if poly_best2 > 0 and area != poly_best2:
                notes.append(f'已回退: 采用几何闭合轮廓{poly_best2:.0f}m² (弃用图签污染值{area:.0f}m²)')
                area, source = poly_best2, '闭合多段线(交叉验证回退)'
    return area, source, notes


def cad_analysis(dwg_file, insunits=4):
    import ezdxf
    from cad_extractor import extract_building_elements, extract_pipe_lengths, count_blocks, extract_dimensions, detect_scale
    doc = ezdxf.readfile(dwg_file)
    msp = doc.modelspace()
    if insunits is None:
        insunits = doc.header.get('$INSUNITS', 4)
    result = {'blocks': count_blocks(doc, msp), 'pipes': extract_pipe_lengths(doc, msp, insunits), 'scale': detect_scale(msp), 'dims': extract_dimensions(msp), 'elem': extract_building_elements(doc, msp)}
    result['total_pipe_len'] = result['pipes'].get('总长度_m', 0)
    result['tree_count'] = sum(result['blocks'].get('tree_blocks',{}).values())
    result['equip_count'] = sum(result['blocks'].get('equip_blocks',{}).values())
    return result


def run(dwg_file, output_dir):
    print('='*50); print('Step 1: 识图 — 增强版'); print('='*50)
    # v5.9: 依赖健康自检(失效自动修复, 不静默坏)
    try:
        from dep_health import ensure_healthy
        ok, _ = ensure_healthy(verbose=True)
        if not ok:
            print('  ⚠ 部分依赖失效且修复失败, 功能可能降级')
    except Exception as e:
        print(f'  ⚠ 依赖自检异常(跳过): {e}')
    skill_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analyze_cad.py')
    if not os.path.exists(skill_script): print('  [!] 未找到analyze_cad.py'); return {}
    spec = importlib.util.spec_from_file_location('analyze_cad', skill_script)
    cad_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(cad_module)
    print(f'  分析图纸: {dwg_file}')
    result = cad_module.analyze_cad(dwg_file)

    raw_texts = [c.get('text','') for c in result.get('text_clusters',[]) if len(c.get('text',''))>3]
    specialty, sp_conf, sp_candidates = detect_specialty_detail([l.get('name','') for l in result.get('layers',[])], raw_texts)
    print(f'  识别专业: {specialty} (置信度 {sp_conf})')

    # ── v5.4 工程性质判定: 新建 / 大修与改造 ──
    nature, nature_detail = detect_project_nature(raw_texts)
    print(f'  工程性质: {nature} {nature_detail.get("证据")}')

    qty = result.get('quantity',{})
    insunits = result.get('metadata', {}).get('insunits', 4)
    total_area, area_source, area_notes = choose_area(result, insunits)
    # 周长优先取非图框层的最大闭合多段线(v5.0: 与面积裁决同用 is_frame_poly,
    # 修复 0 层图框未被周长排除的疏漏 — 墙长/房间周长以主区域轮廓为准)
    closed = result.get('key_entities',{}).get('closed_polylines',[]) or []
    all_areas = [p.get('area_m2', 0) for p in closed if p.get('area_m2', 0) > 1]
    perimeter = 0.0
    for p in closed:
        if is_excluded_layer(p.get('layer', '')):
            continue
        if is_frame_poly(p.get('area_m2', 0), p.get('layer', ''), all_areas):
            continue
        if p.get('perimeter_m', 0) > perimeter:
            perimeter = p['perimeter_m']
    if perimeter == 0:
        perimeter = max([p.get('perimeter_m',0) for p in closed], default=0)
    construction_layers = build_layers(qty.get('items',[]), raw_texts)

    # ── v4.0 第一批: 表格解析 / 标高提取 / 图块属性 ──
    # v5.3: 每路证据独立 try — 一处失败不连坐(真实图纸上 section_calc 抛
    # KeyError 曾导致已算出的标注关联被 except 整体清空)
    tables_info = []
    elevs_info = []
    blocks_detail = {}
    _msp = None
    try:
        import ezdxf as _ezdxf
        _doc = _ezdxf.readfile(dwg_file)
        _msp = _doc.modelspace()
    except Exception as e:
        print(f'  读取DXF失败: {e}')
    if _msp is not None:
        # 1. 表格解析
        try:
            from table_parser import parse_tables, table_to_layers
            tables_info = parse_tables(_msp)
            window_doors = []  # v6.0: 门窗表 → 门窗明细(墙扣门窗)
            layers_from_table = []  # v6.6: 循环前初始化(首表为门窗表时 NameError 隐患)
            table_layers_seen = set()  # v6.6: 多做法表累加去重(原为覆盖 — 多表图纸只剩最后一张)
            for tb in tables_info:
                if tb['type'] == '做法表':
                    layers_from_table = table_to_layers(tb)
                    if layers_from_table:
                        # v6.6: 多张做法表逐张累加(真实图纸含 1+25+9+4+3+2 共6张做法表,
                        # 原逻辑后表覆盖前表, 44 层构造层最后只剩 2 层 — 算量厚度/材料大面积丢失)
                        added = 0
                        for lt in layers_from_table:
                            k = (lt.get('名称', ''), lt.get('材料', ''))
                            if k not in table_layers_seen:
                                table_layers_seen.add(k)
                                construction_layers.append(lt)
                                added += 1
                        print(f'  做法表: +{added} 层 (累计 {len(construction_layers)} 层, 表格优先)')
                elif tb['type'] == '门窗表':
                    # v6.5: 混排表(做法+门窗号+宽高数量) — 同时解析构造层与门窗明细
                    # (表头如 做法|厚度|材料|门窗号|洞口宽|洞口高|数量)
                    mixed_layers = table_to_layers(tb)
                    if mixed_layers and not layers_from_table:
                        construction_layers = mixed_layers
                    # v6.0: 门窗表 → [{门窗号, 宽_mm, 高_mm, 数量, 洞口面积_m2}]
                    headers = tb.get('headers', []) or []
                    hk = {h: i for i, h in enumerate(headers)}
                    # 定位列: 门窗号/宽/高/数量
                    def _find_col(*names):
                        for n in names:
                            for h, i in hk.items():
                                if n in h:
                                    return i
                        return None
                    i_id = _find_col('门窗号', '门号', '窗号', '设计编号', '编号')
                    i_w = _find_col('洞口宽', '宽')
                    i_h = _find_col('洞口高', '高')
                    i_n = _find_col('数量')
                    # v6.4: 洞口尺寸列 '900X2100' 合并格式 → 解析宽/高
                    i_size = _find_col('洞口尺寸', '洞口', '尺寸')
                    if i_id is not None:
                        for row in tb.get('rows', []):
                            cells = row.get('cells', [])
                            if len(cells) <= i_id:
                                continue
                            # v6.6: 门窗说明段落混入表格行 → 不进明细
                            row_text = ' '.join(str(c) for c in cells)
                            if any(k in row_text for k in ('门窗说明', '玻璃', 'JGJ', 'GB', '开启', '性能', '安全玻璃')):
                                continue
                            try:
                                # v6.6: 模式定位解析(不依赖固定列号) — 真实图纸门窗表数据行
                                # 列错位(WM-1527挤到类型列/'LC-1010 LC-1118'行整体左移一列),
                                # 且双门窗合并行('M-0927 M-0921'/'900X2100 900X2700'/'20 2')
                                # 原固定列解析只取第一个匹配, M-0921/LC-1118 等大量丢失
                                id_tokens, dims, nums = [], [], []
                                for c in cells[:7]:
                                    cs = str(c)
                                    id_tokens += re.findall(r'[A-Za-z]{1,3}-?\d{2,4}', cs)
                                    dims += re.findall(r'(\d+)\s*[xX×]\s*(\d+)', cs)
                                    # 数量列: 纯数字序列(排除含X/-/字母的编号/尺寸列)
                                    if re.fullmatch(r'\d+(?:\s+\d+)*', cs.strip()):
                                        nums += [int(v) for v in re.findall(r'\d+', cs.strip())]
                                if not id_tokens:
                                    continue
                                # 编号去噪: 排除 图纸目录类与尺寸串尾部 — '900X2100' 的
                                # 'X2100' 会被编号正则误收(真实门窗号无 X 开头, X 是尺寸分隔符)
                                id_tokens = [t for t in id_tokens
                                             if t[0].isalpha() and not t.upper().startswith('X')]
                                # 行列对齐: 数量个数与编号个数对齐(缺补1, 多截断)
                                if len(nums) < len(id_tokens):
                                    nums += [1] * (len(id_tokens) - len(nums))
                                elif len(nums) > len(id_tokens):
                                    nums = nums[:len(id_tokens)]
                                for j, wd_id in enumerate(id_tokens):
                                    w = h = 0
                                    if j < len(dims):
                                        w, h = float(dims[j][0]), float(dims[j][1])
                                    if w <= 0 or h <= 0 and dims:
                                        w, h = float(dims[0][0]), float(dims[0][1])
                                    if w <= 0 or h <= 0:
                                        continue
                                    n = nums[j] if j < len(nums) else 1
                                    window_doors.append({
                                        '门窗号': wd_id, '宽_mm': w, '高_mm': h, '数量': n,
                                        '洞口面积_m2': round(w * h / 1e6, 4),
                                    })
                            except (ValueError, TypeError, IndexError):
                                continue
            if window_doors:
                print(f'  门窗表: {len(window_doors)} 个门窗 (墙扣门窗用)')
        except Exception as e:
            print(f'  表格解析失败: {e}')
        # 2. 标高提取
        elev_params = {}
        try:
            from elevation_extractor import extract_elevations, derive_params
            elevs_info = extract_elevations(_msp)
            elev_params = derive_params(elevs_info)
            if elev_params:
                print(f'  标高: {len(elevs_info)}个 挖深={elev_params.get("挖深_m")}m 层高={elev_params.get("层高_m")}m')
        except Exception as e:
            print(f'  标高提取失败: {e}')
        # 3. 图块属性+嵌套
        try:
            from block_enhanced import collect_blocks, summarize as block_summarize
            blocks_detail = block_summarize(collect_blocks(_doc, _msp))
        except Exception as e:
            print(f'  图块解析失败: {e}')
        # 4. 标注→构件语义关联 (第二批)
        try:
            from dimension_matcher import match_dimensions, derive_member_sizes
            dim_matches = match_dimensions(_msp)
            member_sizes = derive_member_sizes(dim_matches)
            if dim_matches:
                print(f'  标注关联: {len(dim_matches)}条 → {len(member_sizes)}个构件尺寸')
        except Exception as e:
            print(f'  标注关联失败: {e}')
            dim_matches = []
            member_sizes = {}
        # 5. 剖面联动算量 (第二批)
        section_qty = []
        try:
            from section_calc import calc_section_quantities
            section_qty = calc_section_quantities(_msp)
            if section_qty:
                print(f'  剖面联动: {len(section_qty)} 个断面 × 平面长度')
        except Exception as e:
            print(f'  剖面联动失败(跳过): {e}')
    else:
        dim_matches = []
        member_sizes = {}
        section_qty = []

    pid = {
        '专业类型': specialty,
        '专业识别': {'置信度': sp_conf, '候选': sp_candidates},
        '工程性质': nature,  # v5.4: 新建 / 大修与改造
        '工程性质证据': nature_detail,
        '图纸元数据': {'单位': result.get('metadata',{}).get('unit','mm'), 'insunits': insunits, '实体总数': result.get('metadata',{}).get('entity_total',0),
                      **(_extract_title_block_pid(dwg_file) if 'dwg_file' in dir() else {})},  # v6.3 C2: 图签(图名/图号/比例)
        '面积区域': [{'名称':'主区域','面积_m2':round(total_area,2),'周长_m':round(perimeter,2),'面积来源':area_source}] if total_area else [],
        '构造层': construction_layers,
        '线性构件': [],
        '施工说明': raw_texts[:80],
        '图纸问题候选': [n for n in result.get('validation',{}).get('notes',[])] + area_notes,
        'CAD分析': None,
        '表格': tables_info,
        '门窗': window_doors if 'window_doors' in dir() else [],  # v6.0: 门窗明细(墙扣门窗)
        '房间': _detect_rooms_pid(dwg_file),  # v6.1: 房间分区几何化(闭合区域→房间面积/周长)
        '设计说明': _parse_design_notes_pid(raw_texts),  # v6.2: 设计说明专项解析(材料规格/做法/概况)
        '局部注释': _extract_local_notes_pid(dwg_file),  # v6.3: 局部小说明提取+部位关联
        '图例': _parse_legends_pid(_msp),  # v6.3 B2: 图例表解析(符号↔构件)
        '标高': elevs_info,
        '标高参数': elev_params if 'elev_params' in dir() else {},
        '图块明细': blocks_detail,
        '标注关联': dim_matches if 'dim_matches' in dir() else [],
        '构件尺寸推导': member_sizes if 'member_sizes' in dir() else {},
        '剖面算量': section_qty if 'section_qty' in dir() else [],
    }

    # v6.3 B1: 设计说明文字做法 → 构造层补充(表格做法表缺失时的兜底 + 补充)
    try:
        dn = pid.get('设计说明') or {}
        for layer_info in dn.get('做法层次') or []:
            name = layer_info.get('名称', '')
            layers_ = layer_info.get('层次', [])
            if not name or not layers_:
                continue
            # 生成构造层: 名称=部位+末层做法, 材料=末层(最具体)
            last = layers_[-1] if layers_ else ''
            existing_names = [l.get('名称', '') for l in (pid.get('构造层') or [])]
            if name in existing_names:
                continue
            pid.setdefault('构造层', []).append({
                '名称': f'{name} {last}', '厚度_mm': None, '材料': last,
                '部位': name, '厚度来源': '设计说明做法',
            })
    except Exception:
        pass

    # v6.3 C4: 三源面积核对(图签/文字 vs 设计说明概况 vs 几何闭合) → 图纸问题候选
    try:
        dn = pid.get('设计说明') or {}
        prof_area = (dn.get('工程概况') or {}).get('建筑面积')
        if prof_area:
            used_area = 0.0
            for a in pid.get('面积区域', []):
                used_area = max(used_area, float(a.get('面积_m2', 0) or 0))
            if used_area > 0:
                p_area = float(prof_area)
                ratio = used_area / p_area if p_area else 0
                if not (0.85 <= ratio <= 1.15):
                    pid.setdefault('图纸问题候选', []).append(
                        f'[面积核对] 设计说明建筑面积({p_area:.0f}m²)与识图采用面积({used_area:.0f}m²)差异{ratio:.0%}, 需人工确认')
    except Exception:
        pass

    # v6.3: 设计意图推理(全局理解/算量边界/参数推断) — 依赖上方完整 pid
    try:
        from intent_engine import infer_design_intent
        intent = infer_design_intent(pid)
        if intent.get('参数推断') or intent.get('算量边界', {}).get('含拆除') is not None:
            print(f"  设计意图: 边界[含拆除={intent['算量边界'].get('含拆除')}] 参数推断{len(intent['参数推断'])}条")
        pid['设计意图'] = intent
    except Exception:
        pid['设计意图'] = {}

    try:
        cad = cad_analysis(dwg_file, insunits)
        pid['CAD分析'] = cad
        pid['图块'] = cad['blocks']
        pid['管道总长_m'] = cad['total_pipe_len']
        pid['标注尺寸'] = cad['dims']
        pid['图纸比例'] = cad['scale']
        # 将按管径分类的管线明细转为线性构件
        pipes_by_dia = (cad.get('pipes') or {}).get('按管径', {})
        dia_has_length = any(info.get('长度_m', 0) > 0 for info in pipes_by_dia.values())
        # v5.3: 有管径明细时不再追加聚合条目 — 'CAD提取管线'(总长)与
        # 管径分类明细是同一批线段, 双写导致算量管道量翻倍(真实图纸实测)
        if cad.get('total_pipe_len',0) > 0 and not dia_has_length:
            pid['线性构件'].append({'名称':'CAD提取管线','类型':'管道','长度_m':cad['total_pipe_len'],'来源':'图层/线段识别'})
        for key, info in pipes_by_dia.items():
            if info.get('长度_m', 0) > 0:
                pid['线性构件'].append({'名称':key,'类型':'管道','长度_m':info['长度_m'],'管径':info.get('管径',''),'系统':info.get('系统',''),'来源':'管径分类识别'})
        # v5.10: 电气线缆(天正中文层) — 单独进'电气线缆', 不混入给排水'管道'
        elec_by_sys = (cad.get('pipes') or {}).get('电气线缆', {})
        pid['电气线缆'] = [{'名称': key, '长度_m': info['长度_m'], '系统': info.get('系统', ''),
                           '图层': info.get('图层', ''), '来源': '图层/线段识别'}
                          for key, info in elec_by_sys.items() if info.get('长度_m', 0) > 0]
        print(f'  CAD: 图块{cad["blocks"].get("total_blocks",0)}个 管道{cad["total_pipe_len"]}m 苗木{cad["tree_count"]}株 标注{len(cad.get("dims",[])) if isinstance(cad.get("dims"),list) else cad.get("dims")}条 比例{cad["scale"]}')
    except Exception as e:
        print(f'  CAD深度分析: {e}')

    if specialty == '房屋建筑与装饰工程':
        pid['建筑信息'] = {}
        if pid.get('CAD分析'):
            pid['建筑信息']['构件分类'] = pid['CAD分析']['elem'].get('构件分类', {})
            pid['建筑信息']['构件尺寸样本'] = pid['CAD分析']['elem'].get('构件尺寸样本', {})
            pid['建筑信息']['标注关联'] = pid['CAD分析']['elem'].get('标注关联', [])
            pid['建筑信息']['图块'] = pid['CAD分析']['blocks']
    elif specialty == '安装工程':
        # v4.1.5: 图块明细优先(含属性/嵌套), 旧版 blocks 兜底
        bd = pid.get('图块明细', {})
        blocks = pid.get('CAD分析',{}).get('blocks',{}) if pid.get('CAD分析') else {}
        def _count(cat):
            items = bd.get(cat, [])
            if items:
                # v5.10: 加密块名可读化 — 图层名语义优先(天正HC编码块)
                from block_enhanced import readable_name
                out = {}
                for i in items:
                    disp = readable_name(i.get('name',''), i.get('layer',''), cat)
                    out[disp] = out.get(disp, 0) + i['count']
                return out
            return blocks.get({'阀门': 'valve_blocks', '灯具': 'light_blocks',
                               '开关插座': 'switch_blocks', '配电箱柜': 'panel_blocks',
                               '卫生器具': 'sanitary_blocks', '消防设施': 'fire_blocks',
                               '设备': 'equip_blocks'}.get(cat, ''), {})
        pid['安装信息'] = {
            '管道': [{'名称':i['名称'],'长度_m':i['长度_m'],'管径':i.get('管径',''),'系统':i.get('系统','')} for i in pid.get('线性构件',[]) if i.get('类型')=='管道'],
            # v5.10: 电气线缆 → '电缆' (calc_mep 消费键, 配 030902001 电缆敷设定额);
            # 不填 '管线' — 同一线缆不能既算电缆又算配管, 避免双计
            '电缆': [{'型号': i.get('系统','') + '线缆', '长度_m': i['长度_m']} for i in pid.get('电气线缆', [])],
            '设备': _count('设备'),
            '阀门': _count('阀门'),
            '灯具': _count('灯具'),
            '开关插座': _count('开关插座'),
            '配电箱柜': _count('配电箱柜'),
            '卫生器具': _count('卫生器具'),
            '消防设施': _count('消防设施'),
        }
        total_install = sum(sum(d.values()) for d in [v for v in pid['安装信息'].values() if isinstance(v, dict)] if d)
        print(f'  安装: 管道{len(pid["安装信息"]["管道"])}类 设备{total_install}个')
    elif specialty == '园林绿化工程':
        # v4.5: 乔木/灌木按块名分流, 不再全部计入乔木
        blocks = pid.get('CAD分析',{}).get('blocks',{}) if pid.get('CAD分析') else {}
        tb = blocks.get('tree_blocks', {}) if isinstance(blocks, dict) else {}
        tc = 0
        sc = 0
        if isinstance(tb, dict):
            for name, cnt in tb.items():
                if any(k in name for k in ['灌木', '灌木丛', 'shrub', 'SHRUB', '绿篱', '地被']):
                    sc += cnt
                else:
                    tc += cnt
        if tc == 0 and sc == 0:
            for t in raw_texts:
                m = re.search(r'(\d+)\s*株', t)
                if m:
                    tc = int(m.group(1))
                    break
        hard = blocks.get('hardscape_blocks', {}) if isinstance(blocks, dict) else {}
        pid['园林信息'] = {
            '苗木': {
                '乔木': [{'名称':'CAD识别','数量':tc}] if tc > 0 else [],
                '灌木': [{'名称':'CAD识别','数量':sc}] if sc > 0 else [],
            },
            '硬景': {
                '铺装_m2': total_area if '铺装' in ''.join(raw_texts) else 0,
                '路缘石_m': sum(i.get('长度_m',0) for i in pid.get('线性构件',[]) if '缘石' in i.get('名称','')),
            },
            '园林设施': hard,
        }
    elif specialty == '钢结构工程':
        from steel_weight import extract_steel_from_texts
        mems = extract_steel_from_texts(raw_texts)
        pid['钢结构'] = {'构件': mems}
        if mems: print(f'  钢结构: 识别{len(mems)}个构件')

    # ── 标高参数写入: 挖深/层高 供算量层使用 ──
    if pid.get('标高参数'):
        pid['算量参数'] = pid['标高参数']

    # ── v5.0 P1: 构件级建模 — 特征列表 → 构件对象(仅房建; 其余专业输出空骨架) ──
    try:
        from component_model import build_component_model
        pid['构件模型'] = build_component_model(pid, _msp)
    except Exception as e:
        pid['构件模型'] = {'柱': [], '梁': [], '板': [], '墙': [], '房间': []}
        print(f'  构件建模失败(兜底空): {e}')

    # ── v5.15: 视觉路径 — 识图必经视觉, 与其他识图逻辑交叉验证(用户确认) ──
    # 渲染 → 视觉识别 → 交叉验证 → 写入 pid['视觉识别'] + pid['视觉验证']
    # 任何失败静默降级, 绝不影响几何主流程; VISION_OFF=1 可临时关闭
    try:
        from vision_fusion import run_vision_for_drawing
        run_vision_for_drawing(pid, dwg_file, output_dir)
    except Exception as e:
        print(f'  ⚠ 视觉路径接入异常(跳过): {e}')

    # ── v5.15 V-4: 栅格图(扫描件)OCR 兜底 — 检测到栅格化时补充文字识别 ──
    try:
        from raster_ocr import is_raster_drawing, ocr_fallback
        raster, ents, imgs = is_raster_drawing(dwg_file)
        if raster:
            print(f'  ⚠ 检测到栅格化图纸(实体{ents}个), 走 OCR 兜底')
            ocr_out = ocr_fallback(dwg_file, output_dir)
            if ocr_out:
                ocr_texts = ocr_out.get('文字') or [] if isinstance(ocr_out, dict) else (ocr_out or [])
                pid['OCR文字'] = ocr_texts
                # v6.0 P5: 栅格图视觉结构化信息(工程类型/主要构件) → 供交叉验证
                if isinstance(ocr_out, dict):
                    pid['OCR视觉'] = {'工程类型': ocr_out.get('工程类型', ''),
                                    '主要构件': ocr_out.get('主要构件', '')}
                # 补充进施工说明(供专业识别/工程性质判定消费)
                existing = set(pid.get('施工说明', []) or [])
                new_texts = [t for t in ocr_texts if t and t not in existing]
                pid['施工说明'] = (pid.get('施工说明', []) or []) + new_texts[:50]
                print(f'  OCR 兜底: 补充 {len(new_texts)} 条文字')
    except Exception as e:
        print(f'  ⚠ OCR 兜底异常(跳过): {e}')

    json_path = os.path.join(output_dir, '识图结果.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(pid, f, ensure_ascii=False, indent=2)
    print(f'  输出: {json_path}')
    return pid


if __name__ == '__main__':
    if len(sys.argv) < 2: print('用法: step1_recognize.py 图纸.dxf')
    else: run(sys.argv[1], os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output'))

