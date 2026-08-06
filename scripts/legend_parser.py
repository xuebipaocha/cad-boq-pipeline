# -*- coding: utf-8 -*-
"""图例表解析 — v6.3 B2

图例表(符号↔构件)在真实图纸中常见: 园林苗木图例 / 安装设备图例 / 装饰材料图例。
图例区通常是"图例"标题 + 符号(图块/短线) + 名称文字 的成对结构。

由于符号是图形而非文字, 本解析器:
1. 定位"图例"标题位置
2. 提取该区域内成对的 (符号描述, 名称文字)
3. 名称文字 → 构件类型推断(苗木/设备/材料/符号)

用法:
  from legend_parser import parse_legends
  legends = parse_legends(msp)
"""
import re

# 图例名称 → 构件类型
LEGEND_TYPE_KW = {
    '苗木': ['乔木', '灌木', '绿篱', '草坪', '地被', '花卉', '水生', '胸径', '冠幅', '株高', '地径', '香樟', '银杏', '桂花', '栾树'],
    '设备': ['配电箱', '灯具', '开关', '插座', '风机', '水泵', '阀门', '探测器', '喷头', '消火栓'],
    '材料': ['地砖', '石材', '木材', '涂料', '板材', '玻璃'],
    '道路': ['路缘石', '井盖', '雨水口', '标志'],
    '管道': ['给水', '排水', '燃气', '热力', '电力', '通信'],
}
# 排除(非图例内容)
EXCLUDE = ['图例', '符号', '名称', '说明', '备注']


def parse_legends(msp, area_margin=3000):
    """解析图纸中的图例区。

    msp: modelspace
    返回: [{名称, 类型, x, y, 上下文}]
    """
    # 1. 收集文字(含位置)
    texts = []
    for e in msp:
        if e.dxftype() not in ('TEXT', 'MTEXT'):
            continue
        try:
            txt = (e.dxf.text if e.dxftype() == 'TEXT' else e.text) or ''
        except Exception:
            continue
        if txt.strip():
            texts.append((txt.strip(), (e.dxf.insert.x, e.dxf.insert.y)))
    # 2. 定位"图例"标题
    legends = []
    for i, (txt, (x, y)) in enumerate(texts):
        if txt != '图例' and '图例' not in txt[:4]:
            continue
        # 图例标题位置 → 找其右侧/下方的名称文字(同一区域)
        region = []
        for t2, (x2, y2) in texts:
            if t2 in EXCLUDE:
                continue
            # 图例区: 标题右侧 0~20000mm, 下方 0~20000mm
            dx, dy = x2 - x, y - y2
            if 0 <= dx <= 30000 and 0 <= dy <= 30000 and len(t2) <= 20:
                region.append((t2, dx, dy))
        # 按距离排序取最近 30 个
        region.sort(key=lambda r: r[1] + r[2])
        for t2, dx, dy in region[:30]:
            ltype = '其他'
            for lt, kws in LEGEND_TYPE_KW.items():
                if any(k in t2 for k in kws):
                    ltype = lt
                    break
            legends.append({'名称': t2, '类型': ltype,
                            'x': round(dx, 0), 'y': round(dy, 0),
                            '上下文': f'图例@{x:.0f},{y:.0f}'})
    # 去重(按名称)
    seen, out = set(), []
    for l in legends:
        if l['名称'] not in seen:
            seen.add(l['名称'])
            out.append(l)
    return out


if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 2:
        print('用法: python legend_parser.py 图纸.dxf')
        sys.exit(1)
    import ezdxf
    doc = ezdxf.readfile(sys.argv[1])
    legends = parse_legends(doc.modelspace())
    print(f'图例 {len(legends)} 条:')
    for l in legends[:20]:
        print(f"  [{l['类型']}] {l['名称']}")
