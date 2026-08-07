# -*- coding: utf-8 -*-
"""诊断: 渲染指定 y 带为 PNG, 供视觉模型读图"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import ezdxf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle, Arc

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

BANDS = {
    'elevation': (2000000.0, 2310000.0, -20000.0, 100000.0),   # 立面图
    'plan':      (2000000.0, 2310000.0, 96000.0, 1250000.0),   # 平面图
}
colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2',
          '#7f7f7f', '#bcbd22', '#17becf', '#393b79', '#637939', '#8c6d31', '#843c39']
os.makedirs(OUT, exist_ok=True)

def in_reg(p, X0, X1, Y0, Y1):
    return X0 <= p[0] <= X1 and Y0 <= p[1] <= Y1

def draw(ax, e, color, X0, X1, Y0, Y1):
    t = e.dxftype()
    try:
        if t == 'LINE':
            s, e2 = e.dxf.start, e.dxf.end
            if in_reg(s, X0, X1, Y0, Y1) and in_reg(e2, X0, X1, Y0, Y1):
                ax.plot([s[0], e2[0]], [s[1], e2[1]], color=color, lw=0.5)
        elif t == 'LWPOLYLINE':
            pts = list(e.get_points('xy'))
            if pts and all(in_reg(p, X0, X1, Y0, Y1) for p in pts):
                ax.add_patch(Polygon(pts, closed=True, fill=False, edgecolor=color, lw=0.5))
        elif t == 'CIRCLE':
            c = e.dxf.center
            if in_reg(c, X0, X1, Y0, Y1):
                ax.add_patch(Circle((c[0], c[1]), e.dxf.radius, fill=False, edgecolor=color, lw=0.5))
        elif t == 'ARC':
            c = e.dxf.center
            if in_reg(c, X0, X1, Y0, Y1):
                ax.add_patch(Arc((c[0], c[1]), e.dxf.radius*2, e.dxf.radius*2, angle=0,
                                 theta1=e.dxf.start_angle, theta2=e.dxf.end_angle, edgecolor=color, lw=0.5))
        elif t in ('TEXT', 'MTEXT'):
            p = e.dxf.insert
            if in_reg(p, X0, X1, Y0, Y1):
                txt = e.dxf.text if t == 'TEXT' else e.text()
                ax.text(p[0], p[1], str(txt)[:40], fontsize=5, color=color)
        elif t == 'DIMENSION':
            defp = e.dxf.defpoint
            if in_reg(defp, X0, X1, Y0, Y1):
                ax.plot(defp[0], defp[1], 'x', color=color, ms=4)
                txt = e.dxf.text if e.dxf.hasattr('text') else ''
                if txt:
                    ax.text(defp[0], defp[1] + 2000, str(txt)[:20], fontsize=5, color=color)
        elif t == 'INSERT':
            p = e.dxf.insert
            if in_reg(p, X0, X1, Y0, Y1):
                ax.plot(p[0], p[1], 's', color=color, ms=2)
    except Exception:
        pass

for name, (X0, X1, Y0, Y1) in BANDS.items():
    fig, ax = plt.subplots(figsize=(24, 12) if name == 'elevation' else (20, 22))
    for idx, e in enumerate(msp):
        lay = e.dxf.layer if e.dxf.hasattr('layer') else '0'
        draw(ax, e, colors[hash(lay) % len(colors)], X0, X1, Y0, Y1)
    ax.set_xlim(X0, X1); ax.set_ylim(Y0, Y1)
    ax.set_aspect('equal'); ax.axis('off')
    p = os.path.join(OUT, f'band_{name}.png')
    plt.savefig(p, dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print('saved:', p, os.path.getsize(p))
