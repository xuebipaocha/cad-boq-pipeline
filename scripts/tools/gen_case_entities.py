# -*- coding: utf-8 -*-
"""扩展实体合成测试图生成器 — v6.10

生成含以下实体的 DXF, 供 dxf_entities 解析能力测试与回归:
  MLINE(多线墙) / SPLINE(样条) / HATCH(填充) / LEADER(引线标注) / TABLE(真表格)
  + 常规 LINE/LWPOLYLINE/TEXT(对照既有解析)
  + 高密度小符号区(门窗号/索引号/规格标注密集排布, 供视觉分块识别验证 — p5c)

用法:
  python3 gen_case_entities.py [输出目录]
默认输出: benchmarks/cases/实体扩展/drawings/实体扩展.dxf
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')


def build(out_path, density_cols=12, density_rows=8):
    import ezdxf
    from ezdxf import const

    doc = ezdxf.new('R2013', setup=True)
    msp = doc.modelspace()
    made = {}

    def _try(key, fn):
        try:
            fn()
            made[key] = True
        except Exception as e:
            made[key] = f'失败: {str(e)[:80]}'

    # ── MLINE 多线墙: 3 道墙(水平/垂直/斜), 用标准多线样式 ──
    def _mline():
        # 定义双线多线样式(墙厚 240mm: 偏移 ±120) → 解析层可提取墙厚, 而非只有单线
        style_name = 'Standard'
        try:
            st = doc.mline_styles.new('墙240')
            st.elements.append(120.0)
            st.elements.append(-120.0)
            style_name = '墙240'
        except Exception:
            pass
        pts1 = [(0, 0), (12000, 0)]
        pts2 = [(12000, 0), (12000, 8000)]
        pts3 = [(0, 8000), (12000, 8000)]
        for pts in (pts1, pts2, pts3):
            msp.add_mline([(x, y, 0) for x, y in pts],
                          dxfattribs={'layer': '墙-多线', 'style_name': style_name,
                                      'scale_factor': 1.0})
    _try('mline', _mline)

    # ── SPLINE: 开曲线(道路中心线) + 闭曲线(水池轮廓) ──
    def _spline():
        open_pts = [(0, -3000), (3000, -2000), (7000, -2500), (11000, -1500), (14000, -2000)]
        msp.add_open_spline(open_pts, degree=3, dxfattribs={'layer': '道路中心线'})
        # ezdxf 1.4 无 add_closed_spline → 用 add_spline + flags 置闭合位
        closed_pts = [(20000, 0), (26000, 0), (26000, 5000), (23000, 7000), (20000, 5000)]
        s = msp.add_spline(closed_pts, degree=3, dxfattribs={'layer': '水池轮廓'})
        s.dxf.flags = int(s.dxf.flags) | 1
    _try('spline', _spline)

    # ── HATCH: 填充区域(闭合多段线边界) + 带孤岛 ──
    def _hatch():
        poly = [(0, -8000), (8000, -8000), (8000, -14000), (0, -14000)]
        msp.add_lwpolyline(poly, close=True, dxfattribs={'layer': '地面填充'})
        h = msp.add_hatch(color=3, dxfattribs={'layer': '地面填充'})
        h.paths.add_polyline_path(poly, is_closed=True, flags=const.BOUNDARY_PATH_EXTERNAL)
        h.set_pattern_fill('ANSI31', scale=100)

        outer = [(10000, -8000), (18000, -8000), (18000, -14000), (10000, -14000)]
        inner = [(13000, -10000), (15000, -10000), (15000, -12000), (13000, -12000)]
        h2 = msp.add_hatch(color=4, dxfattribs={'layer': '防水填充'})
        h2.paths.add_polyline_path(outer, is_closed=True, flags=const.BOUNDARY_PATH_EXTERNAL)
        h2.paths.add_polyline_path(inner, is_closed=True,
                                   flags=const.BOUNDARY_PATH_EXTERNAL | const.BOUNDARY_PATH_OUTERMOST)
        h2.set_solid_fill(color=6)
    _try('hatch', _hatch)

    # ── LEADER 引线标注 + 附近文字 ──
    def _leader():
        msp.add_text('做法参2J915 地面防水', height=200,
                     dxfattribs={'layer': '标注文字'}).set_placement((3000, -6000))
        msp.add_leader([(3000, -6200, 0), (2500, -7000, 0), (1500, -7800, 0)],
                       dxfattribs={'layer': '引线标注'})
        msp.add_text('窗台高900', height=200,
                     dxfattribs={'layer': '标注文字'}).set_placement((20000, -3000))
        msp.add_leader([(20000, -3200, 0), (21000, -4000, 0), (21500, -5000, 0)],
                       dxfattribs={'layer': '引线标注'})
    _try('leader', _leader)

    # ── TABLE 真表格(门窗表形态) ──
    def _table():
        # ezdxf 1.4.4 不支持创建 ACAD_TABLE 实体(entities.Table 不存在, 实测) →
        # 生成"匿名表块"(*T 前缀块 + 块内 TEXT 网格 + INSERT), 与真实图纸中
        # ACAD_TABLE 导出后的 common 结构一致(DXF 里表即匿名块+文字), 供解析层识别。
        try:
            blk = doc.blocks.new(name='*T1')
        except Exception:
            blk = doc.blocks.get('*T1')
        rows = [['编号', '洞口尺寸', '数量'],
                ['LC1618', '1600x1800', '12'],
                ['M0921', '900x2100', '8'],
                ['LC1515', '1500x1500', '6']]
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                blk.add_text(val, height=150, dxfattribs={'layer': '门窗表'}).set_placement(
                    (24000 + c * 2500, -8000 - r * 400))
        msp.add_blockref('*T1', (0, 0), dxfattribs={'layer': '门窗表'})
    _try('table', _table)

    # ── 高密度小符号区: 门窗号/索引号/规格标注密集排布(p5c 视觉验证用) ──
    def _density():
        msp.add_text('高密度标注区 (视觉分块验证)', height=300,
                     dxfattribs={'layer': '标注文字'}).set_placement((-20000, 2000))
        for r in range(density_rows):
            for c in range(density_cols):
                x = -20000 + c * 900
                y = -r * 700
                idx = r * density_cols + c + 1
                msp.add_text(f'LC{15 + idx:02d}{18 + (idx % 5):02d}', height=120,
                             dxfattribs={'layer': '门窗号'}).set_placement((x, y - 300))
                msp.add_text(f'{idx:02d}', height=100,
                             dxfattribs={'layer': '索引号'}).set_placement((x, y - 550))
                msp.add_circle((x + 200, y), radius=80, dxfattribs={'layer': '门窗符号'})
    _try('density', _density)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.saveas(out_path)
    return made


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'benchmarks', 'cases', '实体扩展', 'drawings', '实体扩展.dxf')
    made = build(out)
    print(f'生成: {out} ({os.path.getsize(out)} bytes)')
    for k, v in made.items():
        print(f'  {k}: {"✓" if v is True else v}')


if __name__ == '__main__':
    main()
