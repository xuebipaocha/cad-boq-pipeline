# -*- coding: utf-8 -*-
"""视觉交叉验证融合层 — v5.15 视觉化 V-3 / v6.0 视觉优先

用户 2026-08-01 确认: **凡是涉及到识图, 都要经过视觉路径, 且视觉路径与其他识图逻辑交叉验证**。
用户 2026-08-06 确认: **识图算量整个流程若视觉路径精度更高, 优先走视觉路径, 但交叉验证不能少**。

职责:
1. 把视觉识别结果(工程类型/构件计数)与几何/文字识图结果(pid)交叉验证
2. 输出裁决: 一致 → 确认; 冲突 → 视觉置信度高则视觉优先(已交叉验证), 否则标"待核"
3. 视觉证据写入 pid['视觉识别'], 裁决写入 pid['视觉验证'](供审图/质量报告消费)

v6.5 证据优先级: 几何/文字 > 视觉(视觉永不覆盖几何的最终判定)。
视觉降级为纯交叉验证信号: 一致→相互印证; 冲突→标"待核"不改结果。
唯一例外: 栅格图(实体<5)几何无数据, 视觉必须补位。
交叉验证步骤始终保留。
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')

# 视觉返回的工程类型 → 标准专业名(名称包含匹配的兜底表)
SPECIALTY_ALIAS = {
    '房屋建筑与装饰工程': ['房屋建筑', '房建', '建筑装饰', '装饰', '建筑'],
    '安装工程': ['安装', '机电', '暖通', '电气'],
    '市政工程': ['市政', '道路', '道路桥梁'],
    '园林绿化工程': ['园林', '绿化', '景观', '园建'],
    '钢结构工程': ['钢结构', '钢构', '钢架'],
}


def normalize_specialty(name):
    """视觉返回的工程类型 → 标准专业名。无法归并 → None。"""
    if not name:
        return None
    for std, aliases in SPECIALTY_ALIAS.items():
        if std in name or name in std:
            return std
        for a in aliases:
            if a in name:
                return std
    return None


def _vision_conf(r):
    """视觉置信度(0~1), 缺省 0.5(规划默认)。"""
    try:
        v = float(r.get('工程类型置信度') or 0.5)
        return min(max(v, 0.0), 1.0)
    except Exception:
        return 0.5


def cross_validate(pid, vision_result):
    """视觉结果 vs 识图结果交叉验证 → 裁决 dict。

    pid: 识图结果(含 专业类型/专业识别/构件模型/图块明细 等)
    vision_result: query_vision 的结构化 JSON(含 _meta.来源='视觉识别')
    返回:
      {状态: '一致'|'冲突'|'视觉不可用'|'跳过',
       视觉工程类型, 几何工程类型, 视觉置信度, 裁决: str, 待核: bool}
    """
    verdict = {
        '视觉可用': False, '待核': False, '状态': '跳过',
        '视觉工程类型': None, '几何工程类型': pid.get('专业类型', ''),
        '视觉置信度': None, '裁决': '视觉未启用',
    }
    if not vision_result:
        return verdict

    vision_spec = normalize_specialty(vision_result.get('工程类型', ''))
    geo_spec = pid.get('专业类型', '')
    v_conf = _vision_conf(vision_result)
    verdict.update({
        '视觉可用': True,
        '视觉工程类型': vision_spec or vision_result.get('工程类型'),
        '视觉置信度': v_conf,
        '视觉原始': vision_result.get('工程类型'),
    })

    if not vision_spec or not geo_spec:
        verdict.update({'状态': '无法比对', '裁决': '视觉或几何工程类型缺失, 无法交叉验证',
                        '待核': True})
        return verdict

    if vision_spec == geo_spec:
        # 一致: 几何为主, 视觉佐证; 视觉置信度高且几何置信度低 → 提升几何可信度提示
        verdict.update({'状态': '一致', '待核': False,
                        '裁决': f'视觉({vision_spec}, 置信度{v_conf:.2f})与几何识图({geo_spec})一致, 相互印证'})
        # 几何置信度低 + 视觉置信度高 → 提示可用视觉佐证(但不覆盖); 门槛 0.85(v6.5 提高)
        geo_conf = (pid.get('专业识别') or {}).get('置信度', 0)
        if geo_conf < 0.5 and v_conf >= 0.85:
            verdict['裁决'] += '; 注: 几何置信度低(%.2f), 视觉可作为佐证参考' % geo_conf
            verdict['待核'] = True
    else:
        # v6.5: 视觉永不覆盖几何 — 几何是确定性解析(实体/坐标/文字), 视觉只是质检信号。
        # 即使视觉置信度高、几何置信度低, 视觉也只标"待核", 不改 pid 专业类型。
        # (v6.0/v6.4 曾允许"几何<0.5 且视觉≥0.7 时视觉优先", 实测 qwen 自报置信度
        #  不可靠导致真实图纸上误覆盖几何判定 — 已回退。)
        geo_conf = (pid.get('专业识别') or {}).get('置信度', 0)
        verdict.update({'状态': '冲突', '待核': True,
                        '裁决': f'视觉({vision_spec}, 置信度{v_conf:.2f})与几何识图({geo_spec}, 几何置信度{geo_conf:.2f})不一致; 几何判定为准, 视觉冲突标待核, 建议人工复核图面'})
    return verdict


def _geo_counts(pid):
    """几何/文字识图侧的构件计数(与视觉构件计数比对用)。
    返回 {苗木: n, 设备类: n, 明细: {...}}。
    """
    counts = {}
    # 苗木: 园林信息(乔木+灌木) 或 图块明细.苗木
    gi = pid.get('园林信息', {}) or {}
    trees = sum(t.get('数量', 0) or 0 for t in gi.get('苗木', {}).get('乔木', []) or [])
    shrubs = sum(t.get('数量', 0) or 0 for t in gi.get('苗木', {}).get('灌木', []) or [])
    if trees or shrubs:
        counts['苗木'] = trees + shrubs
    # 设备类: 安装信息(阀门/灯具/配电箱柜/设备/开关插座/卫生器具/消防设施)
    info = pid.get('安装信息', {}) or {}
    equip_n = 0
    for k in ('设备', '阀门', '灯具', '开关插座', '配电箱柜', '卫生器具', '消防设施'):
        v = info.get(k)
        if isinstance(v, dict):
            equip_n += sum(v.values())
        elif isinstance(v, list):
            equip_n += sum(i.get('数量', 1) or 1 for i in v if isinstance(i, dict))
    if equip_n:
        counts['设备类'] = equip_n
    # 图块明细兜底(苗木/设备类图块) — 与园林信息/安装信息同源, 取 max 防重复计数
    bd = pid.get('图块明细', {}) or {}
    for cat in ('苗木', '设备', '阀门', '灯具', '配电箱柜'):
        items = bd.get(cat, []) or []
        if items:
            n = sum(i.get('count', 1) or 1 for i in items)
            key = '苗木' if cat == '苗木' else '设备类'
            counts[key] = max(counts.get(key, 0), n)
    return counts


def _vision_counts(vision_result):
    """视觉侧的构件计数(构件计数 dict)。"""
    return (vision_result or {}).get('构件计数', {}) or {}


def cross_validate_counts(pid, vision_result):
    """构件计数交叉验证: 视觉(苗木块/设备符号) vs 几何(苗木/设备类)。

    返回 [{类别, 几何数, 视觉数, 状态: '一致'|'偏差'|'视觉未检出'|'几何无', 裁决}]
    """
    geo = _geo_counts(pid)
    vis = _vision_counts(vision_result)
    # 视觉键 → 几何类别映射(苗木块→苗木, 设备符号→设备类)
    vis_map = [('苗木块', '苗木'), ('设备符号', '设备类')]
    out = []
    for vk, gk in vis_map:
        g_n = geo.get(gk)
        v_n = vis.get(vk)
        if g_n is None:
            continue
        if not v_n:
            out.append({'类别': gk, '几何数': g_n, '视觉数': 0,
                        '状态': '视觉未检出',
                        '裁决': f'{gk}: 几何识别 {g_n}, 视觉未检出(以几何为准, 整图渲染下小符号易漏)'})
            continue
        err = abs(g_n - v_n) / max(g_n, v_n)
        if err <= 0.5:
            out.append({'类别': gk, '几何数': g_n, '视觉数': v_n,
                        '状态': '一致', '裁决': f'{gk}: 几何 {g_n} vs 视觉 {v_n}, 相互印证'})
        else:
            out.append({'类别': gk, '几何数': g_n, '视觉数': v_n,
                        '状态': '偏差', '裁决': f'{gk}: 几何 {g_n} vs 视觉 {v_n} 偏差 {err:.0%}, 需复核'})
    return out


def attach_vision(pid, vision_result):
    """把视觉结果与验证裁决写入 pid(幂等)。"""
    verdict = cross_validate(pid, vision_result)
    if vision_result:
        meta = vision_result.get('_meta', {}) or {}
        pid['视觉识别'] = {
            '工程类型': vision_result.get('工程类型'),
            '工程类型置信度': vision_result.get('工程类型置信度'),
            '构件计数': vision_result.get('构件计数', {}),
            '模型': meta.get('模型'),
            '来源': '视觉识别',
            # v6.10.6 路径1: 视觉语义接入算量 —— 此前只传工程类型(纯质检), 房间/部位/文字
            # 全部丢弃, 导致"视觉看懂了但算量用不上"; 现透传供房间补位/构造层/特征编写消费
            '房间部位': vision_result.get('房间部位') or [],
            '可见文字': (vision_result.get('可见文字') or [])[:40],
        }
    pid['视觉验证'] = verdict
    # v5.15 构件计数交叉验证(视觉苗木块/设备符号 vs 几何苗木/设备类)
    try:
        pid['构件计数验证'] = cross_validate_counts(pid, vision_result)
    except Exception:
        pid['构件计数验证'] = []
    return verdict


def vision_gate_enabled():
    """视觉路径开关: 识图必经视觉(用户确认)。可用 VISION_OFF=1 临时关闭(如无 Key/离线)。"""
    return os.environ.get('VISION_OFF', '0') not in ('1', 'true', 'True', 'yes')


def _geo_evidence_summary(pid):
    """几何/文字识图侧的证据摘要(供二次复核 prompt 使用)。"""
    parts = []
    prof = pid.get('专业识别', {}) or {}
    cands = prof.get('候选', []) or []
    parts.append(f"几何专业识别: {pid.get('专业类型', '未知')}(置信度{prof.get('置信度', 0):.2f})")
    if cands:
        top = cands[0]
        parts.append(f"  命中关键词: {top.get('命中', [])}")
    if pid.get('工程性质'):
        parts.append(f"工程性质: {pid.get('工程性质')}")
    if pid.get('面积区域'):
        parts.append(f"面积区域: {len(pid.get('面积区域', []))}个")
    if pid.get('构造层'):
        names = [l.get('名称', '')[:18] for l in pid.get('构造层', [])[:3]]
        parts.append(f"构造层: {names}")
    return '\n'.join(parts)


def second_look(pid, png_path, vision_result, verdict):
    """方案C 二次复核: 冲突/低置信度时, 把几何证据+视觉结果+渲染图喂回 Qwen,
    模拟人"回头细看"对比判断。返回复核 dict(不覆盖几何, 只补充证据)。
    """
    if not vision_result or not png_path or not os.path.exists(png_path):
        return {'触发': False, '结论': '未触发'}
    try:
        from vision_query import query_vision
    except Exception:
        return {'触发': False, '结论': 'vision_query 不可用'}

    geo_summary = _geo_evidence_summary(pid)
    vis_type = vision_result.get('工程类型', '未知')
    vis_conf = vision_result.get('工程类型置信度', 0.5)
    prompt = (
        '你是工程图纸识图复核助手。系统用两条独立路径识别了同一张图纸, 结果不一致, 请你对比判断。\n'
        '路径A(几何/文字解析, 从CAD实体精确提取):\n'
        f'{geo_summary}\n\n'
        f'路径B(视觉识别, 整体看图): 工程类型={vis_type}, 置信度={vis_conf}\n\n'
        '请结合图中内容判断: 哪条路径更可信? 输出 JSON(不要其他文字):\n'
        '{"更可信": "几何"|"视觉"|"不确定", "理由": "50字以内", "怀疑点": "具体是哪里存疑"}'
    )
    r = query_vision(png_path, enable=True, prompt=prompt)
    if not r:
        return {'触发': True, '结论': '复核调用失败', '更可信': '不确定'}

    # qwen 可能直接返回结构化 JSON(顶层键 更可信/理由/怀疑点), 也可能包在原始回复里
    trust = r.get('更可信') or ''
    reason = r.get('理由') or ''
    doubt = r.get('怀疑点') or ''
    if not trust:
        import re
        content = r.get('原始回复') or r.get('工程类型') or ''
        c = content.strip()
        if '```' in c:
            c = c.split('```')[1]
            if c.startswith('json'):
                c = c[4:]
        try:
            parsed = json.loads(c)
            trust = parsed.get('更可信', '')
            reason = parsed.get('理由', '')
            doubt = parsed.get('怀疑点', '')
        except Exception:
            pass
    if trust not in ('几何', '视觉', '不确定'):
        trust = '不确定'
    return {
        '触发': True,
        '更可信': trust,
        '理由': reason,
        '怀疑点': doubt,
        '结论': f"复核: 模型认为[{trust}]更可信 - {reason}",
    }


def _tiled_enabled(dxf_file=None):
    """多尺度(分块)视觉开关 — v6.10。

    VISION_TILED=1 强制开; =auto 按图纸像素密度自动判定(整图 1px 对应图纸跨度超阈值才分块,
    避免小图上白花 ~10 倍 token); VISION_TILED=0 强制关。
    **v6.10.6 fx4: 默认改为 auto** —— 实测新图 000006(给排水图, 1px≈数十图纸单位):
    整图视觉只读出 0 条文字/0 个房间, 分块后读出 108 条文字、房间补位生效(0→4 个) →
    密图必须默认分块, 否则视觉语义整条路径空转; 稀疏小图由 auto 判定不分块(不浪费 token)。
    可配 VISION_TILES_GRID / VISION_TILES_WORKERS / VISION_TILE_MM_PER_PX。
    """
    v = os.environ.get('VISION_TILED', 'auto').strip().lower()
    if v in ('1', 'true', 'yes'):
        return True
    if v == 'auto':
        if not dxf_file:
            return False
        try:
            from vision_tiles import tiling_recommended
            ok, mmpp, th = tiling_recommended(dxf_file)
            if mmpp is not None:
                print(f'  [分块判定] 整图渲染 1px≈{mmpp} 图纸单位(阈值 {th}) → '
                      f'{"建议分块(小字必然漏检)" if ok else "整图已足够, 跳过分块"}')
            return bool(ok)
        except Exception as e:
            print(f'  [分块判定] 失败, 跳过分块: {e}')
            return False
    return False


_WIN_CODE_RE = None


_CODE_CONFUSION = (('O', '0'), ('o', '0'), ('I', '1'), ('l', '1'),
                   ('S', '5'), ('s', '5'), ('B', '8'), ('b', '8'))
_CODE_CONF_CHARS = 'OoIlSsBb'


def _fix_code_confusion(prefix, digits):
    """修正视觉 OCR 常见字形混淆(v6.10 实测: MO921 实为 M0921, LCl515 实为 LC1515)。

    规则: 前缀末尾若是数字误读字符(O/o/I/l/S/s/B/b) → 移入数字区再统一替换为 0/1/5/8;
    数字区同样替换。保守处理: 仅当 前缀长度≥2 时移植前缀末字符(门窗编号前缀 LC/MC/M/C/TC
    皆不以这些字符结尾, 故不会误伤真前缀)。
    """
    if len(prefix) > 1 and prefix[-1] in _CODE_CONF_CHARS:
        digits = prefix[-1] + digits
        prefix = prefix[:-1]
    prefix = prefix.upper()
    for ch, rep in _CODE_CONFUSION:
        digits = digits.replace(ch, rep)
    return prefix, digits


def _extract_window_codes(text_items):
    """从分块视觉文字里提取门窗编号(LC-1818 / M0921 / C1515 形态)。

    实测(船体大楼 61 条文字)中门窗编号是价值最高的产出 —— 大修门窗量口径依赖它。
    过滤规则: 字母1~3位 + 可选连字符 + 3~4位数字; 排除比例尺(含:)、做法编号(数字开头)。
    """
    import re as _re
    seen = {}
    for it in text_items or []:
        t = (it.get('文本') if isinstance(it, dict) else str(it)) or ''
        weight = it.get('观测次数', 1) if isinstance(it, dict) else 1
        for m in _re.finditer(r'\b([A-Za-z]{1,3})[-]?([0-9OoIlSsBb]{3,4})\b', t):
            if ':' in t or 'GB' in t.upper():
                continue
            prefix, digits = _fix_code_confusion(m.group(1), m.group(2))
            code = f'{prefix}{digits}'
            seen[code] = seen.get(code, 0) + weight
    # 观测数≥2 的保留(单次观测可能是误读), 或总量<5 时全保留
    codes = {k: v for k, v in seen.items() if v >= 2} or seen
    return dict(sorted(codes.items(), key=lambda kv: -kv[1]))


def attach_tiled_details(pid, tiled):
    """把分块视觉细部写入 pid['视觉细部'] — 独立字段, **不覆盖任何几何量**(v6.5 纪律)。

    多尺度思路: 整图识别定框架(工程类型/区域/规模), 分块识别补细节(门窗编号/规格文字)。
    细节只作"视觉识别"信号: 与几何冲突时标待核, 由人工/后续规则裁决。
    """
    if not tiled or not tiled.get('文字'):
        return None
    texts = tiled.get('文字') or []
    st = tiled.get('统计') or {}
    detail = {
        '来源': '分块视觉识别',
        '切块': tiled.get('切块'),
        '后端': tiled.get('后端'),
        '文字数': len(texts),
        '文字': texts[:200],
        '构件计数': tiled.get('构件计数', {}),
        '统计': st,
        '待核提示': [],
    }
    codes = _extract_window_codes(texts)
    if codes:
        detail['门窗编号'] = codes
        # 与几何门窗交叉(几何侧门窗表/门窗分项数量) — 不一致只提示不覆盖
        geo_win = pid.get('门窗表') or pid.get('门窗') or []
        try:
            geo_cnt = len(geo_win) if not isinstance(geo_win, dict) else len(geo_win.get('门窗', []))
        except Exception:
            geo_cnt = 0
        if geo_cnt and len(codes) != geo_cnt:
            detail['待核提示'].append(
                f"分块视觉识出门窗编号 {len(codes)} 类({list(codes)[:6]}), 几何门窗 {geo_cnt} 条 — "
                f'口径需人工确认(视觉不覆盖几何)')
        else:
            detail['待核提示'].append(
                f"分块视觉识出门窗编号 {len(codes)} 类: {list(codes)[:8]} — 供门窗量口径核对")
    if st.get('解析失败块'):
        detail['待核提示'].append(f"分块识别有 {st['解析失败块']} 块 JSON 解析失败, 该块细节缺失")
    pid['视觉细部'] = detail
    return detail


def _merge_vision_samples(acc, new):
    """v6.10.6 ③多次采样治波动: 并集合并两次视觉结果。

    实测同配置两次差异巨大（0~40 条文字 / 2~15 个房间）—— 视觉模型输出有随机性,
    单次采样不可靠。合并策略: 文字/房间部位取**并集**(召回优先), 构件计数取**最大**
    (宁可多算待核, 不可漏), 置信度取最大。
    """
    if not acc:
        return new
    if not new:
        return acc
    out = dict(acc)
    for k in ('可见文字', '房间部位'):
        seen, merged = set(), []
        for x in list(acc.get(k) or []) + list(new.get(k) or []):
            key = str(x)
            if key not in seen:
                seen.add(key)
                merged.append(x)
        out[k] = merged
    ca, cb = acc.get('构件计数') or {}, new.get('构件计数') or {}
    out['构件计数'] = {k: max(int(ca.get(k) or 0), int(cb.get(k) or 0))
                       for k in set(ca) | set(cb)}
    try:
        out['工程类型置信度'] = max(float(acc.get('工程类型置信度') or 0),
                                    float(new.get('工程类型置信度') or 0))
    except Exception:
        pass
    return out


def run_vision_for_drawing(pid, dwg_file, output_dir):
    """识图流程内的视觉路径(渲染→识别→交叉验证→写入 pid)。

    - 默认启用(用户确认: 识图必经视觉); VISION_OFF=1 可临时关闭
    - 任何失败(无 Key/超时/渲染失败) → 静默降级, 绝不影响几何主流程
    - 渲染 PNG 存 output_dir/renders/
    """
    if not vision_gate_enabled():
        return attach_vision(pid, None)

    try:
        # 1. 渲染: DXF → PNG(复用 render_dxf)
        from render_dxf import render_dxf
        render_dir = os.path.join(output_dir, 'renders')
        meta = render_dxf(dwg_file, render_dir, per_layer=False)
        png = meta['files']['full']

        # 2. 视觉识别: 强制启用(识图必经), 独立 try
        from vision_query import query_vision
        # v6.10.6 ③多次采样（VISION_SAMPLES, 默认 2）: 视觉输出随机性大, 单次不可靠 →
        # 采样 N 次取并集(文字/房间部位)与最大值(构件计数)。N=1 时行为与原来一致。
        _n_samp = max(1, int(os.environ.get('VISION_SAMPLES', '2')))
        result = None
        for _si in range(_n_samp):
            _r = query_vision(png, enable=True)
            result = _merge_vision_samples(result, _r) if _r else result
        if _n_samp > 1 and result:
            print(f'  视觉多次采样: {_n_samp} 次 → 文字 {len(result.get("可见文字") or [])} 条'
                  f'/部位 {len(result.get("房间部位") or [])} 个')
        if not result:
            print('  ⚠ 视觉路径: 识别失败, 跳过交叉验证(几何结果不受影响)')
            return attach_vision(pid, None)

        # 3. 交叉验证 + 写入 pid
        verdict = attach_vision(pid, result)
        print(f"  视觉验证: {verdict['状态']} | 视觉工程类型={verdict.get('视觉工程类型')} "
              f"(置信度{verdict.get('视觉置信度')}) vs 几何={verdict.get('几何工程类型')}")

        # 4. 方案C 增强: 冲突时二次复核(模拟人回头对比判断)
        if verdict.get('状态') == '冲突':
            sl = second_look(pid, png, result, verdict)
            pid['视觉复核'] = sl
            print(f"  🔍 二次复核(冲突): {sl.get('结论', '')}")
            if sl.get('更可信') == '视觉':
                # 仍不覆盖几何, 但提升待核提示(视觉有图面证据)
                verdict['裁决'] += '; 模型复核认为视觉更可信, 建议人工优先看图面'
            elif sl.get('更可信') == '几何':
                verdict['裁决'] += '; 模型复核认为几何更可信, 可降低优先级'
            verdict['待核'] = True
            pid['视觉验证'] = verdict

        # 5. 方案C 增强: 几何低置信度特写(模拟人凑近看图)
        geo_conf = (pid.get('专业识别') or {}).get('置信度', 0) or 0
        if geo_conf < 0.5 and verdict.get('视觉可用'):
            # 渲染分层图, 视觉对细节层单独确认
            try:
                meta_layers = render_dxf(dwg_file, render_dir, per_layer=True)
                layer_pngs = meta_layers.get('files', {})
                # 挑"内容层"(排除整图)最多取 2 张给视觉
                content_layers = [p for k, p in layer_pngs.items() if k not in ('full',)]
                detail = None
                for lp in content_layers[:2]:
                    d = query_vision(lp, enable=True, prompt=(
                        '这是CAD图纸的单个图层特写。请识别这个图层上的构件类型和大致数量, '
                        '输出JSON: {"构件": "名称", "数量": n, "置信度": 0-1}'))
                    if d:
                        detail = d
                        break
                if detail:
                    pid['视觉特写'] = detail
                    print(f"  🔍 低置信度特写(几何{geo_conf:.2f}): {json.dumps(detail, ensure_ascii=False)[:100]}")
            except Exception as e:
                print(f'  ⚠ 低置信度特写失败(跳过): {e}')

        if verdict.get('待核'):
            # v6.6: 待核 → 图纸问题候选(视觉质检信号落地 — 21图全量A/B证实视觉对
            # 工程类型无增量(21/21 vs 21/21), 但冲突报警灵敏度100%可靠; 此前待核
            # 只写 pid['视觉验证'] 无任何下游消费, 相当于花了API钱只写日志)
            pid.setdefault('图纸问题候选', []).append(
                f'[视觉交叉验证] {verdict.get("裁决", "视觉与几何识图不一致, 建议人工复核图面")}')
            print(f"  ⚠ 视觉交叉验证: {verdict.get('裁决')}")

        # 6. v6.10 多尺度: 分块补细节(整图已定框架) — VISION_TILED=1 强制 / =auto 按像素密度判定
        # v6.10.6 整图优先（用户定策）: 整图已读出内容 → 不再分块（省约 5 倍耗时/token）;
        # 整图读不出（密图/小字, 整图 0 条文字）才分块兜底。判据用整图**本次实际产出**
        # 而非像素密度 —— 实测同配置多次结果波动大（0~40 条文字）, 密度阈值会误判。
        _vr = pid.get('视觉识别') or {}
        _n_txt = len(_vr.get('可见文字') or [])
        _n_room = len(_vr.get('房间部位') or [])
        if (os.environ.get('VISION_TILED', 'auto').strip().lower() == 'auto'
                and (_n_txt >= 5 or _n_room >= 1)):
            print(f'  [分块判定] 整图已读出 {_n_txt} 条文字/{_n_room} 个部位 → 整图优先, 跳过分块')
        elif _tiled_enabled(dwg_file):
            try:
                from vision_tiles import identify_tiles
                grid_env = os.environ.get('VISION_TILES_GRID', '3x3')
                cols, rows = (int(x) for x in grid_env.lower().split('x'))
                tr = identify_tiles(
                    dwg_file, out_dir=render_dir, grid=(cols, rows),
                    workers=int(os.environ.get('VISION_TILES_WORKERS', '4')))
                detail = attach_tiled_details(pid, tr)
                if detail:
                    codes = detail.get('门窗编号') or {}
                    print(f"  视觉细部(分块): 文字 {detail.get('文字数', 0)} 条"
                          + (f", 门窗编号 {len(codes)} 类" if codes else ''))
                    for warn in detail.get('待核提示', []):
                        print(f"    · {warn}")
            except Exception as e:
                print(f'  ⚠ 分块视觉失败(跳过, 几何结果不受影响): {e}')

        return verdict
    except Exception as e:
        print(f'  ⚠ 视觉路径异常(跳过, 几何结果不受影响): {e}')
        return attach_vision(pid, None)


if __name__ == '__main__':
    # 自检: 交叉验证逻辑
    tests = [
        ({'专业类型': '市政工程', '专业识别': {'置信度': 0.9}},
         {'工程类型': '市政工程', '工程类型置信度': 0.95}),
        ({'专业类型': '房屋建筑与装饰工程', '专业识别': {'置信度': 0.3}},
         {'工程类型': '房建', '工程类型置信度': 0.9}),
        ({'专业类型': '安装工程', '专业识别': {'置信度': 0.8}},
         {'工程类型': '市政工程', '工程类型置信度': 0.8}),
        ({'专业类型': '钢结构工程', '专业识别': {'置信度': 0.9}}, None),
    ]
    for pid, vis in tests:
        v = cross_validate(pid, vis)
        print(f"几何={pid['专业类型']} 视觉={vis and vis.get('工程类型')} → {v['状态']} 待核={v['待核']}")
