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


def _finalize_fig(fig, ax, bbox, title, path, dpi=150):
    if bbox:
        x0, y0, x1, y1 = bbox
        pad = max((x1 - x0), (y1 - y0)) * 0.02 or 1.0
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=8)
    ax.axis('off')
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, bbox_inches='tight', facecolor='white')
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


def _entity_anchor(ent):
    """实体代表点(切块时快速过滤用)。"""
    k = ent[0]
    try:
        if k == 'line':
            return ent[1]
        if k in ('poly', 'arc', 'circle', 'text', 'insert'):
            p = ent[1]
            return p[0] if (k == 'poly' and p) else p
    except Exception:
        return None
    return None


def _tiles_from_density(points, cols, rows, overlap=0.08):
    """按实体分布分位切块(v6.10 实测驱动): 机械等面积切块在真实图上会让多块落在空白区
    (船体大楼 3x3 实测: r2c3 有 7223 实体而 5/9 块为 0)。改按实体坐标等频分位定切分线,
    使每块包含大致相同的实体数 → 每块都有实质内容, 视觉调用不浪费。
    """
    xs = sorted(p[0] for p in points)
    ys = sorted(p[1] for p in points)
    n = len(xs)
    if n < 4:
        return None

    def q(arr, k, total):
        i = min(int(len(arr) * k / total), len(arr) - 1)
        return arr[i]

    xb, yb = [xs[0]], [ys[0]]
    for k in range(1, cols):
        v = q(xs, k, cols)
        if v > xb[-1]:
            xb.append(v)
    xb.append(xs[-1])
    for k in range(1, rows):
        v = q(ys, k, rows)
        if v > yb[-1]:
            yb.append(v)
    yb.append(ys[-1])
    if len(xb) < 2 or len(yb) < 2:
        return None
    tiles = []
    for r in range(len(yb) - 1):
        for c in range(len(xb) - 1):
            tx0, tx1 = xb[c], xb[c + 1]
            ty0, ty1 = yb[r], yb[r + 1]
            ox, oy = (tx1 - tx0) * overlap, (ty1 - ty0) * overlap
            tiles.append((f'r{r + 1}c{c + 1}', (tx0 - ox, ty0 - oy, tx1 + ox, ty1 + oy)))
    return tiles


def _tiles_from_grid(bbox, cols, rows, overlap=0.08):
    """按网格把 bbox 切成 cols×rows 块(带 overlap 扩展, 防边界构件被切半丢失)。"""
    x0, y0, x1, y1 = bbox
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    cw, ch = w / cols, h / rows
    ox, oy = cw * overlap, ch * overlap
    tiles = []
    for r in range(rows):
        for c in range(cols):
            bx0 = x0 + c * cw
            by0 = y0 + r * ch
            tiles.append((f'r{r + 1}c{c + 1}',
                          (max(bx0 - ox, x0), max(by0 - oy, y0),
                           min(bx0 + cw + ox, x1), min(by0 + ch + oy, y1))))
    return tiles


