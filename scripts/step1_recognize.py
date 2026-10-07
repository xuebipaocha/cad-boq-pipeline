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

# ── v5.4 工程性质判定: 新建 / 改造 ──
# 改造信号关键词(施工说明中的关键词, 权重为出现频次倍率)
# 注: 工程性质对外只有「新建 / 改造」两类; 表中的"大修"是**同义词线索**——
# 图纸写"本工程为大修"仍是判为"改造"的强信号(权重 3)。
# 注意: '修改' 不入词表 — 它是图纸修订记录(2026.2.26修改), 非工程改造信号
RENOVATION_KEYWORDS = {
    '拆除': 3, '维修': 2, '更换': 2, '大修': 3, '翻新': 2,
    '改造': 2, '既有': 1, '原有': 1, '加固': 2, '恢复': 1,
}
# v6.10.4 性质词(项目性质强信号): 只有命中这些词才判"改造"。
# 实测教训(4 份真实图): 渔轮办公楼仅"维修×1 + 更换×2"= 6 分即被判改造 —— 但"维修/更换/拆除"
# 是**工序词**, 通用说明/规范条文里的这类词不代表项目性质; 真改造项目(老涂装 改造×11、
# 船体大楼 大修×2+改造×2)均命中性质词。故: 工序词分≥4 且命中性质词 → 改造; 否则新建。
NATURE_WORDS = ('改造', '大修', '翻新', '修缮', '改建', '扩建', '拆除重建')
NEW_BUILD_KEYWORDS = {'新建': 3, '首建': 2, '施工图': 1}


# v6.10.6 路径4 大图性能: 同一图纸在一次流程内**只读一次**。
# cProfile 实测(新图 000008, 26279 实体): 全程 278.6s 中 readfile 占 140.5s —— 被重复读取
# **8 次** × 17.5s/次(面积裁决/房间/表格/扩展实体/标注关联各读一遍)。改为进程级缓存后,
# 大图可省约 2 分钟。流程对文档只读(不修改), 故缓存安全。
import os as _os_perf
import ezdxf as _ezdxf_perf

_ORIG_READFILE = _ezdxf_perf.readfile
_READFILE_CACHE = {}


def _cached_readfile(filename, *args, **kwargs):
    try:
        key = _os_perf.path.abspath(str(filename))
    except Exception:
        return _ORIG_READFILE(filename, *args, **kwargs)
    if key not in _READFILE_CACHE:
        _READFILE_CACHE[key] = _ORIG_READFILE(filename, *args, **kwargs)
    return _READFILE_CACHE[key]


_ezdxf_perf.readfile = _cached_readfile


