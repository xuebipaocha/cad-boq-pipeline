"""
CAD 交互式查询工具（13个命令）
用法: python3 cad_tools.py <命令> <dxf文件> [选项]

命令:
  info       — 图纸基本信息
  layers     — 列出全部图层及实体数
  entities   — 实体类型统计
  texts      — 提取全部文字
  blocks     — 列出图块定义
  distance   — 计算两点间距
  search     — 按关键字搜索实体
  screenshot — 对区域/图层截图
  audit      — 图层合规审查
  purge      — 清理空图层
  list       — 按类型列出实体
  export     — 导出为SVG
  area       — 面积计算（鞋带公式）
"""
import sys, os, json, re, math
sys.stdout.reconfigure(encoding='utf-8')
import ezdxf
from ezdxf.math import Vec2

USAGE = __doc__

def cmd_info(dxf_path):
    """图纸基本信息"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    ents = list(msp)
    layers = set()
    types = {}
    for e in ents:
        layers.add(e.dxf.layer)
        t = e.dxftype()
        types[t] = types.get(t, 0) + 1
    # v5.3: ezdxf 新版本 Drawing 无 .dxf.mmin, 用 header $EXTMIN/$EXTMAX 兜底
    try:
        if hasattr(doc, 'dxf') and doc.dxf is not None:
            mmin, mmax = doc.dxf.mmin, doc.dxf.mmax
        else:
            mmin, mmax = doc.header.get('$EXTMIN'), doc.header.get('$EXTMAX')
        extent = [round(mmin[0],1), round(mmin[1],1), round(mmax[0],1), round(mmax[1],1)]
    except Exception:
        extent = None
    info = {
        '文件名': os.path.basename(dxf_path),
        '实体总数': len(ents),
        '图层数': len(layers),
        '实体类型': sorted(types.items(), key=lambda x: -x[1]),
        '模型空间范围': extent,
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))

def cmd_layers(dxf_path):
    """列出全部图层及实体数"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    layer_counts = {}
    for e in msp:
        lay = e.dxf.layer
        layer_counts[lay] = layer_counts.get(lay, 0) + 1
    result = [{'layer': k, 'entities': v} for k, v in sorted(layer_counts.items(), key=lambda x: -x[1])]
    print(json.dumps(result, ensure_ascii=False, indent=2))

def cmd_entities(dxf_path):
    """实体类型统计"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    types = {}
    for e in msp:
        t = e.dxftype()
        types[t] = types.get(t, 0) + 1
    print(json.dumps(sorted(types.items(), key=lambda x: -x[1]), ensure_ascii=False, indent=2))

def cmd_texts(dxf_path):
    """提取所有文字"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    texts = []
    for e in msp:
        if e.dxftype() == 'TEXT':
            texts.append({'text': e.dxf.text, 'layer': e.dxf.layer, 'pos': list(e.dxf.insert[:2])})
        elif e.dxftype() == 'MTEXT':
            texts.append({'text': e.dxf.text, 'layer': e.dxf.layer, 'pos': list(e.dxf.insert[:2])})
    print(json.dumps(texts, ensure_ascii=False, indent=2))

def cmd_blocks(dxf_path):
    """列出图块"""
    doc = ezdxf.readfile(dxf_path)
    blocks = []
    for bd in doc.blocks:
        if not bd.name.startswith('*'):
            types = {}
            for e in bd:
                t = e.dxftype()
                types[t] = types.get(t, 0) + 1
            blocks.append({'name': bd.name, 'entities': len(bd), 'types': types})
    print(json.dumps(blocks, ensure_ascii=False, indent=2))

def cmd_distance(dxf_path, x1, y1, x2, y2):
    """计算两点间距"""
    p1, p2 = Vec2(float(x1), float(y1)), Vec2(float(x2), float(y2))
    dist = p1.distance(p2)
    print(f'距离: {dist:.3f} (图纸单位)')

