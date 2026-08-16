# -*- coding: utf-8 -*-
"""造价经验指标库与自动复核意见 — v6.7

模拟专业造价工程师的"心里有数"自检: 算完量先算单方含量,
对照行业经验区间, 超限则给出可追溯的复核意见(哪项、实际值、区间、计算式)。

指标区间来源(2026-08-16 联网检索, 标注可靠性):
- 广联达服务新干线专家基准表(2009, 全网 2302 赞, 抗震7度区规则结构, 被 2022 年
  多源全文转载一致): fwxgx.com/questions/341389
  - 钢筋: 砖混30 / 多层框架38-42 / 小高层11-12层50-52 / 高层17-18层54-60 /
          高层30层65-75 / 框架含基础全口径65-70
  - 混凝土: 砖混0.30-0.33 / 框架0.33-0.35 / 小高层0.35 / 高层0.36-0.47
  - 室内抹灰系数(含天棚): 多层住宅≈3.8; 室外≈0.4
  - 门窗占建筑面积: 室外0.20-0.24, 含室内0.35-0.43
  - 模板接触面积/建筑面积: 多层住宅≈2.2
- 轻钢厂房用钢量 20-50kg/m²(GB 51022-2015 门式刚架引用, 无行车25-30/带行车35-40):
  zhidao.baidu.com/question/416021784 / b2b.baidu.com
- 内墙抹灰系数(仅墙面): 0.62-0.67 测算 0.8 取定(楼盘网): m.loupan.com/ask/920911

使用注意(与来源一致):
- 指标仅用于衡量估算是否"大起大落", 不能替代按图算量; 复核容差 ±10-15%
- 基准表适用抗震7度区、规则结构、地上部分为主
"""
import re

# 指标区间: {指标名: [(结构类型关键词, (下限, 上限), 口径说明, 来源)]}
INDICATORS = {
    '钢筋含量kg/m²': [
        (['砌体', '砖混'], (30, 40), '砖混住宅30 / 全口径含基础35-40', '广联达服务新干线'),
        (['框架'], (38, 70), '多层框架38-42 / 框架含基础65-70', '广联达服务新干线'),
        (['剪力墙', '小高层'], (50, 60), '小高层11-12层50-52 / 高层17-18层54-60', '广联达服务新干线'),
        (['高层'], (54, 75), '高层17-18层54-60 / 30层65-75', '广联达服务新干线'),
    ],
    '混凝土含量m³/m²': [
        (['砌体', '砖混'], (0.30, 0.33), '砖混住宅', '广联达服务新干线'),
        (['框架'], (0.33, 0.40), '多层框架0.33-0.35 / 小高层0.35', '广联达服务新干线'),
        (['高层'], (0.36, 0.47), '高层17-18层0.36 / 30层0.42-0.47', '广联达服务新干线'),
    ],
    '室内抹灰系数(抹灰面积/建筑面积)': [
        (['住宅'], (2.0, 4.0), '多层住宅含天棚≈3.8; 仅墙面0.62-0.85', '广联达/楼盘网'),
    ],
    '门窗面积/建筑面积': [
        (['住宅'], (0.16, 0.43), '室外0.20-0.24 / 含室内0.35-0.43 / 实际工程0.16-0.18', '广联达服务新干线'),
    ],
    '模板面积/建筑面积': [
        (['住宅'], (2.2, 4.0), '多层住宅≈2.2 / 高层剪力墙3-4', '广联达服务新干线'),
    ],
    '钢结构厂房用钢量kg/m²': [
        (['轻钢', '厂房', '钢结构', '门式刚架'], (20, 60), '无行车25-30 / 带行车35-40 / 跨度24m+ 35-50', 'GB51022-2015 引用'),
    ],
}


def detect_structure_type(texts):
    """从施工说明/设计说明判定结构类型关键词。"""
    hay = ' '.join(texts or [])
    found = []
    for kw in ('剪力墙', '砖混', '砌体', '框架', '轻钢', '门式刚架', '钢结构'):
        if kw in hay:
            found.append(kw)
    # 建筑类型
    for kw in ('厂房', '仓库', '车间'):
        if kw in hay:
            found.append(kw)
            break
    for kw in ('住宅', '办公楼', '酒店', '学校', '医院'):
        if kw in hay:
            found.append(kw)
            break
    return found or ['未知']


def _first_range(structs, table):
    """按结构关键词选区间(第一个命中的关键词行)。"""
    for kw_list, rng, note, src in table:
        if any(k in structs for k in kw_list):
            return rng, note, src
    return None, None, None


