# -*- coding: utf-8 -*-
"""基准图生成器 — v5.0 A6

生成"房建构件"基准用例的两张测试图(首次为项目建立 ezdxf 测试图生成模式):
  1. 房建构件.dxf      — 合规框架: 24000×15000 板, 6 根 500×500 柱(3×2 网格),
                         梁-KL1 顶边 24000, 梁-KL2 中跨 12000, 平法标注, 层高3.0m
  2. 房建配筋不足.dxf  — 违规变体: 抗震设防 + 480 梁高 5m 跨(高跨比 1/10.4 低于
                         抗震下限 1/10) + 柱 300×300 无配筋

关键设计: 梁长必须真实(=24000), 打破现有基准"梁长默认 4.5m"无区分度的问题。

用法: cd scripts && python tools/gen_case_room.py
输出: ../benchmarks/cases/房建构件/drawings/*.dxf + ../benchmarks/cases/房建配筋不足/drawings/*.dxf
"""
import os
import sys

if sys.stdout.encoding and 'utf-8' not in sys.stdout.encoding.lower():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import ezdxf

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'benchmarks', 'cases')


def _text(msp, x, y, s, h=350, layer='说明'):
    msp.add_text(s, dxfattribs={'height': h, 'layer': layer}).set_placement((x, y))


def _make_case(prefix, beam_h, column_size, seismic_text, beam_section_text):
    doc = ezdxf.new('R2010', setup=True)
    doc.units = 4  # mm
    msp = doc.modelspace()

    # 图框(0层, 大矩形 — 面积≥2倍板轮廓, 触发 analyze_cad 的 0层图框排除逻辑)
    msp.add_lwpolyline([(0, 0), (40000, 0), (40000, 30000), (0, 30000)],
                       close=True, dxfattribs={'layer': '0'})

    # 板轮廓(闭合多段线)
    msp.add_lwpolyline([(0, 0), (24000, 0), (24000, 15000), (0, 15000)],
                       close=True, dxfattribs={'layer': '板-LB1'})

    # 柱: 3×2 网格, 500×500 矩形, 柱距 X=12000 / Y=9000
    for i, (cx, cy) in enumerate([(0, 0), (12000, 0), (24000, 0),
                                  (0, 9000), (12000, 9000), (24000, 9000)]):
        msp.add_lwpolyline([(cx, cy), (cx + 500, cy), (cx + 500, cy + 500), (cx, cy + 500)],
                           close=True, dxfattribs={'layer': f'柱-KZ1-{i + 1}'})

    # 梁: 顶边 24000 + 中跨 12000(横贯)
    msp.add_lwpolyline([(0, 15000), (24000, 15000)], dxfattribs={'layer': '梁-KL1'})
    msp.add_lwpolyline([(0, 4500), (12000, 4500)], dxfattribs={'layer': '梁-KL2'})

    # 墙体: 200 厚(4 边)
    msp.add_lwpolyline([(0, 0), (24000, 0)], dxfattribs={'layer': '墙体'})
    msp.add_lwpolyline([(24000, 0), (24000, 15000)], dxfattribs={'layer': '墙体'})
    msp.add_lwpolyline([(24000, 15000), (0, 15000)], dxfattribs={'layer': '墙体'})
    msp.add_lwpolyline([(0, 15000), (0, 0)], dxfattribs={'layer': '墙体'})

    # 平法标注(施工说明)
    _text(msp, 500, 16500, f'{beam_section_text}', layer='说明')
    _text(msp, 500, 16000, f'KZ1 {column_size}×{column_size} 12Φ22 Φ8@100', layer='说明')
    _text(msp, 500, 15500, '板厚120 底筋Φ10@200双层双向', layer='说明')
    _text(msp, 500, 15000, '层高3.0m', layer='说明')
    _text(msp, 500, 14500, '墙厚200mm 加气混凝土砌块', layer='说明')
    if seismic_text:
        _text(msp, 500, 14000, seismic_text, layer='说明')

    return doc


def gen_all():
    os.makedirs(f'{BASE}/房建构件/drawings', exist_ok=True)
    os.makedirs(f'{BASE}/房建配筋不足/drawings', exist_ok=True)

    # 合规版: KL1(3) 300×600, 梁长真实 24000
    doc1 = _make_case('房建构件', 600, 500, '',
                      'KL1(3) 300×600 Φ8@100/200(2) 2Φ25;4Φ22 G4Φ12')
    doc1.saveas(f'{BASE}/房建构件/drawings/房建构件.dxf')

    # 违规版: 抗震 + 梁高 480(5m 跨比 1/10.4 低于抗震下限) + 柱 300×300
    doc2 = _make_case('房建配筋不足', 480, 300, '抗震设防烈度7度',
                      'KL1(1) 300×480 Φ8@100/200(2) 2Φ18;2Φ18')
    doc2.saveas(f'{BASE}/房建配筋不足/drawings/房建配筋不足.dxf')

    print(f'  ✓ 房建构件.dxf / 房建配筋不足.dxf 已生成 → {BASE}')


if __name__ == '__main__':
    gen_all()
