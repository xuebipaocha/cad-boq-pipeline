# -*- coding: utf-8 -*-
"""s4 钢结构三维重建：从 DXF **线段几何**直接建 3D（替换"12 个示意方块"）。

为什么另写：`export_3d_html.py` 依赖 识图结果.json 的构件模型，而钢结构识别只产出
12 条无坐标记录；钢构件的**真实几何在图内线段上**（LINE 12954 / LWPOLYLINE 1090，
最长 51m）。本脚本按图层语义取线段 → 每段按代表性截面生成细长立方体 →
复用 `export_3d_html.HTML_TMPL` 渲染为可旋转 HTML。

用途：交给视觉模型复验（"是否从 12 方块变成真实钢构架"），反推钢结构算量是否可信。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ezdxf  # noqa: E402
from export_3d_html import HTML_TMPL  # noqa: E402

# 图层 → (类别, 截面宽m, 截面高m, 颜色)
SEC = {
    'SE_STEL_COLU_CON': ('钢柱', 0.20, 0.40, '#d9534f'),
    'SE_STEL_PRUN': ('檩条', 0.075, 0.25, '#337ab7'),
    'S_STAY ROD': ('拉杆', 0.016, 0.016, '#e0a800'),
    '零部件': ('零件', 0.05, 0.10, '#20c997'),
    'S_METAL': ('金属件', 0.05, 0.10, '#20c997'),
    'objs': ('构件', 0.10, 0.20, '#8c8c8c'),
    '1': ('构件', 0.10, 0.20, '#8c8c8c'),
    '0': ('主构件', 0.20, 0.40, '#795548'),
}
MAXSEG = 2000
MINLEN = 1.0          # 米，短于此的线段忽略（标注/装饰线）


def _segments(msp):
    out = []
    for e in msp:
        lay = str(e.dxf.layer or '')
        if lay not in SEC:
            continue
        kind = e.dxftype()
        try:
            if kind == 'LINE':
                p1, p2 = e.dxf.start, e.dxf.end
                out.append((lay, p1.x, p1.y, p2.x, p2.y))
            elif kind == 'LWPOLYLINE':
                pts = [(q[0], q[1]) for q in e.get_points('xy')]
                for i in range(len(pts) - 1):
                    out.append((lay, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
        except Exception:
            continue
    return out


def build(dxf_path, title=''):
    doc = ezdxf.readfile(dxf_path)
    segs = _segments(doc.modelspace())
    boxes, stat = [], {}
    for lay, x1, y1, x2, y2 in segs:
        name, w, h, color = SEC[lay]
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        L = (dx * dx + dy * dy) ** 0.5 / 1000.0
        if L < MINLEN:
            continue
        # 线段 → 细长立方体（沿主轴给长度，另一轴给截面宽）
        if dx >= dy:
            bdx, bdy = max(dx / 1000.0, 0.05), w
        else:
            bdx, bdy = w, max(dy / 1000.0, 0.05)
        boxes.append({'x': (x1 + x2) / 2000.0, 'y': (y1 + y2) / 2000.0, 'z': 0.0,
                      'dx': bdx, 'dy': bdy, 'dz': h,
                      'color': color, 'label': name})
        stat[name] = stat.get(name, 0) + 1
        if len(boxes) >= MAXSEG:
            break

    # 归一化到原点（避免绝对坐标把模型压扁，与 export_3d_html 同一教训）
    if boxes:
        ox = min(b['x'] for b in boxes)
        oy = min(b['y'] for b in boxes)
        for b in boxes:
            b['x'] -= ox
            b['y'] -= oy

    name = title or os.path.splitext(os.path.basename(dxf_path))[0]
    sp = sum(v for v in stat.values())
    caveat = ('⚠ 按图层语义 + 线段几何重建：线段→细长立方体（截面为代表性取值，非逐构件实测剖面）。'
              '共绘 %d 段（源 %d 段，上限 %d）。' % (sp, len(segs), MAXSEG))
    meta = {'名称': name, '专业': '钢结构工程', '说明': caveat}
    html = (HTML_TMPL.replace('__TITLE__', name)
            .replace('__BOXES__', json.dumps(boxes, ensure_ascii=False))
            .replace('__META__', json.dumps(meta, ensure_ascii=False)))
    return html, {'盒体数': len(boxes), '按类别': stat, '源线段数': len(segs),
                  '说明': caveat}


def main(dxf_path, out_html, title=''):
    html, info = build(dxf_path, title)
    os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(html)
    info['输出'] = out_html
    print(json.dumps(info, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    t = sys.argv[sys.argv.index('--title') + 1] if '--title' in sys.argv else ''
    main(a[0], a[1], t)
