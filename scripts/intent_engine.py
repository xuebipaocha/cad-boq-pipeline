# -*- coding: utf-8 -*-
"""设计意图推理引擎 — v6.3 A3

核心思想(用户确认): 不要机械式看图, 要像设计师一样先建立整体认知, 再落细部。
"不光看细部, 还要有大局观 — 设计内容/施工范围/各种信息都要结合来看"。

推理框架: 假设-验证
  ① 假设生成(L3 全局): 根据 专业+性质+用途+图名 → 假设工程类型与算量边界
  ② 证据收集(L1/L2): 设计说明 + 局部注释 + 做法 + 材料规格 → 支持或反驳
  ③ 边界确定: 算量范围 = 假设 ∩ 证据(含项-不含项)
  ④ 参数推断: 范围内各分项的 材料/厚度/含量/高度(设计常识)
  ⑤ 输出: 推理结论 + 证据链(可追溯)

用法:
  from intent_engine import infer_design_intent
  intent = infer_design_intent(pid)   # pid: 识图结果(含 设计说明/局部注释/施工说明/专业/工程性质)
"""
import re

# ── 设计常识库(推断算量参数) ──
# 部位+做法 → 推断参数
COMMON_SENSE = [
    # 卫生间/厨房 防水上翻高度
    {'条件': {'部位': ['卫生间', '洗手间', '浴室', '厨房'], '做法': ['防水']},
     '推断': {'防水上翻_m': 0.3}, '备注': '常规防水上翻 300mm'},
    # 卫生间/厨房 墙砖高度
    {'条件': {'部位': ['卫生间', '洗手间', '浴室', '厨房'], '材料': ['釉面砖', '瓷砖', '墙砖']},
     '推断': {'墙砖高度_m': 2.4}, '备注': '常规墙面贴砖至 2.4m'},
    # 阳台/卫生间 找坡
    {'条件': {'部位': ['阳台', '卫生间', '浴室'], '做法': ['找坡', '找平']},
     '推断': {'找坡系数': 0.01}, '备注': '常规找坡 1%'},
    # 踢脚线高度
    {'条件': {'做法': ['踢脚']},
     '推断': {'踢脚高度_mm': 100}, '备注': '常规踢脚 100mm'},
    # 乳胶漆 常规两遍
    {'条件': {'材料': ['乳胶漆']},
     '推断': {'涂刷遍数': 2}, '备注': '常规乳胶漆两遍'},
    # 石材地面 常规厚度
    {'条件': {'材料': ['大理石', '花岗岩', '石材']},
     '推断': {'厚度_mm': 20}, '备注': '常规石材 20mm'},
    # 大修工程 → 默认含拆除
    {'条件': {'性质': ['大修']},
     '推断': {'含拆除': True}, '备注': '大修工程默认含拆除分项'},
    # 新建工程 → 不含拆除
    {'条件': {'性质': ['新建']},
     '推断': {'含拆除': False}, '备注': '新建工程不含拆除'},
]

# 部位关键词(与做法表/房间匹配)
POSITION_KW = ['卫生间', '洗手间', '浴室', '厨房', '阳台', '客厅', '卧室', '书房',
               '办公室', '会议室', '走廊', '楼梯间', '外墙', '内墙', '天棚', '地面',
               '屋面', '踢脚', '门套', '窗台']


def _has_any(text, kws):
    return any(k in text for k in kws)


