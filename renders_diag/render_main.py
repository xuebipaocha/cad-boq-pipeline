# -*- coding: utf-8 -*-
"""诊断: 裁剪渲染主体区域, 供视觉模型读图测试"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import ezdxf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle

DXF = r'D:/Reasonix Agent/项目1/.reasonix/attachments/dwg_conv/clipboard-20260806-194543.919088-000020.dxf'
OUT = r'D:/Reasonix Agent/项目1/cad-boq-pipeline/renders_diag'

try:
    from matplotlib import font_manager
    _cjk = [f for f in ('Microsoft YaHei', 'SimHei') if f in {ft.name for ft in font_manager.fontManager.ttflist}]
    if _cjk:
        plt.rcParams['font.sans-serif'] = _cjk + plt.rcParams.get('font.sans-serif', [])
        plt.rcParams['axes.unicode_minus'] = False
except Exception:
    pass

doc = ezdxf.readfile(DXF)
msp = doc.modelspace()

# 主体区域 (排除负区镜像垃圾)
X0, X1, Y0, Y1 = 2000000.0, 2310000.0, -20000.0, 300000.0

def in_region(p):
    return X0 <= p[0] <= X1 and Y0 <= p[1] <= Y1

layers = {}
for e in msp:
    lay = e.dxf.layer if e.dxf.hasattr('layer') else '0'
    layers.setdefault(lay, []).append(e)

colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2',
          '#7f7f7f', '#bcbd22', '#17becf', '#393b79', '#637939', '#8c6d31', '#843c39']
os.makedirs(OUT, exist_ok=True)

def draw(ax, ents, color):
    for e in ents:
        try:
            t = e.dxftype()
            if t == 'LINE':
                if in_region(e.dxf.start) and in_region(e.dxf.end):
                    ax.plot([e.dxf.start[0], e.dxf.end[0]], [e.dxf.start[1], e.dxf.end[1]], color=color, lw=0.6)
            elif t == 'LWPOLYLINE':
                pts = list(e.get_points('xy'))
                if pts and all(in_region(p) for p in pts):
                    ax.add_patch(Polygon(pts, closed=True, fill=False, edgecolor=color, lw=0.6))
            elif t == 'CIRCLE':
                c = e.dxf.center
                if in_region(c):
                    ax.add_patch(Circle((c[0], c[1]), e.dxf.radius, fill=False, edgecolor=color, lw=0.6))
            elif t == 'ARC':
                c = e.dxf.center
                if in_region(c):
                    ax.add_patch(matplotlib.patches.Arc((c[0], c[1]), e.dxf.radius*2, e.dxf.radius*2,
                                 angle=0, theta1=e.dxf.start_angle, theta2=e.dxf.end_angle,
                                 edgecolor=color, lw=0.6))
            elif t in ('TEXT', 'MTEXT'):
                p = e.dxf.insert if t == 'TEXT' else e.dxf.insert
                if in_region(p):
                    txt = e.dxf.text if t == 'TEXT' else e.text()
                    ax.text(p[0], p[1], str(txt)[:30], fontsize=4, color=color)
            elif t == 'DIMENSION':
                defp = e.dxf.defpoint
                if in_region(defp):
                    try:
                        ax.plot([defp[0], defp[0]], [defp[1], defp[1]], 'x', color=color, ms=3)
                    except Exception: pass
            elif t == 'INSERT':
                p = e.dxf.insert
                if in_region(p):
                    ax.plot(p[0], p[1], 's', color=color, ms=1.5)
        except Exception:
            pass

# 整图(主体区域)
fig, ax = plt.subplots(figsize=(20, 14))
for idx, (lay, ents) in enumerate(sorted(layers.items())):
    draw(ax, ents, colors[idx % len(colors)])
ax.set_xlim(X0, X1); ax.set_ylim(Y0, Y1)
ax.set_aspect('equal'); ax.axis('off')
full = os.path.join(OUT, 'main_region.png')
plt.savefig(full, dpi=150, bbox_inches='tight', facecolor='white')
plt.close(fig)
print('saved:', full, os.path.getsize(full))

# 按 y 分块: 检查视图布局 (y: 0~30万, 可能多个视图纵向堆叠)
import numpy as np
ys = []
for e in msp:
    try:
        pts = e.get_points() if hasattr(e, 'get_points') else None
        if pts:
            for p in pts:
                if X0 <= p[0] <= X1:
                    ys.append(p[1])
    except Exception: pass
ys = np.array(ys)
print('主体 y 分布: min=%.0f max=%.0f' % (ys.min(), ys.max()))
# 粗聚类视图带
ys.sort()
gaps = np.diff(ys)
big = np.where(gaps > 20000)[0]
print('y 大间隙数量:', len(big))