def _collect_all_texts(msp, _depth=3):
    """v6.10.7 **全量看图**: 汇总图纸所有文字源 —— 图纸上每个字都可能算量/编清单的依据。

    覆盖: ①模型空间 TEXT ②MTEXT ③DIMENSION 标注文本(含测量值) ④INSERT 块内 TEXT/MTEXT/ATTRIB
    ⑤嵌套块(递归, 深度≤_depth)。实测 000007: 原只读到 356 条(局部注释), 而全图文字 4848 条
    (DIMENSION 1836 条 + 块内 170 条**完全未读**) → 覆盖率仅 7%, 平法标注(KL1(1) 300*450)漏读。
    返回 [{'文本','来源','图层','x','y'}]。
    """
    out = []

    def _scan_block(blk, depth, tag):
        if blk is None or depth > _depth:
            return
        for en in blk:
            t = en.dxftype()
            try:
                if t == 'TEXT':
                    out.append({'文本': en.dxf.text, '来源': tag + '块', '图层': en.dxf.layer,
                                'x': float(en.dxf.insert.x), 'y': float(en.dxf.insert.y)})
                elif t == 'MTEXT':
                    out.append({'文本': en.text, '来源': tag + '块M', '图层': en.dxf.layer,
                                'x': float(en.dxf.insert.x), 'y': float(en.dxf.insert.y)})
                elif t == 'ATTRIB':
                    out.append({'文本': en.dxf.text, '来源': tag + '属性', '图层': en.dxf.layer,
                                'x': float(en.dxf.insert.x), 'y': float(en.dxf.insert.y)})
                elif t == 'INSERT':
                    _scan_block(msp.doc.blocks.get(en.dxf.name), depth + 1, tag + '嵌')
            except Exception:
                continue

    for e in msp:
        t = e.dxftype()
        try:
            if t == 'TEXT':
                out.append({'文本': e.dxf.text, '来源': '模型', '图层': e.dxf.layer,
                            'x': float(e.dxf.insert.x), 'y': float(e.dxf.insert.y)})
            elif t == 'MTEXT':
                out.append({'文本': e.text, '来源': '模型M', '图层': e.dxf.layer,
                            'x': float(e.dxf.insert.x), 'y': float(e.dxf.insert.y)})
            elif t == 'DIMENSION':
                try:
                    txt = e.dxf.text
                except Exception:
                    txt = ''
                if not txt or txt == '<>':
                    try:
                        txt = str(round(float(e.get_measurement()), 1))
                    except Exception:
                        txt = ''
                _p = getattr(e.dxf, 'defpoint', None)
                out.append({'文本': txt, '来源': '标注', '图层': e.dxf.layer,
                            'x': float(_p.x) if _p else 0.0, 'y': float(_p.y) if _p else 0.0})
            elif t == 'INSERT':
                _scan_block(msp.doc.blocks.get(e.dxf.name), 1, '')
        except Exception:
            continue
    return out


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
    """工程性质判定: **新建 / 改造** 两类(v6.10.1 用户口径)。

    加权打分: 改造信号(拆除/维修/更换/翻新/修缮...) vs 新建信号。
    判定规则: 改造总分 ≥4 才判"改造"(保守阈值, 弱信号如'更换×1'默认新建 —
    避免带修订记录的新建设计图误判); 图纸文字中的"大修"作为改造的**同义线索词**保留。
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
    nature_hits = [w for w in NATURE_WORDS if w in hay]
    if ren_score >= 4 and nature_hits:
        return NATURE_RENO, {'分数': ren_score, '证据': ren_hits,
                             '新建分数': new_score, '性质词': nature_hits}
    return NATURE_NEW, {'分数': new_score, '证据': new_hits, '改造分数': ren_score,
                        '工序词分': ren_score,
                        '未判改造原因': (None if ren_score < 4 else '无性质词(改造/大修/翻新/修缮…)')}


# ── v6.10.1 工程性质两类口径(用户确认: 只有新建 / 改造) ──
NATURE_NEW = '新建'
NATURE_RENO = '改造'
# 历史取值归一: 旧版本输出 '大修与改造'; 图纸/文档中也可能写 '大修'/'改建'
NATURE_ALIASES = {'大修与改造': NATURE_RENO, '大修': NATURE_RENO, '改建': NATURE_RENO}


def normalize_nature(v):
    """任意历史工程性质取值 → 「新建 / 改造」两类(未知值原样返回)。

    用途: 消费旧 pid JSON(可能含 '大修与改造')时先归一, 避免分支判定失效。
    """
    s = str(v or '').strip()
    return NATURE_ALIASES.get(s, s)


def detect_specialty_detail(layers, texts):
    hay = (' '.join(layers) + ' ' + ' '.join(texts)).lower()
    # v6.10.6 fx1 图名优先: 图名(如"一层给排水及消防平面布置图")是专业最强信号, 但被数千条
    # 说明文字稀释 —— 房建词表含 建筑/结构/柱/梁/板 等**通用词**, 在任何图的说明里都反复出现,
    # 导致给排水图被判"房屋建筑与装饰工程"(实测新图 000006: 0.433)。故命中图名的专业词权重 ×3。
    title_hay = ' '.join(t for t in texts if t and '图' in t and 3 <= len(t) <= 30).lower()
    scores = {}
    for sp, kws in SPECIALTY_KEYWORDS.items():
        score = 0
        hits = []
        for kw, w in kws.items():
            if kw.lower() in hay:
                score += w; hits.append(kw)
            if kw.lower() in title_hay:
                score += w * 3; hits.append(kw + '(图名)')
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
    # v6.10.6 ②多图幅混排并算: 一份图纸常含多张平面(一层/二层/三层), 面积应为**各层之和**,
    # 而原逻辑取"最大单个闭合区域" → 实测 40/51.4/66.3/77.4/47m² 明显偏小。
    # 判据: 候选面积降序, 与最大值同量级(≥1/3)者视为同图多图幅; 该类簇之和显著大于单个
    # 区域(>1.5 倍)时采用之和。只有一个主导区域时不动 —— 避免把一张小详图当"楼层"叠加。
    try:
        _cand = sorted([p.get('area_m2', 0) for p in poly_areas if (p.get('area_m2') or 0) > 1],
                       reverse=True)
        if len(_cand) >= 2:
            _top = _cand[0]
            _cluster = [a for a in _cand if a >= _top / 3.0]
            # v6.10.6 收紧: 需 **≥3 个**同量级区域才判多图幅 —— 只有 2 个时更可能是
            # "主区域 + 附属区域(楼梯/雨棚)", 求和会把单层图算成双层(基准用例板/墙 err=100% 回归)。
            # v6.10.6 ②多图幅**自动判定（文字证据驱动）**: 几何无法区分"多图幅"与"主区域+附属"
            # （纯几何判据曾使基准用例房建B 板/墙 err=117%）。改用图内**"平面"标题数**作证据:
            # 实测新图(一层/二层/三层…平面图) 标题 12~39 个, 基准单图幅用例 **0 个** → 天然可分。
            # AREA_MULTI_VIEW=1 可强制启用; 未设时按文字证据自动判定。
            _n_plan = 0
            try:
                _n_plan = sum(1 for c in (result.get('text_clusters') or [])
                              if '平面' in str(c.get('text', '')))
            except Exception:
                pass
            # 层数去重(一/二/三/四 或 1/2/3/4): 几何常只测到部分楼层 → 用层数折算总面积
            _floors = set()
            try:
                for _c in (result.get('text_clusters') or []):
                    _t = str(_c.get('text', ''))
                    if '平面' in _t:
                        _m = re.search(r'([一二三四五六七八九十0-9]{1,3})\s*层', _t)
                        if _m:
                            _floors.add(_m.group(1))
            except Exception:
                pass
            if (os.environ.get('AREA_MULTI_VIEW', '') == '1' or _n_plan >= 2):
                notes.append(f'多图幅证据: 图内"平面"标题 {_n_plan} 个, 楼层 {sorted(_floors)}')
                _sum_a = sum(_cluster) if _cluster else 0
                if len(_cluster) >= 2 and area and _sum_a > area * 1.5:
                    notes.append(f'多图幅并算: {len(_cluster)} 个同量级区域之和 {_sum_a:.1f}m² '
                                 f'(单区域最大 {area:.1f}m²) — 同图含多张平面/分区')
                    area, source = round(_sum_a, 2), '多图幅区域求和'
                elif area and len(_floors) >= 2 and area < 200:
                    # 几何只测到单层(其余层未闭合) → 按层数折算; **标估算**(不冒充实测, 供人工核)
                    _est = round(area * len(_floors), 2)
                    notes.append(f'多图幅折算(估算): 单层 {area:.1f}m² × {len(_floors)} 层 = {_est:.1f}m² '
                                 f'(图内"平面"标题 {_n_plan} 个; 层面积未逐层闭合, 需人工复核)')
                    area, source = _est, '多图幅按层数折算(估算)'
    except Exception:
        pass
    # v6.10.5: 多图幅混排 → 面积存疑(4 份真实图实测: 基础图 40m² / 办公楼 51m² / 外立面图 66m²
    # 明显失真 —— 根因是"最大闭合多段线"在多图幅/详图混排图纸上取到的不是主平面)。
    # 判据: 采用值偏小(<200m²) 且图内存在 ≥3 个同量级(≥其 30%)闭合区域 → 判多图幅混排, 面积待核。
    # 只标存疑 + 列候选, **不擅改数值**(避免引入错误量)。
    if area and area < 200 and len(poly_areas) >= 3:
        near = [p.get('area_m2', 0) for p in poly_areas if p.get('area_m2', 0) >= area * 0.3]
        if len(near) >= 3:
            notes.append(
                f'面积存疑: 采用值 {area:.0f}m² 偏小, 图内有 {len(near)} 个同量级闭合区域'
                f'(最大 {max(all_areas) if all_areas else 0:.0f}m²) — 疑为多图幅/详图混排, '
                f'主区域需人工确认')
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

    # ── v5.4 工程性质判定: 新建 / 改造 ──
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
            table_layers_seen = set()
            table_desc_rows = []  # v6.10.5: 描述型做法表文字(无构造层列时收此)  # v6.6: 多做法表累加去重(原为覆盖 — 多表图纸只剩最后一张)
            for tb in tables_info:
                if tb['type'] == '做法表':
                    layers_from_table = table_to_layers(tb)
                    if not layers_from_table and tb.get('rows'):
                        # v6.10.5 描述型做法表兜底: 表头无 编号/厚度/材料 列(如三列同名
                        # '建筑材料做法'、列被竖排文字切碎) → 构造层产不出; 此时把文字行
                        # 收作'做法说明'素材(供特征/组价引用), **不编造构造层**。
                        for _r in (tb.get('rows') or [])[:30]:
                            _cells = _r.get('cells') if isinstance(_r, dict) else _r
                            _txt = ' '.join(str(c) for c in (_cells or []) if c).strip()
                            if len(_txt) >= 6:
                                table_desc_rows.append(_txt[:140])
                                # v6.10.6 未修项落地: 描述文字 → 构造层反推(材料/厚度)
                                # 让描述型做法表也能参与算量; 去重后并入 construction_layers,
                                # 并标来源"做法说明文字反推"(可审计, 不是凭空构造层)
                                import re as _re
                                _mats = [m for m in ('无机涂料', '乳胶漆', '腻子', '自流平', '木地板',
                                                     '地砖', '石材', '防水', '砂浆', '混凝土', '面砖',
                                                     '踢脚', '吊顶', '保温', '涂料') if m in _txt]
                                if _mats:
                                    _th = _re.search(r'(\d+)\s*(?:mm\s*厚|厚)|厚\s*(\d+)', _txt)
                                    _thv = int(_th.group(1) or _th.group(2)) if _th else None
                                    _mat = '、'.join(_mats[:2])
                                    if not any(l.get('材料') == _mat and l.get('厚度_mm') == _thv
                                               for l in construction_layers):
                                        construction_layers.append({
                                            '名称': _txt[:24], '材料': _mat,
                                            '厚度_mm': _thv, '来源': '做法说明文字反推'})
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
                                # v6.9.3: 材质提取(组价按材质匹配定额) — 门窗表备注列
                                # ('钢质门'/'塑钢门'/'塑钢窗') → 材质字段
                                mat_words = [w for w in ('钢质', '塑钢', '木质', '铝合金', '断桥铝',
                                                         '推拉', '平开', '防火', '玻璃', '复合')
                                             if w in row_text]
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
                                        '材质': '、'.join(mat_words) if mat_words else '',
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
        '工程性质': nature,  # v5.4: 新建 / 改造
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
        '做法说明': table_desc_rows,  # v6.10.5: 描述型做法表文字(未产出构造层时不丢内容)
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

    # v6.10.7 全量看图: 汇总所有文字源(TEXT/MTEXT/标注/块内/属性/嵌套块) —— 零遗漏
    try:
        pid['全图文字'] = _collect_all_texts(_msp)
        print('  全图文字: %d 条(含标注/块内/属性/嵌套块)' % len(pid['全图文字']))
    except Exception as _e_ta:
        print('  ⚠ 全量文字收集失败: %s' % _e_ta)

    # v6.10.7 **建筑面积按 GB/T 50353 规则计算（图幅分组路径）** — 替代"读图签"与"×层数":
    # ①识别图框(图层含 PUB_TITLE/图框/FRAME 的闭合矩形) ②闭合轮廓按中心点分入所属图框
    # ③每幅取**主轮廓**(该幅最大轮廓, 排除图框自身) = 该幅楼层平面
    # ④各幅主轮廓**求和** = 建筑面积(逐层) —— 这解决"多图幅混排时几何取到剖面/详图轮廓"的根因。
    # 层高/半算判定需标高证据, 未取得时按全面积并注明"未做半算判定"; 计算失败则回退存疑提示。
    try:
        import ezdxf as _ex8
        _doc8 = _ex8.readfile(dwg_file)
        _frs, _polys8 = [], []
        for _e8 in _doc8.modelspace():
            if _e8.dxftype() != 'LWPOLYLINE' or not _e8.closed:
                continue
            try:
                _pt8 = [(float(p[0]), float(p[1])) for p in _e8.get_points('xy')]
            except Exception:
                continue
            if len(_pt8) < 4:
                continue
            _xs8 = [p[0] for p in _pt8]
            _ys8 = [p[1] for p in _pt8]
            _bb8 = (min(_xs8), min(_ys8), max(_xs8), max(_ys8))
            _a8 = 0.0
            for _i8 in range(len(_pt8)):
                _x1, _y1 = _pt8[_i8]
                _x2, _y2 = _pt8[(_i8 + 1) % len(_pt8)]
                _a8 += _x1 * _y2 - _x2 * _y1
            _am2 = abs(_a8) / 2.0 / 1e6          # mm² → m²
            _lay8 = str(_e8.dxf.layer or '').upper()
            if any(k in _lay8 for k in ('PUB_TITLE', '图框', 'FRAME')):
                if (_bb8[2] - _bb8[0]) > 1000 and (_bb8[3] - _bb8[1]) > 1000:
                    _frs.append(_bb8)
                    continue
            if _am2 >= 5:
                _polys8.append((_bb8, _am2))
        if _frs and _polys8:
            _groups = {}
            for _bb8, _am2 in _polys8:
                _cx = (_bb8[0] + _bb8[2]) / 2.0
                _cy = (_bb8[1] + _bb8[3]) / 2.0
                for _fi8, _f8 in enumerate(_frs):
                    if _f8[0] <= _cx <= _f8[2] and _f8[1] <= _cy <= _f8[3]:
                        _groups.setdefault(_fi8, []).append(_am2)
                        break
            _mains = [max(_v) for _v in _groups.values() if _v]
            if len(_mains) >= 2:                 # ≥2 幅各有主轮廓 → 多图幅
                _sum8 = round(sum(_mains), 2)
                # v6.10.7 务实收敛: **只作参考, 不替换采用值** —— 实测各幅"主轮廓"往往仍是
                # 详图/剖面轮廓(000008 得 112.48m² vs 真实 2411.48m²), 且替换会误伤单图幅
                # 用例(基准 100%→83%)。按"错误结果比缺失更坏"原则: 记录证据供人工/后续算法用,
                # 采用值仍走"图签标注 > 几何 > 待核"。
                pid['图幅面积参考'] = {
                    '幅数': len(_mains), '各幅主轮廓_m2': [round(m, 1) for m in _mains[:10]],
                    '求和_m2': _sum8, '说明': '按图框分组的主轮廓求和(未替换采用值, 仅供人工核)'}
                print(f'  建筑面积(规则计算·图幅分组参考): {len(_mains)} 幅求和 = {_sum8}m²'
                      f'(仅记录, 采用值不变)')
        # ── v6.10.7 外墙(WALL)轮廓聚类 → 单层面积 → 建筑面积（GB/T 50353 规则路径）──
        # 动机: 图幅分组取到的"主轮廓"仍是详图轮廓(000008 得 112m² vs 真值 2411.48m²);
        # 改从 **WALL 图层**取外墙范围: 点按网格聚类 → 取"合理楼层面积"(60~3000m²)簇的
        # 中位数作单层面积 → × 层数(图内"X层平面"最高层号) = 建筑面积(逐层求和)。
        # 实测 000008: 聚类得 592.8m²(≈单层真值 591.2m²) → ×4 层 ≈ 2371m²(真值 2411.48, 差1.7%)。
        # **只对多图幅混排图启用**（幅数≥2）: 单图幅图几何本就可靠, 不改口径 → 基准用例不受影响。
        if (pid.get('图幅面积参考') or {}).get('幅数', 0) >= 2:
            import re as _re9
            _hay9 = str(pid.get('设计说明') or '') + str(pid.get('局部注释') or '')
            _CN9 = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            _fs9 = set(_re9.findall(r'([一二三四五六七八九十0-9]{1,3})\s*层\s*(?:平面|布置图)', _hay9))
            _ns9 = [_CN9.get(_v9, int(_v9) if str(_v9).isdigit() else 0) for _v9 in _fs9]
            _fl9 = max([_n for _n in _ns9 if 0 < _n <= 60] or [0])
            _wp = []
            for _e9 in _doc8.modelspace():
                if str(_e9.dxf.layer or '').upper() not in ('WALL', '墙', 'WALLS'):
                    continue
                try:
                    if _e9.dxftype() == 'LWPOLYLINE':
                        _wp += [(float(_p[0]), float(_p[1])) for _p in _e9.get_points('xy')]
                    elif _e9.dxftype() == 'LINE':
                        _wp += [(_e9.dxf.start.x, _e9.dxf.start.y),
                                (_e9.dxf.end.x, _e9.dxf.end.y)]
                except Exception:
                    pass
            if _wp and _fl9 >= 2:
                _G = 15000.0
                _cells = {}
                for _x9, _y9 in _wp:
                    _cells.setdefault((int(_x9 // _G), int(_y9 // _G)), []).append((_x9, _y9))
                _seen9, _areas9 = set(), []
                for _c9 in list(_cells):
                    if _c9 in _seen9:
                        continue
                    _stk, _comp = [_c9], []
                    _seen9.add(_c9)
                    while _stk:
                        _cur = _stk.pop()
                        _comp.append(_cur)
                        for _dx9 in (-2, -1, 0, 1, 2):
                            for _dy9 in (-2, -1, 0, 1, 2):
                                _nb = (_cur[0] + _dx9, _cur[1] + _dy9)
                                if _nb in _cells and _nb not in _seen9:
                                    _seen9.add(_nb)
                                    _stk.append(_nb)
                    _p9 = [_pp for _cc in _comp for _pp in _cells[_cc]]
                    _xs9 = [_pp[0] for _pp in _p9]
                    _ys9 = [_pp[1] for _pp in _p9]
                    _a9 = (max(_xs9) - min(_xs9)) * (max(_ys9) - min(_ys9)) / 1e6
                    if 60 <= _a9 <= 3000:
                        _areas9.append(_a9)
                if _areas9:
                    _areas9.sort()
                    _mid9 = _areas9[len(_areas9) // 2]
                    _est9 = round(_mid9 * _fl9, 2)
                    _ar9 = pid.get('面积区域') or []
                    if _ar9:
                        _old9 = _ar9[0].get('面积_m2')
                        _ar9[0]['面积_m2'] = _est9
                        _ar9[0]['面积来源'] = '外墙轮廓聚类×层数(GB/T 50353)'
                        _ar9[0]['备注'] = (str(_ar9[0].get('备注') or '') +
                                         f'；WALL 聚类单层 {_mid9:.1f}m²(合理簇 {len(_areas9)} 个)'
                                         f' × {_fl9} 层; 原采用 {_old9}m²')
                        print(f'  建筑面积(规则计算): WALL 轮廓单层 {_mid9:.1f}m² × {_fl9} 层 '
                              f'= {_est9}m² (原采用 {_old9}m²)')
    except Exception as _e8:
        print(f'  ⚠ 图幅分组面积计算失败(跳过): {_e8}')
    # 面积按"层数折算"校正。证据源用 pid['设计说明']（实测新图 000008 含"平面"18 次、
    # 000006 19 次; 基准单图幅用例 0 次 → 天然可分, 不误触发)。
    # 只在"采用面积偏小(<200m²)"时折算, 且**标"估算"**（不冒充实测, 供人工复核）。
    try:
        _ar = (pid.get('面积区域') or [{}])
        _a0 = (_ar[0].get('面积_m2') or 0) if _ar else 0
        _hay = str(pid.get('设计说明') or '') + str(pid.get('局部注释') or '')
        _n_plan2 = _hay.count('平面')
        if _n_plan2 >= 2 and _a0 and _a0 < 200:
            import re as _re3
            _fl = set(_re3.findall(r'([一二三四五六七八九十0-9]{1,3})\s*层\s*(?:平面|布置图|图)', _hay))
            # 层数取**最大楼层号**而非去重计数（实测 000008 去重得 9 个值 → 明显过估;
            # 其平面标题实为 一/二/三层平面图 → 3 层）
            _CN2 = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            _nums = [_CN2.get(_x, int(_x) if str(_x).isdigit() else 0) for _x in _fl]
            _n_fl2 = max([n for n in _nums if 0 < n <= 60] or [0])
            if _n_fl2 >= 2:
                # v6.10.7 修正: **不再按层数折算** —— 实测 000008 几何单层 47m² × 4 = 188m²,
                # 而真实建筑面积 2411.48m²（591.2×4 层 + 53.56 屋面）: 几何测到的"单层区域"
                # 本身不完整, 折算法把残缺小区域成倍放大 → 产出错误量。
                # 改为**标存疑 + 列证据**, 不擅改数值（诚实）; 面积正解是"读图内建筑面积标注"
                # （units.resolve_area 已补 'S=' 规则, 实测可命中 000008 的 S=2411.48m）。
                _ar[0]['备注'] = (str(_ar[0].get('备注') or '') +
                                  f'；多图幅证据: 图内"平面"标题 {_n_plan2} 个, 最高层 {_n_fl2} 层'
                                  f' — 采用面积可能仅覆盖单层局部, 需人工复核')
                print(f'  面积多图幅存疑: 采用 {_a0:.1f}m² 而图含 {_n_fl2} 层'
                      f'(图内"平面"标题 {_n_plan2} 个) — 面积需人工复核')
    except Exception as _e:
        print(f'  ⚠ 面积多图幅校正失败(跳过): {_e}')

    # v6.10.6 fx2 图例表 → 符号映射: 给排水/电气图的核心信息(W1=生活给水管道、RJ=热水给水管道、
    # 地漏、大便器水箱、淋浴喷头、闸阀…), 此前被解析成"设备表"后**整段丢弃** —— 现提取
    # 符号↔名称 对写入 pid['图例'], 作为安装专业(管道/器具)算量的入口, 也供专业判据复用。
    try:
        _legend = {}
        for _tb in (pid.get('表格') or []):
            _h = ' '.join(str(x) for x in (_tb.get('headers') or []))
            _body = str(_tb.get('rows') or '')[:600]
            if ('图例' in _h) or ('图例' in _body and '名称' in (_h + _body)):
                for _r in (_tb.get('rows') or []):
                    _cs = [str(c).strip() for c in
                           ((_r.get('cells') if isinstance(_r, dict) else _r) or [])
                           if str(c).strip()]
                    for _i in range(0, len(_cs) - 1, 2):
                        _sym, _nm = _cs[_i], _cs[_i + 1]
                        if 1 <= len(_sym) <= 8 and 2 <= len(_nm) <= 40 and _sym not in _legend:
                            _legend[_sym] = _nm
        if _legend:
            pid['图例'] = _legend
            print(f'  图例表: {len(_legend)} 项 (如 {list(_legend.items())[:3]})')
    except Exception as _e:
        print(f'  ⚠ 图例表解析失败(跳过): {_e}')

    # v6.10: 扩展实体解析(多线墙/样条/填充/引线/真表格/天正专业对象) — 此前全项目零处理
    try:
        from dxf_entities import extract_all
        ext = extract_all(_msp) if _msp is not None else {}
        if ext:
            pid['扩展实体'] = ext
            es = ext.get('summary') or {}
            print(f"  扩展实体: 多线墙{es.get('多线墙', 0)} 样条{es.get('样条曲线', 0)} "
                  f"填充{es.get('填充区域', 0)} 引线{es.get('引线标注', 0)} "
                  f"真表格{es.get('真表格', 0)} 天正对象{es.get('天正对象', 0)}")
            # 天正专业对象 → 图纸问题清单提示(几何语义丢失, 建议导出 T3/普通实体格式)
            px = ext.get('proxy') or {}
            if px.get('count'):
                lays = list((px.get('layers') or {}).keys())[:3]
                pid.setdefault('图纸问题候选', []).append(
                    f"[天正专业对象] 检测到 {px['count']} 个 ACAD_PROXY_ENTITY(图层: {lays}) — "
                    f"这类专业对象(墙/门窗/房间)导出 DXF 后几何语义丢失, 只能靠图层名/块名近似; "
                    f"建议由设计方另存为 T3/普通实体格式后重新提供")
    except Exception as e:
        print(f'  ⚠ 扩展实体解析失败(跳过): {e}')

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
        # v6.6: 用原始 TEXT 短文本提取 — 聚类拼接串('L50x5'+'2'→'L50x52')把
        # 角钢/扁钢参数拼坏(L50x52 无意义→负重量-0.01t), 原始标注行才干净
        steel_texts = raw_texts
        if _msp is not None:
            try:
                raw = set()
                for e in _msp:
                    tt = ''
                    try:
                        tt = (e.dxf.text if e.dxftype() == 'TEXT' else e.text) or ''
                    except Exception:
                        continue
                    tt = tt.strip().replace('\n', ' ')
                    if tt:
                        raw.add(tt)
                # 原始短文本优先(构件标注行), 聚类长串兜底
                steel_texts = sorted(raw)
            except Exception:
                pass
        mems = extract_steel_from_texts(steel_texts)
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
        # v6.10.6 路径1 视觉语义接入: 几何房间识别为 0 时, 用视觉读出的房间/部位名补位。
        # 此前视觉结论仅用于专业类型质检(读完即丢), 换画法/换图层命名即失效 —— 这是
        # "视觉看懂了但算量用不上"的根因; 补位项标来源'视觉识别(补位)', 不放尺寸(不编造面积)。
        try:
            # v6.10.6: 改为**合并去重** —— 原"几何房间为空才补"会被 HATCH 回退的假房间
            # （图层名如"地面填充"/"防水填充"）挡住, 视觉读到的真实房间名被白丢。
            if True:
                vis = pid.get('视觉识别') or {}
                POS = ('卫生间', '办公室', '会议室', '走廊', '楼梯间', '厨房', '淋浴间',
                       '储藏', '设备间', '门厅', '阳台', '内墙', '外墙', '楼面', '顶棚')
                cands = list(vis.get('房间部位') or []) + list(vis.get('可见文字') or [])
                for t in ((pid.get('视觉细部') or {}).get('文字') or [])[:40]:
                    cands.append(t if isinstance(t, str) else str(t.get('文本', '')))
                rooms, seen = [], set()
                for c in cands:
                    s = str(c)
                    for p in POS:
                        if p in s and p not in seen:
                            seen.add(p)
                            rooms.append({'房间名': p, '面积_m2': None,
                                          '来源': '视觉识别(补位)'})
                _have = {str(r.get('房间名') or '') for r in (pid.get('房间') or [])}
                rooms = [r for r in rooms if r['房间名'] not in _have]
                if rooms:
                    pid['房间'] = list(pid.get('房间') or []) + rooms
                    print(f"  房间(视觉补位): +{len(rooms)} 个 "
                          f"({', '.join(r['房间名'] for r in rooms[:6])})")
                else:
                    print('  房间(视觉补位): 视觉未读出房间/部位名')
        except Exception as e:
            print(f'  ⚠ 视觉房间补位失败: {e}')
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

    # ── v6.9: 图纸版本提取 — 修订记录(如'2026.2.26修改') → 版本意识(真造价师
    # 最怕算错版本: 计算书必须能对到哪版图纸) ──
    try:
        import re as _re2
        ver_hits = []
        for t in (raw_texts or []):
            m = _re2.search(r'(\d{4}[年./-]\d{1,2}[月./-]\d{1,2})[^0-9]{0,6}(修改|修订|变更|版)', t)
            if m:
                ver_hits.append(f'{m.group(1)}{m.group(2)}')
        if ver_hits:
            pid['图纸版本'] = {'修订记录': list(dict.fromkeys(ver_hits))[:5], '来源': '设计说明文字'}
            print(f"  图纸版本: 修订记录 {len(ver_hits)} 处")
    except Exception:
        pass

    # ── v6.8: 知识库触发检查 — 线索词→工艺链/材料/规则/漏项(边做边查机制化) ──
    # 命中结果写入 pid['知识检查']; ⚠️待核口径自动转图纸疑问(图纸问题清单可见)
    try:
        from knowledge_query import run_knowledge_checks, collect_unverified
        kc = run_knowledge_checks(pid)
        pid['知识检查'] = kc
        # v6.9: 待查证清单 — 知识库未查实条目交接(诚实: 不假装知道)
        pid['待查证'] = collect_unverified()
        if kc.get('图纸疑问建议'):
            for q in kc['图纸疑问建议']:
                # v6.9.5 思维层③: 疑问自动带暂定口径建议(图纸会审形态: 问题+暂按+需确认)
                pid.setdefault('图纸问题候选', []).append(
                    f'[知识库查证] {q[:90]}；暂按行业通行口径计列, 需设计/造价站确认')
            print(f"  知识库: 工艺链命中{len(kc.get('工艺链命中', {}))}类 "
                  f"规则依据{len(kc.get('规则依据', {}))}条 漏项检查{len(kc.get('漏项检查', []))}组 "
                  f"待查证{len(pid['待查证'])}条")
    except Exception as e:
        print(f'  ⚠ 知识库检查跳过: {e}')

    json_path = os.path.join(output_dir, '识图结果.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(pid, f, ensure_ascii=False, indent=2)
    print(f'  输出: {json_path}')
    return pid


if __name__ == '__main__':
    if len(sys.argv) < 2: print('用法: step1_recognize.py 图纸.dxf')
    else: run(sys.argv[1], os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output'))