def infer_design_intent(pid):
    """设计意图推理主入口。

    pid: 识图结果(含 设计说明/局部注释/施工说明/专业类型/工程性质)
    返回:
      {
        '全局理解': {'工程性质': str, '专业': str, '用途': str, '设计标准': str},
        '算量边界': {'含拆除': bool, '含项': [], '不含项': [], '边界说明': str},
        '参数推断': [{'部位': str, '做法': str, '推断': {...}, '依据': str}],
        '证据链': [str...],
      }
    """
    out = {
        '全局理解': {}, '算量边界': {}, '参数推断': [], '证据链': [],
    }
    dn = pid.get('设计说明') or {}
    notes = pid.get('局部注释') or []
    texts = [str(t) for t in (pid.get('施工说明') or [])]
    all_text = '\n'.join(texts) + '\n' + str(dn)

    eng = dn.get('工程内容') or {}
    profile = dn.get('工程概况') or {}

    # ── ① 全局理解 ──
    g = out['全局理解']
    g['工程性质'] = eng.get('性质') or pid.get('工程性质') or ''
    g['专业'] = pid.get('专业类型') or ''
    g['用途'] = eng.get('用途') or ''
    if profile.get('建筑面积'):
        g['建筑面积'] = profile['建筑面积']
    if profile.get('层数'):
        g['层数'] = profile['层数']
    if profile.get('檐高'):
        g['檐高'] = profile['檐高']
    out['证据链'].append(f"全局: 性质={g['工程性质'] or '未知'} 专业={g['专业']} 用途={g['用途'] or '未知'}")
    if not g['工程性质']:
        out['证据链'].append('注: 工程性质未在说明中识别, 以识图判定为准')

    # ── ② 算量边界(含/不含项 → 边界) ──
    b = out['算量边界']
    b['含项'] = eng.get('含项') or []
    b['不含项'] = eng.get('不含项') or []
    # 性质 → 拆除边界
    nature = g['工程性质']
    if nature in ('大修', '扩建'):
        b['含拆除'] = True
        b['边界说明'] = '大修/扩建工程: 默认含拆除分项(除非明确不含)'
    elif nature == '新建':
        b['含拆除'] = False
        b['边界说明'] = '新建工程: 不含拆除分项'
    else:
        b['含拆除'] = None
        b['边界说明'] = '工程性质未知: 拆除边界待确认'
    if b['不含项']:
        b['边界说明'] += f"；明确不含: {'、'.join(b['不含项'][:3])}"
        out['证据链'].append(f"边界: 不含 {b['不含项'][:3]}")
    if b['含项']:
        out['证据链'].append(f"边界: 含 {b['含项'][:3]}")

    # ── ③ 参数推断(设计常识: 部位+材料+做法 → 参数) ──
    # 收集证据文本: 局部注释 + 设计说明材料/做法
    evidence_pool = []
    for n in notes:
        evidence_pool.append((n.get('部位') or '', n.get('注释') or '', n.get('类型') or ''))
    for spec in dn.get('材料规格') or []:
        evidence_pool.append(('', f"材料规格{spec.get('规格', '')}", '规格'))
    for layer in dn.get('做法层次') or []:
        evidence_pool.append((layer.get('名称') or '', ' '.join(layer.get('层次') or []), '做法'))

    for rule in COMMON_SENSE:
        cond = rule['条件']
        matched = False
        match_pos, match_txt = '', ''
        for pos, txt, ntype in evidence_pool:
            # 部位匹配
            if cond.get('部位'):
                if not _has_any(pos + ' ' + txt, cond['部位']):
                    continue
            # 做法匹配
            if cond.get('做法'):
                if not _has_any(txt, cond['做法']):
                    continue
            # 材料匹配
            if cond.get('材料'):
                if not _has_any(txt, cond['材料']):
                    continue
            # 性质匹配
            if cond.get('性质'):
                if nature not in cond['性质']:
                    continue
            matched = True
            match_pos, match_txt = pos, txt
            break
        if matched:
            infer = dict(rule['推断'])
            # 证据覆盖: 证据文本中的实际数值优先于常识(如"乳胶漆三遍"→3遍)
            m_bian = re.search(r'([一二两三]|[1-3])遍', match_txt)
            if m_bian and '遍' in str(infer):
                n = {'一': 1, '二': 2, '三': 3}.get(m_bian.group(1), m_bian.group(1))
                infer = {'涂刷遍数': int(n)}
            m_thick = re.search(r'(\d+)\s*mm', match_txt)
            if m_thick and '厚度_mm' in str(infer):
                infer['厚度_mm'] = int(m_thick.group(1))
            out['参数推断'].append({
                '部位': match_pos,
                '推断': infer,
                '依据': rule['备注'],
                '证据': match_txt[:40],
            })
            out['证据链'].append(f"参数: {rule['备注']} (依据: {(match_txt or '')[:30]})")

    # 去重推断
    seen, uniq = set(), []
    for item in out['参数推断']:
        k = (item.get('依据'), item.get('部位'))
        if k not in seen:
            seen.add(k)
            uniq.append(item)
    out['参数推断'] = uniq
    return out


if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.stdout.reconfigure(encoding='utf-8')
    import step1_recognize as s1
    out = os.path.join(os.environ.get('TEMP', '.'), '_intent')
    os.makedirs(out, exist_ok=True)
    pid = s1.run('../benchmarks/cases/精装规范/drawings/精装_住宅规范.dxf', out)
    r = infer_design_intent(pid)
    print('全局理解:', r['全局理解'])
    print('算量边界:', r['算量边界'])
    print('参数推断:', r['参数推断'][:5])
    print('证据链:', r['证据链'][:6])
