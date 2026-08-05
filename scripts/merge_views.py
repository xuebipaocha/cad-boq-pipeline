# -*- coding: utf-8 -*-
"""多视图合并去重器 — v5.0 重写

v4.0 第三批原有 20 键合并逻辑全部保留, v5.0 变更:
1. 新增 `构件模型` 合并(merge_component_models): 同编号同截面 → 数量相加;
   同编号异截面 → 取置信度高者; 长度/位置取置信度高者; 证据链拼接;
   跨图计数取 max 防立面投影重复。
2. 修复 bug: `构件尺寸推导` 原来只取 primary 图, 而 dim 标注常出现在
   其他视图(剖面/立面), 导致多视图下标注尺寸丢失 — 改为按 layer 跨图合并。

合并规则(按权威性排序, 与 v4.0 一致):
1. 面积: 平面图 > 其他视图 (平面图是水平投影, 面积权威)
2. 构造层: 剖面图/做法表 优先于 平面文字 (厚度在剖面/做法中最准确)
3. 线性构件: 按名称+类型去重, 长度取最大值(平面图比立面准)
4. 构件计数(柱/梁/块): 平面图计数 (立面/剖面是同一构件的投影, 不应重复计数)
5. 标高: 全部保留, 按类型分组
6. 施工说明: 全部合并去重
7. 跨图一致性: 面积/厚度 差异告警

输入: 多张 DXF 各自识图后的结果列表
输出: 合并后的单一识图结果
"""
import json

# 截面一致容差
SECTION_TOL = 0.01
LEN_TOL = 0.02

# 同图层 dim 标注合并: 每层取最大长/宽/高
def _merge_dim_sizes(dim_sizes_list):
    out = {}
    for ds in dim_sizes_list:
        for layer, dims in (ds or {}).items():
            cur = out.setdefault(layer, {})
            for k, v in dims.items():
                if v is not None and (k not in cur or v > cur[k]):
                    cur[k] = v
    return out


def _merge_component_models(models):
    """构件模型合并: 每类构件按 编号+截面(±1%) 归并。
    同编号同截面 → 数量相加(跨图计数取 max 防立面投影重复);
    同编号异截面 → 截面/长度/位置取置信度高者, 证据链拼接。
    板/墙/房间: 单件类(编号固定 板-1/W-1/R1), 按编号直接归并,
    面积/长度取最大(平面图权威), 厚度取置信度高者。
    安装/钢构(v5.1): 按 编号+规格 归并, 数量取 max, 位置取置信度高者。
    """
    merged = {'柱': [], '梁': [], '板': [], '墙': [], '房间': [],
              '设备': [], '阀门': [], '灯具': [], '开关插座': [], '配电箱柜': [],
              '卫生器具': [], '消防设施': [], '管道': [], '钢构件': []}
    single_cls = {'板', '墙', '房间'}  # 单件类: 按编号归并, 面积取 max
    spec_cls = {'设备', '阀门', '灯具', '开关插座', '配电箱柜', '卫生器具',
                '消防设施', '管道', '钢构件'}  # 规格类: 按编号+规格归并
    for model in models:
        for cls in merged:
            for comp in (model or {}).get(cls, []) or []:
                name = comp.get('编号', '')
                hit = None
                for m in merged[cls]:
                    if m['编号'] != name:
                        continue
                    if cls in single_cls:
                        hit = m  # 单件类: 同编号即归并
                        break
                    if cls in spec_cls:
                        # 规格类: 编号+规格相同才归并
                        if m.get('规格') == comp.get('规格'):
                            hit = m
                        break
                    w1, w2 = m.get('截面宽_mm'), comp.get('截面宽_mm')
                    h1, h2 = m.get('截面高_mm'), comp.get('截面高_mm')
                    same_sec = (w1 and w2 and abs(w1 - w2) / max(w1, w2) <= SECTION_TOL
                                and h1 and h2 and abs(h1 - h2) / max(h1, h2) <= SECTION_TOL)
                    same_len = True
                    if m.get('长度_m') and comp.get('长度_m'):
                        l1, l2 = m['长度_m'], comp['长度_m']
                        same_len = abs(l1 - l2) / max(l1, l2) <= LEN_TOL
                    if same_sec and same_len:
                        hit = m
                        break
                if hit:
                    if cls in single_cls:
                        # 单件类: 面积/长度取 max, 厚度取置信度高者
                        for k in ('面积_m2', '长度_m', '周长_m'):
                            if comp.get(k) and comp.get(k, 0) > hit.get(k, 0):
                                hit[k] = comp[k]
                        if comp.get('置信度', 0) > hit.get('置信度', 0):
                            for k in ('厚度_mm', '厚度来源', '面积来源', '长度来源', '位置', '位置来源'):
                                if k in comp:
                                    hit[k] = comp[k]
                            hit['置信度'] = comp['置信度']
                    else:
                        # 数量: 同编号取 max(防立面投影重复)
                        hit['数量'] = max(hit.get('数量', 1), comp.get('数量', 1))
                        # 截面/长度/位置: 取置信度高者
                        if comp.get('置信度', 0) > hit.get('置信度', 0):
                            for k in ('截面宽_mm', '截面高_mm', '长度_m', '高度_m',
                                      '截面来源', '长度来源', '厚度_mm', '厚度来源',
                                      '面积_m2', '位置', '位置来源', '钢筋', '配筋'):
                                if k in comp:
                                    hit[k] = comp[k]
                            hit['置信度'] = comp['置信度']
                    for ev in comp.get('证据', []):
                        if ev not in hit.get('证据', []):
                            hit['证据'].append(ev)
                else:
                    merged[cls].append(dict(comp))
    return merged


