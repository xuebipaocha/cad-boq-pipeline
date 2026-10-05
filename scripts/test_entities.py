# -*- coding: utf-8 -*-
"""扩展实体解析专项用例 — v6.10（p3e）

覆盖六类此前零处理的 DXF 实体: MLINE / SPLINE / HATCH / LEADER / ACAD_TABLE / PROXY。
用合成图(benchmarks/cases/实体扩展)做可重复断言, 并做与既有几何的互证:
  HATCH 面积 ↔ 同区域闭合多段线面积;  真表格 ↔ 文字聚类解析。
PROXY 因 ezdxf 无法生成真实 ACAD_PROXY_ENTITY, 用 mock 实体验证检测逻辑(如实标注)。

用法:
  python3 test_entities.py          # 全量
  python3 test_entities.py --gen    # 先重新生成合成图再测
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name} {detail}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--gen', action='store_true', help='先重新生成合成图')
    args = ap.parse_args(argv)

    SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DXF = os.path.join(SKILL, 'benchmarks', 'cases', '实体扩展', 'drawings', '实体扩展.dxf')
    if args.gen or not os.path.exists(DXF):
        from tools.gen_case_entities import build
        build(DXF)
        print(f'已生成合成图: {DXF}')

    import ezdxf
    from dxf_entities import (extract_all, extract_proxy, cross_check_hatch_polys)
    from table_parser import parse_tables

    doc = ezdxf.readfile(DXF)
    msp = doc.modelspace()
    d = extract_all(msp)

    print('=== 1. MLINE 多线墙 ===')
    ml = d['mline']
    check('识别 3 道多线墙', len(ml) == 3, f'实际 {len(ml)}')
    check('墙厚 240mm (多线样式偏移 ±120)',
          all(abs(m['width'] - 240.0) < 1.0 for m in ml),
          str([m['width'] for m in ml]))
    check('墙体长度 24000/16000/24000',
          sorted(round(m['length']) for m in ml) == [16000, 24000, 24000],
          str(sorted(round(m['length']) for m in ml)))
    check('中心线顶点齐全(每墙 2 点)', all(len(m['center']) == 2 for m in ml))
    check('厚度来源标注为多线样式', all(m.get('width_src') == '多线样式' for m in ml))

    print('=== 2. SPLINE 样条曲线 ===')
    sp = d['spline']
    check('识别 2 条样条', len(sp) == 2, f'实际 {len(sp)}')
    opl = [s for s in sp if not s['closed']]
    cl = [s for s in sp if s['closed']]
    check('开曲线长度≈14114 (容差 3%)',
          bool(opl) and abs(opl[0]['length'] - 14114) / 14114 < 0.03,
          str(opl[0]['length'] if opl else None))
    check('闭曲线面积≈43.04e6 (容差 3%)',
          bool(cl) and abs(cl[0]['area'] - 43040746) / 43040746 < 0.03,
          str(cl[0]['area'] if cl else None))
    check('开曲线面积记 0(不编造)', bool(opl) and opl[0]['area'] == 0.0)

    print('=== 3. HATCH 填充边界面积 + 互证 ===')
    ha = d['hatch']
    check('识别 2 个填充', len(ha) == 2, f'实际 {len(ha)}')
    areas = sorted(round(h['area']) for h in ha)
    check('面积 = 外环 48,000,000 与 净面积 44,000,000(外环−孤岛)',
          areas == [44000000, 48000000], str(areas))
    tc = d['hatch_check']
    ok_c = [c for c in tc if c.get('互证') == '一致']
    check('填充↔闭合多段线互证: 1 个一致(偏差 0)', len(ok_c) == 1, str(tc))
    check('无对照填充如实标"无同区域闭合多段线"',
          any('无同区域' in str(c.get('互证', '')) for c in tc), str(tc))

    print('=== 4. LEADER 引线标注关联 ===')
    ld = d['leader']
    check('识别 2 条引线', len(ld) == 2, f'实际 {len(ld)}')
    texts = [l.get('nearby_text', '') for l in ld]
    check('引线末端关联到文字(2/2)', all(texts),
          str(texts))
    check('关联文字含做法/窗台信息',
          any('做法' in t for t in texts) and any('窗台' in t for t in texts), str(texts))
    check('引线末端坐标存在', all(l.get('tip') for l in ld))

    print('=== 5. ACAD_TABLE 真表格(匿名表块) + 接入消费 ===')
    tb = d['table']
    check('识别 1 个真表格', len(tb) == 1, f'实际 {len(tb)}')
    if tb:
        t0 = tb[0]
        check('表名结构 4 行 3 列', t0['rows'] == 4 and t0['cols'] == 3,
              f"{t0['rows']}x{t0['cols']}")
        flat = [c for row in t0['cells'] for c in row]
        check('单元格文字含门窗编号 LC1618/M0921/LC1515',
              all(k in flat for k in ('LC1618', 'M0921', 'LC1515')), str(flat[:8]))
        check('来源标注为匿名表块', t0.get('kind') == '匿名表块')
    tables = parse_tables(msp)
    real = [t for t in tables if t.get('source')]
    check('table_parser 消费到真表格(type=门窗表)', 
          any(t['type'] == '门窗表' and t.get('source') for t in tables),
          str([(t['type'], t.get('source')) for t in tables]))
    check('真表格 rows 结构对齐({y,cells,grid})',
          all(isinstance(r, dict) and 'cells' in r for t in real for r in t['rows']))

    print('=== 6. ACAD_PROXY_ENTITY 天正对象检测 ===')

    class _F:
        def __init__(self, layer, cls='TCH_WALL'):
            self.dxf = type('D', (), {'layer': layer, 'class_name': cls})()

        def dxftype(self):
            return 'ACAD_PROXY_ENTITY'

    px = extract_proxy([_F('WALL'), _F('WALL'), _F('DOOR'), _F('WIN', 'TCH_WINDOW')])
    check('mock 天正对象计数=4', px['count'] == 4, str(px))
    check('图层分布正确', px['layers'] == {'WALL': 2, 'DOOR': 1, 'WIN': 1}, str(px['layers']))
    check('类名分布正确', px['classes'] == {'TCH_WALL': 3, 'TCH_WINDOW': 1}, str(px['classes']))
    check('合成图无 proxy(如实为 0)', (d['proxy'] or {}).get('count') == 0)

    print(f'\n结果: {PASS}通过, {FAIL}失败, 共{PASS + FAIL}项')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
