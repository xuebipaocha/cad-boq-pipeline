# -*- coding: utf-8 -*-
"""造价知识库查询层 — v6.8

全专业知识库(knowledge/): 工艺链/材料特性/计算规则/工程类型检查表。
线索词 → 查库 → 决定列项/口径/依据引用(边做边查的机制化)。

库文件: data/knowledge/*.json (全专业维度: 房屋建筑与装饰/安装/市政/园林绿化/钢结构)
来源分级: ✅规范原文 / ⚠️行业通行口径 / ❌未查实(不写入)
"""
import json
import os

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'knowledge')
_CACHE = {}


def _norm_specialty(sp):
    """'房屋建筑与装饰工程' → '房屋建筑与装饰'(库 key 无工程后缀)。"""
    return (sp or '').replace('工程', '') if sp else ''


def _load(name):
    if name not in _CACHE:
        with open(os.path.join(BASE, name), encoding='utf-8') as f:
            _CACHE[name] = json.load(f)
    return _CACHE[name]


def query_process_chain(specialty, clue_words):
    """线索词 → 工艺链。返回 {线索词: {工艺链, 列项建议, 口径, _来源}} 或 {}。

    clue_words: 文本或词列表, 在指定专业(及通用)的工艺链库中查找线索词。
    """
    chains = _load('process_chains.json')
    hits = {}
    words = clue_words if isinstance(clue_words, (list, tuple)) else [clue_words]
    hay = ' '.join(words)
    specialty = _norm_specialty(specialty)
    for sp in (specialty, '房屋建筑与装饰'):  # 房建作为通用兜底
        for clue, entry in chains.get(sp, {}).items():
            if any(w in hay or clue in w for w in words if w):
                hits.setdefault(clue, entry)
    return hits


def query_material(specialty, word):
    """材料名 → 特性(清单特征编写/组价判断用)。"""
    mats = _load('materials.json')
    specialty = _norm_specialty(specialty)
    for sp in (specialty,):
        for name, entry in mats.get(sp, {}).items():
            if word and (word in name or name in word):
                return dict(entry, 材料=name)
    return None


def query_rule(specialty, keyword):
    """分项关键词 → 计算规则(依据引用用)。"""
    rules = _load('calc_rules.json')
    specialty = _norm_specialty(specialty)
    for sp in (specialty, '房屋建筑与装饰'):
        for name, entry in rules.get(sp, {}).items():
            if keyword and (keyword in name or name in keyword):
                return dict(entry, 规则项=name)
    return None


def query_checklist(specialty, project_type_hint=''):
    """工程类型 → 漏项检查表(识图后过一遍)。"""
    cl = _load('type_checklists.json')
    specialty = _norm_specialty(specialty)
    items = cl.get(specialty, {})
    if project_type_hint:
        for k, v in items.items():
            if project_type_hint in k or k in project_type_hint:
                return {k: v}
    return items


def collect_unverified():
    """收集知识库中 ⚠️/❌/待核 条目 → 待查证清单(交接下一轮联网查证, 不假装知道)。"""
    out = []
    for fname in ('process_chains.json', 'materials.json', 'calc_rules.json', 'unit_prices.json'):
        try:
            data = _load(fname)
        except Exception:
            continue
        stack = [('', data)]
        while stack:
            path, node = stack.pop()
            if isinstance(node, dict):
                for k, v in node.items():
                    if k.startswith('_'):
                        continue
                    stack.append((f'{path}/{k}', v))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    stack.append((f'{path}[{i}]', v))
            elif isinstance(node, str):
                if '⚠️' in node or '❌' in node or '待核' in node:
                    out.append({'条目': path, '待查证': node[:100]})
    return out[:30]


def run_knowledge_checks(pid):
    """识图结果 → 知识库触发检查(边做边查)。

    返回 {工艺链命中, 材料提示, 规则依据, 漏项检查, 图纸疑问建议}
    """
    specialty = pid.get('专业类型', '房屋建筑与装饰工程')
    texts = ' '.join(pid.get('施工说明', []) or [])
    # 工艺链线索词(全专业)
    specialty = _norm_specialty(specialty)
    CLUES = ['防水', '屋面', '混凝土', '门窗', '高强螺栓', '钢构件', '管道', '电缆',
             '道路', '沥青', '苗木', '砌筑', '抹灰', '吊顶', '桥架', '设备', '消防', '通风']
    hits = query_process_chain(specialty, CLUES)
    out = {'工艺链命中': {}, '材料提示': [], '规则依据': {}, '漏项检查': [], '图纸疑问建议': []}
    for clue, entry in hits.items():
        out['工艺链命中'][clue] = entry.get('列项建议', [])
        # 口径含"⚠️/待核"的 → 图纸疑问建议(诚实机制: 查证痕迹)
        for k, v in (entry.get('口径', {}) or {}).items():
            if '⚠️' in str(v) or '待核' in str(v):
                out['图纸疑问建议'].append(f'[{clue}] {k}: {v}')
    # 材料提示
    for mword in ('Q345', 'Q355', 'Q235', '砂浆', '涂料', '防水材料', '沥青', '钢管', '电缆'):
        m = query_material(specialty, mword)
        if m:
            out['材料提示'].append(f"{m.get('材料', mword)}: {m.get('特性', '')}"
                                   f"({m.get('价格档次', m.get('规格维度', ''))})")
    # 规则依据
    for kw in ('防水', '混凝土', '门窗', '钢构件', '金属结构', '管道', '电缆'):
        r = query_rule(specialty, kw)
        if r:
            out['规则依据'][r.get('规则项', kw)] = r
    # 漏项检查(工程性质大修→大修表)
    nature = pid.get('工程性质', '')
    hint = '大修与改造' if '大修' in nature else ''
    cl = query_checklist(specialty, hint)
    for k, v in cl.items():
        out['漏项检查'].append(f'{k} 常见项: ' + '、'.join(v[:14]))
    return out
