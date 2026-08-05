# -*- coding: utf-8 -*-
"""图块属性+嵌套展开提取器 — v4.0 第一批

增强 count_blocks:
1. 读取 INSERT 的 ATTRIB 属性 (块属性存规格/型号/编号等)
2. 嵌套块递归展开 (块内包含 INSERT 时展开到最深层)
3. 块名+属性组合分类, 输出图块明细 [{name, attrs, count, nested}]
4. 扩展分类: 属性中带规格关键词的块归入更细类别
"""
import re


def expand_block_refs(doc, insert_entity, depth=0, max_depth=5):
    """展开嵌套块: 返回 [{name, attrs, insert_pt, layer}] 列表"""
    if depth > max_depth:
        return []
    out = []
    name = insert_entity.dxf.name
    # ATTRIB 属性
    attrs = {}
    try:
        for attrib in insert_entity.attribs:
            attrs[attrib.dxf.tag or ''] = attrib.dxf.text or ''
    except Exception:
        pass
    out.append({'name': name, 'attrs': attrs,
                'x': insert_entity.dxf.insert.x, 'y': insert_entity.dxf.insert.y,
                'layer': insert_entity.dxf.layer})
    # 嵌套块: 块定义内的 INSERT
    try:
        block = doc.blocks.get(name)
        if block is not None:
            for sub in block:
                if sub.dxftype() == 'INSERT':
                    nested = expand_block_refs(doc, sub, depth + 1, max_depth)
                    for n in nested:
                        # 嵌套块坐标 = 父块插入点 + 子块相对位置 (简化: 不处理旋转/缩放)
                        n['x'] += insert_entity.dxf.insert.x
                        n['y'] += insert_entity.dxf.insert.y
                        out.append(n)
    except Exception:
        pass
    return out


def collect_blocks(doc, msp):
    """主入口: 收集所有图块(含嵌套) + 属性; 过滤 *U 匿名块"""
    blocks = []
    seen = set()
    for e in msp.query('INSERT'):
        try:
            key = (e.dxf.name, round(e.dxf.insert.x, 1), round(e.dxf.insert.y, 1))
            if key in seen:
                continue
            seen.add(key)
            expanded = expand_block_refs(doc, e)
            for blk in expanded:
                if blk['name'].startswith('*'):
                    continue  # 匿名块 (add_auto_blockref 产物)
                blocks.append(blk)
        except Exception:
            continue
    return blocks


# 属性关键词 → 规格信息
ATTR_SPEC_KEYWORDS = {
    '阀门': ['DN', 'PN', '公称'],
    '灯具': ['功率', 'LED', 'W数', '瓦'],
    '设备': ['kW', '功率', '型号'],
    '配电箱': ['回路', 'AP', 'AL'],
    '卫生器具': ['型号', '规格'],
    '桩': ['桩径', '桩长'],
}


def attr_spec(attrs):
    """从属性提取规格摘要: 'DN100 PN16'"""
    parts = []
    for tag, val in attrs.items():
        if val and re.search(r'[A-Za-z0-9]', val) and len(val) <= 20:
            parts.append(f'{tag}={val}' if tag else val)
    return ' '.join(parts[:6])


