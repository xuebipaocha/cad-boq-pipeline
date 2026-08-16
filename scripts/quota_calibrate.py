# -*- coding: utf-8 -*-
"""定额成本基数校准 — v6.9.1

问题: 辽宁24定额库重建时丢失了"计量单位系数" — 成本字段(labor/material/machine)
按 10/100/1000 倍单位编制, 但 unit 只存了主单位(m²/m³/t/m/个...)。
step5 组价直接用 labor_cost×量 → 抹灰人工 1801.58 元/m²(实际 18 元) — 单价放大
100 倍(实测: 墙面一般抹灰 2579 元/m²、防水 4143 元/m² 的离谱单价根因)。

解法: 经验人工价反向校准 — 子目 labor_cost ÷ 经验人工价(按名称关键词分类) → 
最接近的 1/10/100/1000 即该子目成本基数。生成 data/quota_calibration.json,
step5 组价时除以基数。

校准依据(经验人工价, 元/主单位, 综合辽宁定额水平, ⚠️近似值):
- 抹灰/粉刷 m²≈10, 防水 m²≈5, 涂料 m²≈5, 模板 m²≈20, 铺装 m²≈30
- 挖土 m³≈25, 混凝土 m³≈45, 砌体 m³≈150
- 钢筋 t≈600, 钢结构制安 t≈1500, 钢构件防腐/防火 t≈300
- 管道 m≈20, 桥架 m≈15, 电缆 m≈5, 侧石 m≈15
- 阀门 个≈40, 门/窗 樘≈150, 配电箱 台≈200, 泵 台≈220, 设备 台≈300
- 苗木 株≈30
未命中分类的子目不校准(保持原值, 由单价校验报警兜底)。
"""
import json
import os
import sqlite3
import statistics

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(BASE), 'data')

# 分类 → (经验人工价, 主单位) — 顺序重要, 前面优先
LABOR_EXPECT = [
    (['抹灰', '粉刷', '刮腻子', '内墙', '外墙', '找平'], 10, 'm²'),
    (['防水', '卷材', '涂膜', '聚氨酯', 'SBS', '玻纤', '纤维布', '油毡', '三油', '沥青玻璃'], 5, 'm²'),
    (['成品门', '成品窗', '门安装', '窗安装', '防盗门', '防火门', '钢质门', '塑钢门', '塑钢窗'], 30, 'm²'),
    (['涂料', '喷刷', '乳胶漆', '真石漆'], 5, 'm²'),
    (['模板'], 20, 'm²'),
    (['铺装', '园路', '广场'], 30, 'm²'),
    (['挖土', '挖一般土方', '挖基坑', '挖沟槽'], 25, 'm³'),
    (['混凝土', '砼'], 45, 'm³'),
    (['砌体', '砌块', '砖墙', '实心砖'], 150, 'm³'),
    (['钢筋'], 600, 't'),
    (['钢结构', '钢构件', '钢柱', '钢梁', '檩条', '钢屋架', '钢支撑'], 1500, 't'),
    (['制作安装'], 1500, 't'),
    (['防腐', '防火涂料'], 300, 't'),
    (['管道', '配管', '给水', '排水', '采暖'], 20, 'm'),
    (['桥架'], 15, 'm'),
    (['电缆', '线缆'], 5, 'm'),
    (['侧石', '路缘石', '边石'], 15, 'm'),
    (['阀门'], 40, '个'),
    (['门', '窗'], 150, '樘'),
    (['配电箱', '配电柜'], 200, '台'),
    (['泵'], 220, '台'),
    (['设备'], 300, '台'),
    (['苗木', '乔木', '灌木'], 30, '株'),
]

BASES = (1, 10, 100, 1000)


def _classify(name):
    for kws, price, unit in LABOR_EXPECT:
        if any(k in name for k in kws):
            return price, unit
    return None


def infer_base(labor, expect_price):
    """labor ÷ 经验价 → 最接近的 1/10/100/1000。返回基数或 None(无法判断)。"""
    if not labor or labor <= 0 or not expect_price:
        return None
    ratio = labor / expect_price
    best, best_dist = None, None
    for b in BASES:
        d = abs(ratio - b) / b
        if best_dist is None or d < best_dist:
            best, best_dist = b, d
    # 容差: 距最接近基数 40% 以内才采信(经验价±40% 波动可容忍)
    if best_dist <= 0.4:
        return best
    return None


def build_calibration(conn_path=None, out_path=None, verbose=True):
    conn = sqlite3.connect(conn_path or os.path.join(DATA, 'liaoning_24.db'))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT quota_code, item_name, labor_cost, material_cost, machine_cost, unit "
        "FROM quota_items WHERE labor_cost>0").fetchall()
    conn.close()
    calib = {}
    stats = {'校准': 0, '跳过': 0, '无匹配': 0}
    by_base = {}
    group_stats = {}  # (分类, 单位) → {基数: 次数}
    for r in rows:
        d = dict(r)
        cls = _classify(d['item_name'] or '')
        if not cls:
            stats['无匹配'] += 1
            continue
        price, unit = cls
        b = infer_base(d['labor_cost'], price)
        if b is None:
            stats['跳过'] += 1
            continue
        calib[d['quota_code']] = {'基数': b,
                                   '分类': next(kws[0] for kws, p, u in LABOR_EXPECT if any(kw in (d['item_name'] or '') for kw in kws)),
                                   '单位': unit, 'labor': d['labor_cost']}
        by_base[b] = by_base.get(b, 0) + 1
        stats['校准'] += 1
        # 分类+单位 → 基数统计(众数法, 输出规则表)
        clsname = next(kws[0] for kws, p, u in LABOR_EXPECT if any(kw in (d['item_name'] or '') for kw in kws))
        gk = (clsname, unit)
        g = group_stats.setdefault(gk, {})
        g[b] = g.get(b, 0) + 1
    # 规则表: (分类, 单位) → 众数基数(样本≥3 才输出, 防单条噪声)
    rules = []
    for (clsname, unit), counts in group_stats.items():
        total = sum(counts.values())
        if total < 3:
            continue
        base = max(counts, key=counts.get)
        # 众数占比 ≥60% 才采信(同一分类单位下基数应一致)
        if counts[base] / total >= 0.6:
            ks = next(ks_ for ks_, p, u in LABOR_EXPECT if ks_[0] == clsname)
            rules.append({'关键词组': ks, '单位': unit, '基数': base,
                          '样本': total, '占比': round(counts[base] / total, 2)})
    out_path = out_path or os.path.join(DATA, 'quota_calibration.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'规则': rules, '_说明': '成本基数规则表: step5 组价时按定额名称关键词+单位查基数, labor/material/machine 除以基数。'
                                        '由 quota_calibrate.py 从经验人工价反向校准生成(众数法)。'}, f, ensure_ascii=False, indent=1)
    if verbose:
        print(f"校准完成: 校准{stats['校准']}条 / 跳过{stats['跳过']}条(无法判断) / 无匹配{stats['无匹配']}条")
        print(f"基数分布: {by_base}")
        print(f"规则数: {len(rules)}")
        for r in rules[:25]:
            print(f"  {r['关键词组']} {r['单位']} 基数={r['基数']} 样本={r['样本']} 占比={r['占比']}")
        print(f"输出: {out_path}")
    return rules


if __name__ == '__main__':
    build_calibration()