def merge_views(view_results):
    """view_results: [{名称, 数据}] → 合并结果"""
    merged = {}
    views = [v for v in view_results if v.get('数据')]
    if not views:
        return merged

    # 1. 面积: 平面图优先 (判断: 面积最大的图视为平面图; 或按名称含'平面')
    area_candidates = []
    for v in views:
        areas = v['数据'].get('面积区域', [])
        total = sum(a.get('面积_m2', 0) for a in areas)
        name = v.get('名称', '')
        is_plan = ('平面' in name) or ('PLAN' in name.upper())
        area_candidates.append((total, is_plan, v))
    area_candidates.sort(key=lambda x: (-x[1], -x[0]))
    primary = area_candidates[0][2] if area_candidates else views[0]

    # 2. 构造层: 剖面/做法表优先 — 收集所有构造层, 厚度非空优先
    layers = []
    seen_layers = set()
    for v in views:
        for l in v['数据'].get('构造层', []):
            name = l.get('名称', '')
            if not name or name in seen_layers:
                continue
            seen_layers.add(name)
            layers.append(l)
    # 同名构造层: 厚度非空者优先
    layers.sort(key=lambda l: 0 if l.get('厚度_mm') else 1)

    # 3. 线性构件: 名称去重, 长度取最大
    linear = {}
    for v in views:
        for item in v['数据'].get('线性构件', []):
            key = item.get('名称', '') + '|' + item.get('类型', '')
            if key not in linear or item.get('长度_m', 0) > linear[key].get('长度_m', 0):
                linear[key] = item
    linear_list = list(linear.values())

    # 4. 构件计数: 平面图优先 (柱/梁/块等)
    counts = {}
    for v in views:
        bi = v['数据'].get('建筑信息', {})
        elem = bi.get('构件分类', {})
        for k, c in elem.items():
            if k not in counts or c > counts[k]:
                counts[k] = c
        blocks = v['数据'].get('图块明细', {})
        for cat, items in blocks.items():
            merged_blocks = counts.setdefault(f'图块_{cat}', {})
            if isinstance(merged_blocks, dict) and isinstance(items, list):
                for it in items:
                    nm = it.get('name', '')
                    merged_blocks[nm] = max(merged_blocks.get(nm, 0), it.get('count', 0))

    # 5. 标高: 全部保留去重
    elevs = []
    seen_e = set()
    for v in views:
        for e in v['数据'].get('标高', []):
            key = (e.get('type', ''), round(e.get('value', 0), 2))
            if key not in seen_e:
                seen_e.add(key)
                elevs.append(e)

    # 6. 施工说明/问题候选: 合并去重
    texts = []
    seen_t = set()
    for v in views:
        for t in v['数据'].get('施工说明', []):
            if t not in seen_t:
                seen_t.add(t)
                texts.append(t)

    # 7. 跨图一致性告警
    notes = []
    plan_area = sum(a.get('面积_m2', 0) for a in primary['数据'].get('面积区域', []))
    for v in views:
        if v is primary:
            continue
        other_area = sum(a.get('面积_m2', 0) for a in v['数据'].get('面积区域', []))
        if plan_area > 0 and other_area > 0:
            ratio = other_area / plan_area
            if not (0.7 <= ratio <= 1.3) and other_area > plan_area:
                notes.append(f'视图"{v.get("名称","")}"面积({other_area:.0f}m²)大于平面图({plan_area:.0f}m²)，疑似重复或立面投影')

    # v5.0: 构件尺寸推导按 layer 跨图合并(修复 dim 标注在非 primary 视图丢失)
    dim_sizes_merged = _merge_dim_sizes([v['数据'].get('构件尺寸推导', {}) for v in views])

    # v5.0: 构件模型合并
    cm_merged = _merge_component_models([v['数据'].get('构件模型', {}) for v in views])

    # 组装
    merged = {
        '专业类型': primary['数据'].get('专业类型', '房屋建筑与装饰工程'),
        '专业识别': primary['数据'].get('专业识别', {}),
        '图纸元数据': primary['数据'].get('图纸元数据', {}),
        '面积区域': primary['数据'].get('面积区域', []),
        '构造层': layers,
        '线性构件': linear_list,
        '施工说明': texts[:80],
        '图纸问题候选': list(dict.fromkeys(sum([v['数据'].get('图纸问题候选', []) for v in views], [])))[:30],
        '表格': sum([v['数据'].get('表格', []) for v in views], []),
        '标高': elevs,
        '标高参数': primary['数据'].get('标高参数', {}),
        '图块明细': counts,
        '标注关联': sum([v['数据'].get('标注关联', []) for v in views], []),
        '构件尺寸推导': dim_sizes_merged,
        '构件模型': cm_merged,
        '剖面算量': sum([v['数据'].get('剖面算量', []) for v in views], []),
        '建筑信息': primary['数据'].get('建筑信息', {}),
        '安装信息': primary['数据'].get('安装信息', {}),
        '园林信息': primary['数据'].get('园林信息', {}),
        'CAD分析': primary['数据'].get('CAD分析'),
        '_merge_notes': notes,
        '_multi_view': True,
    }
    # v5.15 修复: 多视图工程性质汇总 — 任一视图判"大修与改造"则继承
    # (平面图含施工说明判定大修, 立面/剖面无说明判新建, 不能丢)
    nature_hits = {}
    for v in views:
        nv = v['数据'].get('工程性质')
        if nv:
            nature_hits[nv] = nature_hits.get(nv, 0) + 1
    if nature_hits:
        if nature_hits.get('大修与改造'):
            merged['工程性质'] = '大修与改造'
        else:
            merged['工程性质'] = max(nature_hits, key=nature_hits.get)
    return merged


