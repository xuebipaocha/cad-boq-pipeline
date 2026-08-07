# -*- coding: utf-8 -*-
"""诊断: 按 y 带切分视图, 统计各带实体/文字/标注"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import ezdxf
import numpy as np
from collections import Counter

DXF = r'D:/Reasonix Agent/项目1/.reasonix/attachments/dwg_conv/clipboard-20260806-194543.919088-000020.dxf'
X0, X1 = 2000000.0, 2310000.0

doc = ezdxf.readfile(DXF)
msp = doc.modelspace()

def sample_pts(e):
    t = e.dxftype()
    try:
        if t == 'LINE':
            return [e.dxf.start, e.dxf.end]
        if t == 'LWPOLYLINE':
            return list(e.get_points('xy'))
        if t in ('TEXT', 'MTEXT'):
            return [e.dxf.insert]
        if t == 'DIMENSION':
            return [e.dxf.defpoint]
        if t == 'INSERT':
            return [e.dxf.insert]
        if t == 'CIRCLE':
            return [e.dxf.center]
        if t == 'ARC':
            return [e.dxf.center]
        if t == 'HATCH':
            return []
    except Exception:
        return []
    return []

ys = []
for e in msp:
    for p in sample_pts(e):
        if X0 <= p[0] <= X1:
            ys.append(p[1])
ys = np.array(ys)
ys.sort()
gaps = np.where(np.diff(ys) > 20000)[0]
bounds = [ys[0]]
for g in gaps:
    bounds.append((ys[g] + ys[g+1]) / 2)
bounds.append(ys[-1])
print('y 视图带边界:', [round(b) for b in bounds])

for i in range(len(bounds) - 1):
    y0, y1 = bounds[i], bounds[i+1]
    c = Counter()
    texts, dims, layers = [], [], Counter()
    for e in msp:
        pts = sample_pts(e)
        if not pts:
            continue
        if any(X0 <= p[0] <= X1 and y0 <= p[1] <= y1 for p in pts):
            c[e.dxftype()] += 1
            lay = e.dxf.layer if e.dxf.hasattr('layer') else '?'
            layers[lay] += 1
            if e.dxftype() == 'TEXT':
                texts.append(str(e.dxf.text)[:50])
            if e.dxftype() == 'DIMENSION':
                dims.append(str(e.dxf.text)[:25] if e.dxf.hasattr('text') else '')
    print(f'--- 带{i+1} y:[{round(y0)},{round(y1)}] 高{round(y1-y0)} ---')
    print('  实体:', dict(c.most_common(8)))
    print('  图层top:', dict(layers.most_common(8)))
    print('  文字样例:', texts[:14])
    print('  标注样例:', [d for d in dims if d][:12])