def cmd_search(dxf_path, keyword):
    """按关键字搜索"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    hits = []
    for e in msp:
        text = ''
        if e.dxftype() == 'TEXT': text = e.dxf.text
        elif e.dxftype() == 'MTEXT': text = e.dxf.text
        elif e.dxftype() == 'INSERT': text = e.dxf.name
        if keyword.lower() in str(text).lower():
            hits.append({'type': e.dxftype(), 'layer': e.dxf.layer, 'text': text, 'pos': list(e.dxf.insert[:2]) if hasattr(e.dxf, 'insert') else []})
    print(json.dumps(hits, ensure_ascii=False, indent=2))

def cmd_screenshot(dxf_path, output, layer=None, region=None, dpi=150):
    """导出截图"""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon
    except ImportError:
        print('需要 matplotlib'); return
    doc = ezdxf.readfile(dxf_path); msp = doc.modelspace()
    fig, ax = plt.subplots(figsize=(12, 10))
    for e in msp:
        if layer and e.dxf.layer != layer: continue
        if e.dxftype() == 'LWPOLYLINE':
            pts = [(p[0], p[1]) for p in e.get_points()]
            if len(pts) >= 3: ax.add_patch(Polygon(pts, fill=False, edgecolor='blue', linewidth=0.5))
        elif e.dxftype() == 'LINE':
            ax.plot([e.dxf.start[0], e.dxf.end[0]], [e.dxf.start[1], e.dxf.end[1]], 'k-', linewidth=0.3)
        elif e.dxftype() == 'CIRCLE':
            ax.add_patch(plt.Circle((e.dxf.center[0], e.dxf.center[1]), e.dxf.radius, fill=False, color='green', linewidth=0.5))
        elif e.dxftype() == 'TEXT':
            ax.text(e.dxf.insert[0], e.dxf.insert[1], e.dxf.text, fontsize=4, color='gray')
    ax.set_aspect('equal'); ax.axis('off')
    if region:
        x, y, w, h = [float(v) for v in region.split(',')]; ax.set_xlim(x, x+w); ax.set_ylim(y, y+h)
    plt.tight_layout(); plt.savefig(output, dpi=dpi, bbox_inches='tight'); plt.close()
    print(f'截图已保存: {output}')

def cmd_audit(dxf_path):
    """图层合规审查"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    issues = []
    zero_layer_ents = []
    empty_layers = set()
    layer_ents = {}
    for e in msp:
        lay = e.dxf.layer
        if lay == '0': zero_layer_ents.append(e.dxftype())
        if lay not in layer_ents: layer_ents[lay] = 0
        layer_ents[lay] += 1
    for lay, cnt in doc.layers.items():
        if lay not in layer_ents: empty_layers.add(lay)
    if zero_layer_ents: issues.append(f'{len(zero_layer_ents)}个实体在0层, 建议指定图层')
    if empty_layers: issues.append(f'{len(empty_layers)}个空图层: {list(empty_layers)[:5]}')
    if not issues: issues.append('未发现问题')
    print('\n'.join(issues))

def cmd_purge(dxf_path):
    """清理空图层"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    used_layers = {e.dxf.layer for e in msp}
    purged = []
    for lay in list(doc.layers):
        if lay.name not in used_layers:
            doc.layers.remove(lay)
            purged.append(lay.name)
    print(f'清理 {len(purged)} 个空图层: {purged[:10]}')

def cmd_list_by_type(dxf_path, etype='LWPOLYLINE'):
    """按类型列出实体"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    count = 0
    for e in msp:
        if e.dxftype() == etype:
            count += 1
    print(f'{etype}: {count}个')

def cmd_export_svg(dxf_path, output):
    """导出为SVG"""
    try:
        from dxf2svg import dxf_to_svg
    except ImportError:
        print('需要 dxf2svg: pip install dxf2svg'); return
    dxf_to_svg(dxf_path, output)
    print(f'SVG已保存: {output}')

def cmd_area(dxf_path):
    """面积计算（鞋带公式）"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    areas = []
    for e in msp:
        if e.dxftype() != 'LWPOLYLINE': continue
        pts = list(e.get_points())
        if len(pts) < 3: continue
        # 鞋带公式 Shoelace Formula
        n = len(pts)
        a = sum(pts[i][0] * pts[i+1][1] - pts[i+1][0] * pts[i][1] for i in range(n-1))
        area = abs(a) / 2
        areas.append({'layer': e.dxf.layer, 'vertices': n, 'area_m2': round(area, 2)})
    areas.sort(key=lambda x: -x['area_m2'])
    print(json.dumps(areas[:20], ensure_ascii=False, indent=2))
    if areas:
        total = sum(a['area_m2'] for a in areas)
        print(f'总面积: {round(total, 2)} m²')

def main():
    if len(sys.argv) < 3:
        print(USAGE); return
    cmd = sys.argv[1]; dxf = sys.argv[2]
    cmds = {
        'info': lambda: cmd_info(dxf),
        'layers': lambda: cmd_layers(dxf),
        'entities': lambda: cmd_entities(dxf),
        'texts': lambda: cmd_texts(dxf),
        'blocks': lambda: cmd_blocks(dxf),
        'search': lambda: cmd_search(dxf, sys.argv[3] if len(sys.argv) > 3 else ''),
        'audit': lambda: cmd_audit(dxf),
        'purge': lambda: cmd_purge(dxf),
        'list': lambda: cmd_list_by_type(dxf, sys.argv[3] if len(sys.argv) > 3 else 'LWPOLYLINE'),
        'export': lambda: cmd_export_svg(dxf, sys.argv[3] if len(sys.argv) > 3 else 'output.svg'),
        'area': lambda: cmd_area(dxf),
    }
    if cmd == 'distance' and len(sys.argv) >= 7:
        cmd_distance(dxf, sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
    elif cmd == 'screenshot':
        out = sys.argv[3] if len(sys.argv) > 3 else 'cad_output.png'
        la = [sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--layer' and i+1 < len(sys.argv)]
        ra = [sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--region' and i+1 < len(sys.argv)]
        cmd_screenshot(dxf, out, layer=la[0] if la else None, region=ra[0] if ra else None)
    elif cmd in cmds:
        cmds[cmd]()
    else:
        print(f'未知命令: {cmd}')

if __name__ == '__main__':
    main()