def classify_with_attrs(name, attrs, layer=None):
    """块名+属性+图层 联合分类
    v5.10: 图层名信号兜底 — 真实图纸(天正CAD)块名多为加密编码
    (HC000134等), 但图层名携带真实语义('电-强电平面-设备'),
    块名/属性都不认识时用图层名分类, 否则设备全落'其他'(155 INSERT 事故)。
    """
    import re
    cls = None
    # 属性中的规格关键词 → 更细类别
    joined = ' '.join(attrs.values())
    for cat, kws in ATTR_SPEC_KEYWORDS.items():
        if any(k in joined for k in kws):
            cls = cat
            break
    if cls is None:
        # 块名关键词兜底 (与原 count_blocks 逻辑一致 + v4.2 英文/前缀扩展)
        # 苗木优先(避免 TREE-MAPLE 里的 AP 误配配电箱)
        if any(k in name for k in ['树', '乔木', '灌木', 'tree', 'plant', 'TREE', 'SHRUB', 'PLANT', '苗木']):
            cls = '苗木'
        elif any(k in name for k in ['泵', '风机', '空调', '设备', 'equip', 'pump', 'fan', 'PUMP', 'FAN', 'AHU', 'CHILLER']):
            cls = '设备'
        elif any(k in name for k in ['阀', 'valve', 'VALVE', 'GATE', 'BFLY']):
            cls = '阀门'
        elif any(k in name for k in ['灯', 'light', 'lamp', 'LIGHT', 'LAMP', 'FLUOR']):
            cls = '灯具'
        elif any(k in name for k in ['配电箱', '配电柜', 'panel', 'PANEL', 'DB']):
            cls = '配电箱柜'
        elif any(k in name for k in ['马桶', '蹲便', '洗手', '盆', '浴', 'toilet', 'basin', 'SINK', 'WC', 'BATH']):
            cls = '卫生器具'
        elif any(k in name for k in ['消火栓', '消防', '喷淋', '灭火', 'fire', 'hydrant', 'sprinkler',
                                     'HYDRANT', 'SPRINKLER', 'SMOKE', 'ALARM']):
            cls = '消防设施'
        elif any(k in name for k in ['开关', 'switch', '插座', 'socket', 'SWITCH', 'SOCKET', 'OUTLET']):
            cls = '开关插座'
    # v5.10: 图层名兜底 — 块名不认识(HC编码/匿名)时, 图层名是唯一语义信号
    if cls is None and layer:
        ln = (layer or '').lower()
        if any(k in ln for k in ['应急照明', '照明', '灯具']):
            cls = '灯具'
        elif any(k in ln for k in ['配电', '电气箱', '电箱', '强电-设备', '弱电箱']):
            cls = '配电箱柜'
        elif any(k in ln for k in ['开关', '插座']):
            cls = '开关插座'
        elif any(k in ln for k in ['空调', '风机', '水泵', '设备']):
            cls = '设备'
        elif any(k in ln for k in ['消火栓', '消防', '喷淋', '烟感', '报警']):
            cls = '消防设施'
        elif any(k in ln for k in ['卫生器具', '洁具', '马桶', '洗手盆']):
            cls = '卫生器具'
        elif any(k in ln for k in ['阀门', '阀']):
            cls = '阀门'
    return cls


def summarize(blocks):
    """汇总: {类别: [{name, attrs, count}]}
    v5.1: 增加 x_m/y_m 位置字段(图纸单位→米, 由调用方传 scale 或按默认 mm)
    """
    from collections import Counter
    summary = {}
    for blk in blocks:
        cls = classify_with_attrs(blk['name'], blk['attrs'], blk.get('layer')) or '其他'
        key = (blk['name'], attr_spec(blk['attrs']))
        entry = summary.setdefault(cls, {})
        entry.setdefault('_items', []).append(key)
        # v5.1: 保留坐标(图纸单位); v5.10: 保留图层名(设备可读化用)
        entry.setdefault('_pos', {})[key] = (blk.get('x', 0), blk.get('y', 0))
        entry.setdefault('_layers', {})[key] = blk.get('layer', '')
    out = {}
    for cls, data in summary.items():
        cnt = Counter(data['_items'])
        pos = data.get('_pos', {})
        out[cls] = [{'name': n, 'spec': s, 'count': c,
                     'layer': data.get('_layers', {}).get((n, s), ''),
                     'x': round(pos[(n, s)][0], 1), 'y': round(pos[(n, s)][1], 1)}
                    for (n, s), c in cnt.items()]
    return out


# v5.10: 天正CAD加密块名(HC000134/A$C...等) → 可读名
# 规则: 图层名提取语义 (取'设备'等类别词后的具体部位词), 块名兜底
def readable_name(blk_name, layer, cls):
    """加密块名可读化: 图层名语义优先, 块名尾号保粒度(不同符号不合并)。
    如 ('HC000429', '电-强电平面-设备', '设备') → '电-强电平面-设备(HC000429)'
    (带尾号让 23 个 HC000134 与 4 个 HC000429 分开成项, 不混成'39台')"""
    if not blk_name:
        return layer or cls or '构件'
    if re.match(r'^(HC|A\$|_)[A-Za-z0-9$]+$', blk_name):
        # 天正加密块名: 图层名(真实语义) + 块名尾号(区分符号)
        if layer:
            return f'{layer}({blk_name})'
        return f'{cls}块({blk_name})' if cls and cls != '其他' else blk_name
    return blk_name