# ───────────────────────── CLI 入口(v5.14 工作流 P1: 多视图合并独立化) ─────────────────────────

def merge_drawing_files(dwg_files, output_dir=None, specialty=None):
    """多图纸 → 各自识图 → 合并去重 → 写识图结果.json。返回合并结果。
    v5.14: 从 pipeline.py main 中抽出的多视图逻辑(调度瘦身)。
    """
    import json
    import shutil
    import sys
    import os

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output')
    os.makedirs(output_dir, exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from step1_recognize import run as run_recognize

    print(f'多视图模式: {len(dwg_files)}个文件')
    views = []
    for i, dwg in enumerate(dwg_files):
        run_recognize(dwg, output_dir)
        src = os.path.join(output_dir, '识图结果.json')
        dst = os.path.join(output_dir, f'识图结果_{i}.json')
        if os.path.exists(src):
            shutil.move(src, dst)
    for i, dwg in enumerate(dwg_files):
        vp = os.path.join(output_dir, f'识图结果_{i}.json')
        if os.path.exists(vp):
            with open(vp, encoding='utf-8') as f:
                vd = json.load(f)
            views.append({'名称': os.path.basename(dwg), '数据': vd})

    try:
        combined = merge_views(views)
        combined['视图'] = views
        # v5.15 修复: 多视图工程性质汇总 — 任一视图判"大修与改造"则继承
        # (平面图含施工说明判定大修, 立面/剖面无说明判新建, 不能丢)
        nature_hits = {}
        for v in views:
            nd = v.get('数据', {}) or {}
            nv = nd.get('工程性质')
            if nv:
                nature_hits[nv] = nature_hits.get(nv, 0) + 1
        if nature_hits:
            # 大修与改造 优先(翻新项目信号比新建强), 其次取多数
            if nature_hits.get('大修与改造'):
                combined['工程性质'] = '大修与改造'
            else:
                combined['工程性质'] = max(nature_hits, key=nature_hits.get)
        print(f'  ✅ 合并去重完成: {len(views)}个视图, 构造层{len(combined.get("构造层", []))}个, '
              f'线性构件{len(combined.get("线性构件", []))}个, 工程性质={combined.get("工程性质")}')
        for n in combined.get('_merge_notes', []):
            print(f'  ⚠ {n}')
    except Exception as e:
        print(f'  合并去重失败({e}), 使用简单拼接')
        combined = {'专业类型': specialty or '房屋建筑与装饰工程', '视图': views, '面积区域': [], '构造层': [],
                    '线性构件': [], '施工说明': [], '图纸问题候选': [], '_multi_view': True}
        for v in views:
            for k in ['面积区域', '构造层', '施工说明']:
                combined[k].extend(v.get('数据', {}).get(k, []))
    with open(os.path.join(output_dir, '识图结果.json'), 'w', encoding='utf-8') as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    return combined


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='多视图合并: 多张 DXF 识图后合并去重')
    ap.add_argument('drawings', nargs='+', help='DXF/DWG 图纸文件(≥2 张)')
    ap.add_argument('--output-dir', default=None, help='输出目录(默认 skill/output)')
    ap.add_argument('--specialty', default=None, help='指定专业')
    args = ap.parse_args(argv)
    if len(args.drawings) < 2:
        print('多视图合并至少需要 2 张图纸')
        return None
    return merge_drawing_files(args.drawings, args.output_dir, args.specialty)


if __name__ == '__main__':
    main()
