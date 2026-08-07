# -*- coding: utf-8 -*-
"""视觉化 V-0 渲染层 — DXF → PNG(整图 + 分层图)

定位(见 规划_视觉化路径.md V-0):
- 纯本地、零新依赖(matplotlib 已是核心依赖)
- 按图层分色渲染 LINE/ARC/CIRCLE/LWPOLYLINE/TEXT/INSERT
- 图框/0 层淡化(复用 units.EXCLUDE_LAYER_KW 语义)
- 输出整图 + 每层 PNG + 渲染元数据 json, 供视觉识别层(V-2)消费

用法:
  python3 render_dxf.py 图纸.dxf [--out-dir renders] [--dpi 150] [--per-layer]
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')

# 非交互后端(无显示环境也可用)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle

# 中文字体回退(Windows 常用; Linux 环境缺则回退默认, 仅图内文字受影响)
try:
    from matplotlib import font_manager
    _cjk = [f for f in ('Microsoft YaHei', 'SimHei', 'SimSun', 'Noto Sans CJK SC')
            if f in {ft.name for ft in font_manager.fontManager.ttflist}]
    if _cjk:
        plt.rcParams['font.sans-serif'] = _cjk + plt.rcParams.get('font.sans-serif', [])
        plt.rcParams['axes.unicode_minus'] = False
except Exception:
    pass

# 图层调色板(循环取色, 相邻层区分度大)
LAYER_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
                '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
                '#393b79', '#637939', '#8c6d31', '#843c39', '#7b4173']

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)


def _layer_color(layer, idx):
    return LAYER_COLORS[idx % len(LAYER_COLORS)]


def _is_frame_layer(layer):
    """图框/辅助层: 0 层 + EXCLUDE_LAYER_KW 命中(与面积裁决同口径)"""
    from units import is_excluded_layer
    if (layer or '') == '0':
        return True
    return is_excluded_layer(layer)


def _collect_entities(msp):
    """按图层收集可渲染实体。返回 {layer: [entities]} + 全局 bbox。"""
    from units import collect_closed_polys  # 闭合多段线坐标
    layers = {}
    xs_all, ys_all = [], []
    for e in msp:
        lay = e.dxf.layer or ''
        t = e.dxftype()
        try:
            if t == 'LWPOLYLINE':
                pts = list(e.get_points('xy'))
                if len(pts) >= 2:
                    layers.setdefault(lay, []).append(('poly', pts, bool(e.closed)))
                    for p in pts:
                        xs_all.append(p[0]); ys_all.append(p[1])
            elif t == 'LINE':
                s, en = e.dxf.start, e.dxf.end
                layers.setdefault(lay, []).append(('line', (s.x, s.y), (en.x, en.y)))
                xs_all += [s.x, en.x]; ys_all += [s.y, en.y]
            elif t == 'ARC':
                c = e.dxf.center
                r = e.dxf.radius
                layers.setdefault(lay, []).append(('arc', (c.x, c.y), r, e.dxf.start_angle, e.dxf.end_angle))
                xs_all += [c.x - r, c.x + r]; ys_all += [c.y - r, c.y + r]
            elif t == 'CIRCLE':
                c = e.dxf.center
                r = e.dxf.radius
                layers.setdefault(lay, []).append(('circle', (c.x, c.y), r))
                xs_all += [c.x - r, c.x + r]; ys_all += [c.y - r, c.y + r]
            elif t == 'TEXT':
                layers.setdefault(lay, []).append(('text', (e.dxf.insert.x, e.dxf.insert.y), e.dxf.text))
            elif t == 'INSERT':
                layers.setdefault(lay, []).append(('insert', (e.dxf.insert.x, e.dxf.insert.y), e.dxf.name))
        except Exception:
            continue
    bbox = None
    if xs_all and ys_all:
        # v6.4: bbox 用 0.5%~99.5% 分位裁剪 — 排除镜像/孤立的离群垃圾实体
        # (如误复制的 WINDOW 块跑到 -200万坐标, 把渲染画布撑大导致视觉读图失效)
        xs_s, ys_s = sorted(xs_all), sorted(ys_all)
        n = len(xs_s)
        lo, hi = max(int(n * 0.005), 1), max(int(n * 0.995), 1)
        bbox = (xs_s[lo], ys_s[lo], xs_s[hi - 1], ys_s[hi - 1])
    return layers, bbox


def _draw_entities(ax, entities, color, frame_layer=False):
    """绘制一组实体。frame_layer=True 时淡化为灰色细线。"""
    alpha = 0.25 if frame_layer else 0.9
    lw = 0.6 if frame_layer else 1.0
    for ent in entities:
        try:
            kind = ent[0]
            if kind == 'poly':
                pts = ent[1]
                closed = ent[2]
                ax.plot([p[0] for p in pts], [p[1] for p in pts],
                        color=color, lw=lw, alpha=alpha)
                if closed and len(pts) >= 3:
                    ax.add_patch(Polygon(pts, closed=True, fill=False,
                                         edgecolor=color, lw=lw, alpha=alpha))
            elif kind == 'line':
                (x1, y1), (x2, y2) = ent[1], ent[2]
                ax.plot([x1, x2], [y1, y2], color=color, lw=lw, alpha=alpha)
            elif kind == 'arc':
                (cx, cy), r, a1, a2 = ent[1], ent[2], ent[3], ent[4]
                ax.add_patch(matplotlib.patches.Arc((cx, cy), 2 * r, 2 * r,
                                                    theta1=a1, theta2=a2,
                                                    color=color, lw=lw, alpha=alpha))
            elif kind == 'circle':
                (cx, cy), r = ent[1], ent[2]
                ax.add_patch(Circle((cx, cy), r, fill=False, color=color, lw=lw, alpha=alpha))
            elif kind == 'text':
                (tx, ty), txt = ent[1], ent[2]
                ax.text(tx, ty, str(txt)[:12], fontsize=3, color=color, alpha=min(alpha, 0.7))
            elif kind == 'insert':
                (ix, iy), name = ent[1], ent[2]
                ax.plot(ix, iy, marker='+', markersize=3, color=color, alpha=alpha)
        except Exception:
            continue


def _finalize_fig(fig, ax, bbox, title, path):
    if bbox:
        x0, y0, x1, y1 = bbox
        pad = max((x1 - x0), (y1 - y0)) * 0.02 or 1.0
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=8)
    ax.axis('off')
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def render_dxf(dxf_path, out_dir=None, per_layer=True):
    """渲染 DXF → PNG。返回渲染元数据 dict。"""
    import ezdxf
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(dxf_path), 'renders')
    os.makedirs(out_dir, exist_ok=True)

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    layers, bbox = _collect_entities(msp)
    insunits = doc.header.get('$INSUNITS', 4)

    base = os.path.splitext(os.path.basename(dxf_path))[0]
    meta = {
        'source': dxf_path, 'base': base, 'insunits': insunits,
        'bbox': bbox, 'layers': [], 'files': {}, 'layer_count': len(layers),
    }

    # 整图: 全部图层, 图框层淡化
    fig, ax = plt.subplots(figsize=(14, 10))
    for idx, (lay, ents) in enumerate(sorted(layers.items())):
        frame = _is_frame_layer(lay)
        _draw_entities(ax, ents, _layer_color(lay, idx), frame_layer=frame)
        meta['layers'].append({'name': lay, 'count': len(ents),
                               'frame_layer': frame, 'color': _layer_color(lay, idx)})
    full_path = os.path.join(out_dir, f'{base}_plan.png')
    _finalize_fig(fig, ax, bbox, f'{base} (全图层)', full_path)
    meta['files']['full'] = full_path

    # 分层图: 每层一张(仅实体层)
    if per_layer:
        layer_dir = os.path.join(out_dir, 'layers')
        os.makedirs(layer_dir, exist_ok=True)
        for idx, (lay, ents) in enumerate(sorted(layers.items())):
            if _is_frame_layer(lay):
                continue  # 图框/0层不单独出图(噪声)
            fig, ax = plt.subplots(figsize=(12, 9))
            _draw_entities(ax, ents, _layer_color(lay, idx))
            p = os.path.join(layer_dir, f'{base}_{lay.replace("/", "_")}.png')
            _finalize_fig(fig, ax, bbox, f'{base} / {lay}', p)
            meta['files'][lay] = p

    meta_path = os.path.join(out_dir, f'{base}_render_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='DXF 渲染层(V-0)')
    ap.add_argument('dxf', help='DXF 文件路径')
    ap.add_argument('--out-dir', default=None, help='输出目录(默认 图纸旁/renders)')
    ap.add_argument('--no-per-layer', action='store_true', help='不生成分层图')
    args = ap.parse_args(argv)
    meta = render_dxf(args.dxf, args.out_dir, per_layer=not args.no_per_layer)
    print(f'渲染完成: {meta["base"]}_plan.png')
    print(f'  图层数: {meta["layer_count"]} | 整图: {meta["files"]["full"]}')
    print(f'  元数据: {os.path.join(args.out_dir or os.path.dirname(args.dxf) + "/renders", meta["base"] + "_render_meta.json")}')
    return meta


if __name__ == '__main__':
    main()
