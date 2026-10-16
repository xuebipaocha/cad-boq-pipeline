# -*- coding: utf-8 -*-
"""m3 按图归属去重汇总（v6.10.14）。

问题：261012 的 7 张图**各自独立跑** pipeline，同一实体在外轮廓共用/多图区重复绘制时
会被各图分别计入 → 汇总出现 13 组同名分项（建筑图 vs 结构图占 11 组），造成**重复计量**。

去重规则（已固化于交叉验证报告，见 PRINCIPLES.md 「图纸取证与分区原则」）：
  ① **结构性分项**（现浇混凝土 / 钢筋 / 模板 / 柱梁板墙）→ 只认**结构图**（混凝土结构 / 钢结构）；
  ② **装饰性分项**（涂料 / 抹灰 / 门窗 / 地面 / 块料 / 吊顶）→ 只认**建筑图**；
  ③ **安装分项**（管道 / 阀门 / 电缆 / 风管 / 器具 / 消防 / 给排水 / 暖通 / 电气）→
     **各专业各自计量**（口径不同，不算重复）；
  ④ 其余（总图/场地类）→ 按图归属，不跨图去重。

输出：去重后的汇总表 + 被剔除的"重复来源(不计量)"清单。
"""
import json
import os
from collections import defaultdict

BASE = os.path.join(os.environ['LOCALAPPDATA'], 'Temp', '261012_out')
FIGS = [('0-总图', '总图'), ('1-建筑', '建筑'), ('t21', '钢结构'), ('t22', '混凝土结构'),
        ('3-给排水', '给排水'), ('4-暖通', '暖通'), ('5-电气', '电气')]

STRUCT_KW = ('现浇混凝土', '钢筋', '模板')
DECOR_KW = ('涂料', '抹灰', '门窗', '地面', '块料', '吊顶', '油漆', '喷刷', '墙裙')
INSTALL_KW = ('管', '阀门', '电缆', '导线', '风管', '器具', '消防', '给水', '排水',
              '通风', '燃气', '采暖', '卫生', '配电', '照明', '桥架', '母线')
STRUCT_OWNERS = ('混凝土结构', '钢结构')
DECOR_OWNERS = ('建筑',)


def kind_of(name):
    n = str(name)
    if any(k in n for k in STRUCT_KW):
        return '结构'
    if any(k in n for k in DECOR_KW):
        return '装饰'
    if any(k in n for k in INSTALL_KW):
        return '安装'
    return '其他'


def main():
    rows = []          # (图, 专业, 分项, 量, 单位, 类别)
    for d, label in FIGS:
        p = os.path.join(BASE, d, '识图结果.json')
        q = os.path.join(BASE, d, '算量结果.json')
        if not (os.path.exists(p) and os.path.exists(q)):
            continue
        prof = (json.load(open(p, encoding='utf-8')) or {}).get('专业类型')
        for it in json.load(open(q, encoding='utf-8')):
            n = str(it.get('分项名称') or '')
            rows.append((label, prof, n, float(it.get('工程量') or 0),
                         it.get('单位'), kind_of(n)))

    # ── 按"分项名 + 单位"分组 → 归属判定 ──
    groups = defaultdict(list)
    for r in rows:
        groups[(r[2], r[4])].append(r)

    kept, dropped = [], []
    for (name, unit), items in groups.items():
        if len(items) == 1:
            kept.append(items[0])
            continue
        k = items[0][5]
        figs = {r[0] for r in items}
        owner = None
        if k == '结构':
            owner = next((f for f in STRUCT_OWNERS if f in figs), None)
        elif k == '装饰':
            owner = next((f for f in DECOR_OWNERS if f in figs), None)
        if owner:                                  # 有明确归属 → 只保留主图，其余标重复
            for r in items:
                (kept if r[0] == owner else dropped).append(r)
        else:                                      # 安装类/无归属 → 各图各自计量，全保留
            kept.extend(items)

    # ── 输出 ──
    print('=' * 78)
    print('【去重后汇总】%d 条（原 %d 条，剔除重复 %d 条）' % (len(kept), len(rows), len(dropped)))
    byfig = defaultdict(float)
    for r in kept:
        byfig[r[0]] += 1
    print('%-12s %s' % ('图', '保留分项数'))
    for _, label in FIGS:
        if byfig.get(label):
            print('  %-10s %3d' % (label, int(byfig[label])))
    print()
    print('【被剔除的重复来源（不计量）】')
    for r in dropped:
        print('  %-12s %-30s %10s %s  [%s类·归属%s]'
              % (r[0], r[2][:30], r[3], r[4], r[5], '结构图' if r[5] == '结构' else '建筑图'))
    print()
    # 关键合计
    conc = sum(r[3] for r in kept if '现浇混凝土' in r[2] and r[4] == 'm³')
    steel = sum(r[3] for r in kept if r[2] == '钢筋')
    print('【去重后关键量】混凝土合计 %.2f m³ | 钢筋合计 %.2f t' % (conc, steel))
    out = os.path.join(os.path.dirname(BASE), '261012_去重汇总.json')
    json.dump({'kept': kept, 'dropped': dropped}, open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('RESULTN 明细已落盘: %s' % out)


if __name__ == '__main__':
    main()
