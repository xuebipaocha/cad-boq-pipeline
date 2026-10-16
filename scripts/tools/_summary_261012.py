# -*- coding: utf-8 -*-
"""P5 汇总与交叉验证：261012 项目 7 图算量 → 总表 + 一致性/量级核查。一次性工具。

交叉验证四类（对应"交叉反复验证"要求）：
  ① **跨图重复**：同一分项在多图重复出现（如"外墙涂料"同时出现在 1-建筑 与 2-2 基础图）→ 提示串味
  ② **占位/未提取**：分项名含"未标/未注明/待提取"→ 说明关键规格未提取
  ③ **量级合理性**：钢筋 t 与混凝土 m³ 的比值、单方含钢量是否落在经验区间
  ④ **零产出/空图**：算量 0 项或面积缺失的图 → 不可交付
"""
import json
import os

BASE = os.path.join(os.environ['LOCALAPPDATA'], 'Temp', '261012_out')
FIGS = [('0-总图', '总图'), ('1-建筑', '建筑'), ('t21', '钢结构'),
        ('t22', '混凝土结构'), ('3-给排水', '给排水'),
        ('4-暖通', '暖通'), ('5-电气', '电气')]


def load(d, fn):
    p = os.path.join(BASE, d, fn)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None


def main():
    rows, per_fig, placeholders, missing_area = [], {}, [], []
    for d, label in FIGS:
        pid = load(d, '识图结果.json') or {}
        res = load(d, '算量结果.json') or []
        per_fig[label] = {'专业': pid.get('专业类型'), '性质': pid.get('工程性质'),
                          '面积': pid.get('建筑面积_m2'), '算量项': len(res)}
        if not pid.get('建筑面积_m2'):
            missing_area.append(label)
        for it in res:
            n = str(it.get('分项名称') or '')
            rows.append({'图': label, '专业': pid.get('专业类型'), '分项': n,
                         '量': it.get('工程量'), '单位': it.get('单位')})
            if any(k in n for k in ('未标', '未注明', '待提取', '未知')):
                placeholders.append((label, n))

    # ① 跨图重复
    byname = {}
    for r in rows:
        byname.setdefault(r['分项'], []).append(r['图'])
    dup = {k: v for k, v in byname.items() if len(set(v)) > 1}

    # ③ 量级核查
    def _sum(label, key):
        return sum(float(r['量'] or 0) for r in rows if r['图'] == label and key in r['分项'])
    conc = _sum('混凝土结构', '混凝土')
    steel = _sum('混凝土结构', '钢筋')
    stl = _sum('钢结构', '制作安装')

    print('=' * 72)
    print('① 各图产出总览')
    for d, label in FIGS:
        v = per_fig[label]
        print('   %-8s %-14s 性质%-6s 面积%-10s 算量%3d 项' % (
            label, str(v['专业'])[:12], str(v['性质'] or '—'), str(v['面积'] or '—'), v['算量项']))

    print('\n② 跨图重复分项（可能串味，需人工判是否重复计量）')
    for k, v in sorted(dup.items(), key=lambda x: -len(x[1]))[:12]:
        print('   %-22s 出现在: %s' % (k[:22], '、'.join(sorted(set(v)))))

    print('\n③ 占位/未提取分项（规格未读到 → 量不可用）共 %d 条' % len(placeholders))
    pc = {}
    for f, n in placeholders:
        pc[n] = pc.get(n, 0) + 1
    for n, c in sorted(pc.items(), key=lambda x: -x[1])[:10]:
        print('   %-26s × %d' % (n[:26], c))

    print('\n④ 量级核查')
    print('   混凝土结构：混凝土 %.2f m³ / 钢筋 %.2f t → 含钢量 %.0f kg/m³（经验 100~180）'
          % (conc, steel, (steel * 1000 / conc) if conc else 0))
    print('   钢结构：制作安装合计 %.2f t（厂房常见数十~数百 t）' % stl)
    print('   缺建筑面积的图：%s' % ('、'.join(missing_area) or '无'))

    print('\nRESULTN 汇总 %d 条分项 / 跨图重复 %d 组 / 占位 %d 条 / 缺面积 %d 图'
          % (len(rows), len(dup), len(placeholders), len(missing_area)))
    out = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'Temp', '261012_汇总.json')
    json.dump({'rows': rows, 'per_fig': per_fig, 'dup': dup,
               'placeholders': placeholders, 'missing_area': missing_area},
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('   明细已落盘: %s' % out)


if __name__ == '__main__':
    main()