def calc_indicators(pid, results):
    """计算单方含量指标实际值。

    返回 {指标名: {'实际': v, '区间': (lo,hi), '口径': note, '来源': src,
                  '计算式': str, '状态': '正常'|'超限'|'无数据'}}
    """
    structs = detect_structure_type(pid.get('施工说明', []))
    bfa = 0.0
    for a in pid.get('面积区域', []):
        bfa = max(bfa, float(a.get('面积_m2', 0) or 0))
    out = {}

    def _sum_qty(keywords, unit):
        s = 0.0
        for it in results:
            nm = it.get('分项名称', '')
            if any(k in nm for k in keywords) and it.get('单位') == unit:
                s += float(it.get('工程量', 0) or 0)
        return s

    if bfa <= 0:
        return {k: {'状态': '无数据', '计算式': '无建筑面积'} for k in INDICATORS}

    # 钢筋 kg/m²
    rebar_t = _sum_qty(['钢筋'], 't')
    if rebar_t:
        v = rebar_t * 1000 / bfa
        rng, note, src = _first_range(structs, INDICATORS['钢筋含量kg/m²'])
        out['钢筋含量kg/m²'] = {
            '实际': round(v, 1), '区间': rng, '口径': note, '来源': src,
            '计算式': f'{rebar_t}t×1000÷{bfa:.0f}m²={v:.1f}kg/m²',
            '状态': '超限' if rng and not (rng[0] * 0.85 <= v <= rng[1] * 1.15) else '正常',
        }
    # 混凝土 m³/m²
    con_v = _sum_qty(['混凝土', '现浇', '砌体'], 'm³')
    if con_v:
        v = con_v / bfa
        rng, note, src = _first_range(structs, INDICATORS['混凝土含量m³/m²'])
        out['混凝土含量m³/m²'] = {
            '实际': round(v, 3), '区间': rng, '口径': note, '来源': src,
            '计算式': f'{con_v}m³÷{bfa:.0f}m²={v:.3f}m³/m²',
            '状态': '超限' if rng and not (rng[0] * 0.85 <= v <= rng[1] * 1.15) else '正常',
        }
    # 室内抹灰系数
    plaster = _sum_qty(['抹灰', '涂料', '乳胶漆'], 'm²')
    if plaster:
        v = plaster / bfa
        rng, note, src = _first_range(structs, INDICATORS['室内抹灰系数(抹灰面积/建筑面积)'])
        out['室内抹灰系数(抹灰面积/建筑面积)'] = {
            '实际': round(v, 2), '区间': rng, '口径': note, '来源': src,
            '计算式': f'{plaster}m²÷{bfa:.0f}m²={v:.2f}',
            '状态': '超限' if rng and not (rng[0] * 0.85 <= v <= rng[1] * 1.15) else '正常',
        }
    # 门窗占比
    win_area = _sum_qty(['门窗'], 'm²')
    if win_area:
        v = win_area / bfa
        rng, note, src = _first_range(structs, INDICATORS['门窗面积/建筑面积'])
        out['门窗面积/建筑面积'] = {
            '实际': round(v, 3), '区间': rng, '口径': note, '来源': src,
            '计算式': f'{win_area}m²÷{bfa:.0f}m²={v:.3f}',
            '状态': '超限' if rng and not (rng[0] * 0.85 <= v <= rng[1] * 1.15) else '正常',
        }
    # 钢结构用钢量
    steel_t = _sum_qty(['制作安装'], 't')
    if steel_t:
        v = steel_t * 1000 / bfa
        rng, note, src = _first_range(structs, INDICATORS['钢结构厂房用钢量kg/m²'])
        out['钢结构厂房用钢量kg/m²'] = {
            '实际': round(v, 1), '区间': rng, '口径': note, '来源': src,
            '计算式': f'{steel_t}t×1000÷{bfa:.0f}m²={v:.1f}kg/m²',
            '状态': '超限' if rng and not (rng[0] * 0.85 <= v <= rng[1] * 1.15) else '正常',
        }
    return out


def review_opinions(pid, results):
    """超限指标 → 造价复核意见(像人写的可追溯意见)。

    返回 [{'指标', '实际值', '经验区间', '计算式', '结构类型', '来源', '意见'}]
    """
    structs = detect_structure_type(pid.get('施工说明', []))
    inds = calc_indicators(pid, results)
    ops = []
    for name, d in inds.items():
        if d.get('状态') != '超限':
            continue
        lo, hi = d['区间'] or (0, 0)
        ops.append({
            '指标': name,
            '实际值': d['实际'],
            '经验区间': f'{lo}-{hi}',
            '口径': d.get('口径', ''),
            '计算式': d.get('计算式', ''),
            '结构类型': '/'.join(structs),
            '来源': d.get('来源', ''),
            '意见': f'{name} 实测 {d["实际"]}{name.split("(")[0].replace("kg/m²","").replace("m³/m²","")}'
                    f'，经验区间 {lo}-{hi}（{d.get("口径","")}），超限建议复核对应分项的计算口径'
                    f'（部位/扣减/层数/单位），确认非图纸特殊设计后保留',
        })
    return ops