def render_tiles(dxf_path, out_dir=None, grid=(3, 3), tiles=None, dpi=300,
                 overlap=0.08, max_long_inch=10.0, density_aware=True):
    """切块渲染 — v6.10 视觉精度突破(核心): 大图按块以高 dpi 分别渲染。

    动机: 整图渲染(14×10in @150dpi ≈ 2100×1500px)对 A0/A1 施工图意味着 1px≈40mm 实物,
    小符号(门窗号/索引/规格标注)不足 1px 必然漏检。
    做法: 图纸按网格(或显式语义块, 如轴线分区)切块, 每块单独渲染并提高 dpi,
    块内文字/符号的相对像素尺寸成倍放大 → 视觉可读性实质提升。

    参数:
      grid=(cols, rows) 机械网格; tiles=[(x0,y0,x1,y1) | (name,(x0,y0,x1,y1))] 显式块(优先)
      dpi 每块渲染分辨率(默认 300, 整图 150)
      overlap 块间重叠比例(默认 0.08, 防边界构件被切半)

    返回 meta: tiles=[{name, file, bbox}], 其中 bbox 为图纸坐标系范围(供坐标反算合并)。
    """
    import ezdxf
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(dxf_path), 'renders')
    os.makedirs(out_dir, exist_ok=True)

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    layers, bbox = _collect_entities(msp)
    base = os.path.splitext(os.path.basename(dxf_path))[0]

    if not bbox:
        return {'source': dxf_path, 'base': base, 'bbox': None, 'tiles': [],
                'note': '无可渲染实体, 切块为空'}

    # 预取实体代表点(避免每块全量遍历; 密度切块也要用)
    ents_pts = []
    for lay, ents in layers.items():
        for e in ents:
            ents_pts.append((lay, e, _entity_anchor(e)))
    pts = [p for _, _, p in ents_pts if p]

    # 块清单: 显式 tiles > 密度自适应 > 机械网格
    if tiles:
        spec = []
        for t in tiles:
            if isinstance(t, (list, tuple)) and len(t) == 2 and isinstance(t[0], str):
                spec.append((t[0], tuple(t[1])))
            else:
                spec.append((f't{len(spec) + 1}', tuple(t)))
        mode = 'explicit'
    else:
        spec = (_tiles_from_density(pts, grid[0], grid[1], overlap) if density_aware else None)
        mode = 'density' if spec else 'grid'
        if not spec:
            spec = _tiles_from_grid(bbox, grid[0], grid[1], overlap)

    meta = {'source': dxf_path, 'base': base, 'bbox': bbox, 'dpi': dpi,
            'grid': list(grid) if not tiles else None, 'overlap': overlap,
            'tile_mode': mode, 'tile_count': len(spec), 'tiles': []}

    for name, tb in spec:
        tx0, ty0, tx1, ty1 = tb
        bw, bh = (tx1 - tx0) or 1.0, (ty1 - ty0) or 1.0
        if bw >= bh:
            figsize = (max_long_inch, max(max_long_inch * bh / bw, 1.5))
        else:
            figsize = (max(max_long_inch * bw / bh, 1.5), max_long_inch)
        fig, ax = plt.subplots(figsize=figsize)
        drawn = 0
        for idx, (lay, ents) in enumerate(sorted(layers.items())):
            sel = []
            for lay2, e, pt in ents_pts:
                if lay2 != lay:
                    continue
                if pt is None or (tx0 <= pt[0] <= tx1 and ty0 <= pt[1] <= ty1):
                    sel.append(e)
            if sel:
                _draw_entities(ax, sel, _layer_color(lay, idx), frame_layer=_is_frame_layer(lay))
                drawn += len(sel)
        p = os.path.join(out_dir, f'{base}_tile_{name}.png')
        # v6.10: 切块图不画标题 — 实测模型会把渲染标题("船体大楼 / 分块 r1c1")当图纸内容读出,
        # 污染识别结果(假文字)。整图保留标题(便于人工核对), 切块图保持画面纯净。
        _finalize_fig(fig, ax, tb, '', p, dpi=dpi)
        meta['tiles'].append({'name': name, 'file': p, 'bbox': list(tb), 'entities': drawn})

    meta_path = os.path.join(out_dir, f'{base}_tiles_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='DXF 渲染层(V-0)')
    ap.add_argument('dxf', help='DXF 文件路径')
    ap.add_argument('--out-dir', default=None, help='输出目录(默认 图纸旁/renders)')
    ap.add_argument('--no-per-layer', action='store_true', help='不生成分层图')
    ap.add_argument('--tiles', default=None, help='切块渲染: 网格 cols x rows(如 3x3); 与整图并行输出')
    ap.add_argument('--tile-dpi', type=int, default=300, help='切块渲染 dpi(默认 300, 整图为 150)')
    args = ap.parse_args(argv)
    meta = render_dxf(args.dxf, args.out_dir, per_layer=not args.no_per_layer)
    print(f'渲染完成: {meta["base"]}_plan.png')
    print(f'  图层数: {meta["layer_count"]} | 整图: {meta["files"]["full"]}')
    if args.tiles:
        try:
            cols, rows = (int(x) for x in args.tiles.lower().split('x'))
        except Exception:
            print(f'  --tiles 格式错误: {args.tiles} (应为 3x3)')
            return meta
        tm = render_tiles(args.dxf, args.out_dir, grid=(cols, rows), dpi=args.tile_dpi)
        print(f'  切块: {tm["tile_count"]} 块 @ {tm["dpi"]}dpi → {os.path.join(args.out_dir or os.path.join(os.path.dirname(args.dxf), "renders"))}')
        for t in tm.get('tiles', [])[:8]:
            print(f'    {t["name"]}: {os.path.basename(t["file"])} 实体 {t["entities"]}')
    print(f'  元数据: {os.path.join(args.out_dir or os.path.dirname(args.dxf) + "/renders", meta["base"] + "_render_meta.json")}')
    return meta


if __name__ == '__main__':
    main()
