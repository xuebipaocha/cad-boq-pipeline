# -*- coding: utf-8 -*-
"""视觉交叉验证融合层 — v5.15 视觉化 V-3 / v6.0 视觉优先

用户 2026-08-01 确认: **凡是涉及到识图, 都要经过视觉路径, 且视觉路径与其他识图逻辑交叉验证**。
用户 2026-08-06 确认: **识图算量整个流程若视觉路径精度更高, 优先走视觉路径, 但交叉验证不能少**。

职责:
1. 把视觉识别结果(工程类型/构件计数)与几何/文字识图结果(pid)交叉验证
2. 输出裁决: 一致 → 确认; 冲突 → 视觉置信度高则视觉优先(已交叉验证), 否则标"待核"
3. 视觉证据写入 pid['视觉识别'], 裁决写入 pid['视觉验证'](供审图/质量报告消费)

v6.0 证据优先级: 视觉置信度≥几何置信度 且 ≥0.7 → 视觉优先(更新专业类型);
否则 几何/文字 > 视觉(冲突标待核)。交叉验证步骤始终保留。
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
        # 几何置信度低 + 视觉置信度高 → 提示可用视觉增强(但不覆盖)
        geo_conf = (pid.get('专业识别') or {}).get('置信度', 0)
        if geo_conf < 0.5 and v_conf >= 0.7:
            verdict['裁决'] += '; 注: 几何置信度低(%.2f), 视觉可作为佐证参考' % geo_conf
            verdict['待核'] = True
    else:
        # v6.0: 视觉优先策略 — 视觉置信度≥几何置信度 且 视觉置信度高时, 采用视觉结果(已交叉验证)
        geo_conf = (pid.get('专业识别') or {}).get('置信度', 0)
        if v_conf >= 0.7 and v_conf >= geo_conf:
            pid['专业类型'] = vision_spec
            # 同步专业识别置信度(视觉主导)
            (pid.setdefault('专业识别', {}))['置信度'] = v_conf
            verdict.update({'状态': '视觉优先', '待核': False,
                            '裁决': f'视觉({vision_spec}, 置信度{v_conf:.2f})优先于几何({geo_spec}, {geo_conf:.2f})——视觉精度更高且已交叉验证'})
        else:
            verdict.update({'状态': '冲突', '待核': True,
                            '裁决': f'视觉({vision_spec}, {v_conf:.2f})与几何识图({geo_spec}, {geo_conf:.2f})不一致, 需人工复核'})
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
        result = query_vision(png, enable=True)
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
            print(f"  ⚠ 视觉交叉验证: {verdict.get('裁决')}")
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
