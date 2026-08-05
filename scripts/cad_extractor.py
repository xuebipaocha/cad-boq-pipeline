"""
CAD深度分析扩展 — 提取基础/柱/梁/板/管道/苗木
"""
import sys, os, re
sys.stdout.reconfigure(encoding='utf-8')
from units import shoelace_area  # v5.13: 统一鞋带公式(与 analyze_cad 同源)

def classify_element(bounds, layer_name, dxftype):
    """根据几何特征+图层名识别构件类型"""
    area = bounds.get('area', 0)
    width = abs(bounds.get('max_x',0) - bounds.get('min_x',0))
    height = abs(bounds.get('max_y',0) - bounds.get('min_y',0))
    aspect = max(width,height) / max(min(width,height), 0.001)
    
    ln = layer_name.upper()
    if '基础' in layer_name or 'JCT' in ln or 'FOUND' in ln or 'FT' in ln:
        if area >= 50: return '筏板基础'
        elif area >= 5: return '独立基础'
        else: return '基础' if area > 0 else '基础构件'
    elif '桩' in layer_name or 'PILE' in ln: return '桩基'
    elif '柱' in layer_name or 'KZ' in ln or 'COL' in ln:
        if area < 5 and aspect < 2: return '框架柱'
        else: return '柱'
    elif '梁' in layer_name or 'KL' in ln or 'BEAM' in ln:
        if aspect > 3: return '框架梁'
        else: return '梁'
    elif '板' in layer_name or 'LB' in ln or 'SLAB' in ln: return '楼板'
    elif '墙' in layer_name or 'WALL' in ln: return '墙体'
    return dxftype

def extract_building_elements(doc, msp):
    """从CAD提取建筑构件信息 — v3.1 全量统计版

    改进：
    - 移除每图层仅取前5个实体的抽样限制，改为全量分类统计。
    - 将标注尺寸关联到构件，提取实际截面尺寸。
    - 输出每个构件分类的数量和尺寸样本。
    """
    info = {'基础':{}, '柱':[], '梁':[], '板':[]}

    LAYER_MAP = {
        '基础': ['基础', '独立基础', 'JCT', 'JCL', '承台', '筏板', '基础梁', 'FOUND', 'FT'],
        '柱': ['柱', 'KZ', '构造柱', 'GZ', '框架柱', '柱子', 'COL', 'A-COL', 'C-'],
        '梁': ['梁', 'KL', '框架梁', 'LL', '连梁', '梁体', 'BEAM', 'A-BEAM', 'B-'],
        '板': ['板', 'LB', '楼板', '现浇板', '板厚', 'SLAB', 'A-SLAB', 'S-'],
        '墙': ['墙', 'WALL', 'A-WALL', 'W-'],
    }

    layer_entities = {}
    for e in msp:
        lay = e.dxf.layer
        if lay not in layer_entities:
            layer_entities[lay] = {'count':0, 'types':{}, 'entities':[]}
        layer_entities[lay]['count'] += 1
        t = e.dxftype()
        layer_entities[lay]['types'][t] = layer_entities[lay]['types'].get(t, 0) + 1
        if t in ('LWPOLYLINE', 'POLYLINE', 'LINE', 'CIRCLE', 'ELLIPSE', 'INSERT', 'SOLID', '3DFACE'):
            bounds = None
            try:
                if t == 'LWPOLYLINE':
                    pts = list(e.get_points('xy'))
                    if len(pts) < 2: continue
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    bounds = {'min_x': min(xs), 'max_x': max(xs), 'min_y': min(ys), 'max_y': max(ys),
                              'width': round(max(xs)-min(xs),1), 'height': round(max(ys)-min(ys),1)}
                    if len(pts) >= 3:
                        # v5.13: 统一鞋带公式(units.shoelace_area), 与 analyze_cad 同源
                        bounds['area'] = shoelace_area(pts)
                    if len(pts) >= 3 and shoelace_area(pts) < 1:
                        bounds['is_closed'] = True
                elif t == 'LINE':
                    bounds = {'min_x': min(e.dxf.start[0], e.dxf.end[0]),
                              'max_x': max(e.dxf.start[0], e.dxf.end[0]),
                              'min_y': min(e.dxf.start[1], e.dxf.end[1]),
                              'max_y': max(e.dxf.start[1], e.dxf.end[1]),
                              'length': e.dxf.start.distance(e.dxf.end)}
                elif t == 'INSERT':
                    bounds = {'x': e.dxf.insert[0], 'y': e.dxf.insert[1], 'name': e.dxf.name}
                elif t == 'CIRCLE':
                    bounds = {'cx': e.dxf.center[0], 'cy': e.dxf.center[1], 'radius': e.dxf.radius,
                              'min_x': e.dxf.center[0]-e.dxf.radius, 'max_x': e.dxf.center[0]+e.dxf.radius,
                              'min_y': e.dxf.center[1]-e.dxf.radius, 'max_y': e.dxf.center[1]+e.dxf.radius}
            except:
                pass
            if bounds:
                layer_entities[lay]['entities'].append({'type': t, 'bounds': bounds})

    # 提取标注尺寸并关联到构件
    dims = extract_dimensions(msp)
    dim_matches = match_dims_to_elements(dims, layer_entities, threshold=800) if dims else []

    # 全量几何分类统计
    element_counts = {}
    element_sizes = {}
    for cat, keywords in LAYER_MAP.items():
        matched_layers = {lay: ent for lay, ent in layer_entities.items() if any(k in lay for k in keywords)}
        if matched_layers:
            total_ents = sum(l['count'] for l in matched_layers.values())
            info[cat] = {'图层数': len(matched_layers), '实体数': total_ents, '图层': list(matched_layers.keys())[:5]}
            for lay, ent in matched_layers.items():
                for e in ent['entities']:
                    b = e['bounds']
                    elem_type = classify_element(b, lay, e['type'])
                    element_counts[elem_type] = element_counts.get(elem_type, 0) + 1
                    if 'width' in b and 'height' in b and b['width'] > 0:
                        if elem_type not in element_sizes:
                            element_sizes[elem_type] = []
                        if len(element_sizes[elem_type]) < 10:
                            element_sizes[elem_type].append({'宽_mm': b['width'], '高_mm': b['height']})
    info['构件分类'] = element_counts
    info['构件尺寸样本'] = element_sizes
    info['标注关联'] = dim_matches[:20]
    return info

def extract_pipe_lengths(doc, msp, insunits=4):
    """提取管道/管线长度 — v3.1 按管径分类版 + v4.0 单位感知

    改进：
    - 从图层名中提取管径（DN100、De110、Φ50 等）。
    - 按管径/系统分类统计管道长度。
    - 长度按 $INSUNITS 换算（毫米÷1000, 米直接, 英寸×25.4）。
    - 移除单字"管"关键词，避免误伤"管理/管线标注"等图层。
    """
    from units import length_to_m
    # v4.2: 支持工程前缀图层 (W-给水 S-消防 E-电气 等) + 英文系统名
    pipe_keywords = ['给水', '排水', '消防', '管道', '管线', 'PIPE', 'WATER', 'GAS',
                     '喷淋', '消火', '雨水', '污水', '废水', '通气', '给排水',
                     '冷凝', '冷媒', '燃气', '油管', '风管', 'SEWER', 'SUPPLY',
                     'W-JS', 'W-PS', 'W-WS', 'W-XH', 'S-SP', 'S-HY', 'S-XF', 'SPRINK']
    # v5.10: 中文电气图层(天正CAD) — 线缆层 '电-强电平面-线缆-1/3' 等;
    # 与给排水管道分开统计(系统语义不同, 下游配管/电缆分开消费)
    electrical_keywords = ['强电', '弱电', '线缆', '电-', '电缆', '配电', '照明回路',
                           '应急照明', '干线', '桥架']
    # v4.1.5: 桥架/线槽 从管道中分离 (不算管道长度)
    tray_keywords = ['桥架', '线槽', '托盘', 'CABLE', 'TRAY', 'E-CT', 'CT']
    total_len = 0
    pipes_by_layer = {}
    elec_by_layer = {}  # v5.10: 电气线缆单独统计(不混入给排水管道)

    for e in msp:
        lay = e.dxf.layer
        if not any(k in lay for k in pipe_keywords + electrical_keywords):
            continue
        if any(k in lay for k in tray_keywords):
            continue  # 桥架不计入管道
        try:
            seg_len = 0
            if e.dxftype() == 'LINE':
                seg_len = e.dxf.start.distance(e.dxf.end)
            elif e.dxftype() == 'LWPOLYLINE':
                # v5.10: get_points() 返回坐标元组(非 Vec3), .distance() 崩溃
                # (tuple 无 distance 属性, 被 except 静默吞掉 → 多段线长度恒 0)
                # 且只连相邻顶点(不开环): range(-1,...) 会把 2 顶点开放线段的
                # 首尾闭合边也算进去(线缆 190m 被重复算成 380m)
                pts = list(e.get_points())
                seg_len = sum(((pts[i][0]-pts[i+1][0])**2 + (pts[i][1]-pts[i+1][1])**2) ** 0.5
                              for i in range(len(pts)-1))
            if seg_len > 0:
                lay_m = round(length_to_m(seg_len, insunits), 2)
                is_elec = any(k in lay for k in electrical_keywords) and not any(k in lay for k in pipe_keywords)
                if is_elec:
                    # v5.10: 电气线缆不进 total_len(管道口径) — 避免被
                    # 'CAD提取管线'当给排水管道双计/错配(190m 电缆误配 030801001)
                    if lay not in elec_by_layer:
                        elec_by_layer[lay] = {'长度_m': 0, '段数': 0}
                    elec_by_layer[lay]['长度_m'] += lay_m
                    elec_by_layer[lay]['段数'] += 1
                else:
                    total_len += lay_m
                    if lay not in pipes_by_layer:
                        pipes_by_layer[lay] = {'长度_m': 0, '段数': 0}
                    pipes_by_layer[lay]['长度_m'] += lay_m
                    pipes_by_layer[lay]['段数'] += 1
        except:
            continue

    # 从图层名提取管径并归并
    import re
    diameter_pattern = re.compile(r'(?:DN|De|DE|Φ|φ|d)(\d+)', re.I)
    system_map = {'给水': '给水', '排水': '排水', '污水': '排水', '废水': '排水', '雨水': '雨水',
                  '消防': '消防', '消火': '消防', '喷淋': '消防', '通气': '通气', '燃气': '燃气', 'GAS': '燃气',
                  # v4.2: 工程前缀图层 W-/S-/XH- 等
                  'W-JS': '给水', 'W-PS': '排水', 'W-WS': '污水', 'W-XH': '消火', 'W-Y': '雨水',
                  'S-SP': '喷淋', 'S-HY': '消火', 'S-XF': '消防', 'SUPPLY': '给水', 'SEWER': '排水',
                  'WATER': '给水'}
    pipes_by_diameter = {}
    for lay, info in pipes_by_layer.items():
        m = diameter_pattern.search(lay)
        dia = m.group(0).upper() if m else '未标管径'
        system = '未知系统'
        for kw, sys in system_map.items():
            if kw in lay:
                system = sys; break
        key = f'{system}_{dia}'
        if key not in pipes_by_diameter:
            pipes_by_diameter[key] = {'长度_m': 0, '段数': 0, '系统': system, '管径': dia}
        pipes_by_diameter[key]['长度_m'] += info['长度_m']
        pipes_by_diameter[key]['段数'] += info['段数']

    # v5.10: 电气线缆按层归并 (系统=强电/弱电/照明, 供 '安装信息.电缆' 消费)
    elec_by_system = {}
    for lay, info in elec_by_layer.items():
        system = '强电'
        if '弱电' in lay:
            system = '弱电'
        elif '照明' in lay:
            system = '照明'
        key = f'{system}_{lay}'
        if key not in elec_by_system:
            elec_by_system[key] = {'长度_m': 0, '段数': 0, '系统': system, '图层': lay}
        elec_by_system[key]['长度_m'] += info['长度_m']
        elec_by_system[key]['段数'] += info['段数']

    return {
        '总长度_m': round(total_len, 2),
        '段数': sum(p['段数'] for p in pipes_by_layer.values()),
        '按图层': pipes_by_layer,
        '按管径': pipes_by_diameter,
        '电气线缆': elec_by_system,  # v5.10: {系统_图层: {长度/段数/系统/图层}}
    }

def count_blocks(doc, msp):
    """统计图块插入 — v3.1 扩展分类版

    改进：
    - 扩展安装工程图块识别：阀门、灯具、开关插座、配电箱、卫生器具、消火栓、喷淋头、桥架。
    - 扩展园林图块识别：花坛、景石、铺装。
    - 输出各类图块的数量和名称。
    """
    blocks = {}
    for e in msp:
        if e.dxftype() != 'INSERT':
            continue
        name = e.dxf.name
        blocks[name] = blocks.get(name, 0) + 1

    # 苗木图块
    tree_kw = ['树', '乔木', '灌木', '草坪', 'tree', 'plant', '苗木', '花卉', '花', '竹']
    tree_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in tree_kw)}

    # 设备图块
    equip_kw = ['泵', '风机', '空调', '设备', 'equip', 'pump', 'fan', 'ahu', '冷水', '冷却', '锅炉', '水箱']
    equip_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in equip_kw)}

    # 阀门图块
    valve_kw = ['阀门', 'valve', '闸阀', '蝶阀', '止回', '球阀', '截止']
    valve_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in valve_kw)}

    # 灯具图块
    light_kw = ['灯', 'light', 'lamp', '灯具', '荧光', 'LED', '射灯', '筒灯', '吸顶']
    light_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in light_kw)}

    # 开关插座图块
    switch_kw = ['开关', 'switch', '插座', 'socket', 'outlet', '面板']
    switch_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in switch_kw)}

    # 配电箱柜图块
    panel_kw = ['配电箱', '配电柜', '箱', '柜', 'panel', 'box', 'cabinet', '开关柜']
    panel_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in panel_kw)}

    # 卫生器具图块
    sanitary_kw = ['马桶', '蹲便', '坐便', '洗手', '盆', '浴', '小便', '卫生', 'toilet', 'basin', 'sink', 'bathtub']
    sanitary_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in sanitary_kw)}

    # 消防图块
    fire_kw = ['消火栓', '消防', '喷淋', '灭火', 'fire', 'hydrant', 'sprinkler', 'extinguish']
    fire_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in fire_kw)}

    # 园林硬景图块
    hardscape_kw = ['花坛', '景石', '铺装', '座椅', '凳', '亭', '廊', '花架', '栏杆']
    hardscape_blocks = {k: v for k, v in blocks.items() if any(x in k.lower() for x in hardscape_kw)}

    return {
        'total_blocks': len(blocks),
        'tree_blocks': tree_blocks if tree_blocks else {},
        'equip_blocks': equip_blocks if equip_blocks else {},
        'valve_blocks': valve_blocks if valve_blocks else {},
        'light_blocks': light_blocks if light_blocks else {},
        'switch_blocks': switch_blocks if switch_blocks else {},
        'panel_blocks': panel_blocks if panel_blocks else {},
        'sanitary_blocks': sanitary_blocks if sanitary_blocks else {},
        'fire_blocks': fire_blocks if fire_blocks else {},
        'hardscape_blocks': hardscape_blocks if hardscape_blocks else {},
        'all_blocks': {k: v for k, v in sorted(blocks.items(), key=lambda x: -x[1])[:20]},
    }


def detect_scale(msp):
    """检测图纸比例：对比标注值和图形实测值
    v5.3: 异常值防护 — 过滤 0/负/超界(>1e6) 比值, 防止 ODA 转换后
    DIMENSION 无测量值(94/156 为 0)时产生 3.4e12 天文数字比例
    """
    ratios = []
    for e in msp:
        if e.dxftype() != 'DIMENSION': continue
        try:
            dim_val = e.get_measurement()
            if not dim_val or dim_val <= 0: continue
            dp = e.dxf.defpoint
            # 在标注位置附近找直线或闭合多段线
            for e2 in msp:
                if e2 is e: continue
                try:
                    if e2.dxftype() == 'LINE':
                        # 线段两端到标注点的距离
                        d1 = ((e2.dxf.start[0]-dp[0])**2 + (e2.dxf.start[1]-dp[1])**2)**0.5
                        d2 = ((e2.dxf.end[0]-dp[0])**2 + (e2.dxf.end[1]-dp[1])**2)**0.5
                        if min(d1,d2) < 200:  # 在标注点附近
                            geom_len = e2.dxf.start.distance(e2.dxf.end)
                            if geom_len > 0:
                                r = dim_val / geom_len
                                # v5.3: 合理比例范围 1:5000~5000:1(实际工程 1:100 最常见)
                                if 1e-4 <= r <= 1e4:
                                    ratios.append(r)
                                break
                    elif e2.dxftype() == 'LWPOLYLINE':
                        pts = list(e2.get_points())
                        for j in range(len(pts)):
                            d = ((pts[j][0]-dp[0])**2 + (pts[j][1]-dp[1])**2)**0.5
                            if d < 200:
                                # 测量这条边的长度
                                nxt = pts[(j+1)%len(pts)]
                                edge_len = ((pts[j][0]-nxt[0])**2 + (pts[j][1]-nxt[1])**2)**0.5
                                if edge_len > 0:
                                    r = dim_val / edge_len
                                    if 1e-4 <= r <= 1e4:
                                        ratios.append(r)
                                    break
                        if ratios: break
                except: pass
        except: pass

    if not ratios: return 1.0
    # 取出现最频繁的比例作为图纸比例
    from collections import Counter
    ratio_freq = Counter(round(r, 4) for r in ratios)
    return ratio_freq.most_common(1)[0][0]


def extract_dimensions(msp):
    """从DXF中提取标注尺寸"""
    dims = []
    for e in msp:
        if e.dxftype() != 'DIMENSION': continue
        try:
            meas = e.get_measurement()
            if meas and meas > 0:
                lay = e.dxf.layer
                text = e.get_text() or str(int(meas))
                dp = e.dxf.defpoint
                dims.append({
                    'layer': lay, 'measurement': round(meas, 1),
                    'text': text, 'pos': (round(dp[0],1), round(dp[1],1)),
                })
        except:
            pass
    return dims


def get_element_sizes(msp):
    """获取构件实际尺寸（以标注为准，图形为辅）"""
    # 1. 先检测图纸比例
    scale = detect_scale(msp)
    
    # 2. 提取所有标注
    dims = extract_dimensions(msp)
    
    result = {
        'scale': scale,
        'dimensions': dims,
        'size_note': '标注值' if len(dims) > 0 else '图形实测',
        'elements': [],
    }
    
    # 3. 按图层分组构件
    for e in msp:
        if e.dxftype() not in ('LWPOLYLINE', 'CIRCLE', 'LINE', 'INSERT'): continue
        lay = e.dxf.layer
        try:
            bounds = {}
            if e.dxftype() == 'LWPOLYLINE':
                pts = list(e.get_points())
                xs = [p[0]*scale for p in pts]
                ys = [p[1]*scale for p in pts]
                bounds = {'min_x': min(xs), 'max_x': max(xs),
                         'min_y': min(ys), 'max_y': max(ys),
                         'width': round(max(xs)-min(xs),1),
                         'height': round(max(ys)-min(ys),1)}
                if len(pts) >= 3:
                    # v5.13: 统一鞋带公式(units.shoelace_area), 与 analyze_cad 同源
                    bounds['area'] = round(shoelace_area(pts) * scale * scale / 1e6, 2)  # mm²→m²
            result['elements'].append({'layer': lay, 'type': e.dxftype(), 'bounds': bounds})
        except: pass
    
    return result


def match_dims_to_elements(dims, layer_entities, threshold=500):
    """将标注尺寸关联到附近的实体"""
    matches = []
    for dim in dims:
        px, py = dim['pos']
        best_dist = float('inf')
        best_elem = None
        for lay, ent in layer_entities.items():
            for e in ent.get('entities', []):
                b = e.get('bounds', {})
                if 'min_x' in b:
                    cx = (b['min_x'] + b['max_x']) / 2
                    cy = (b['min_y'] + b['max_y']) / 2
                    dist = ((cx-px)**2 + (cy-py)**2)**0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_elem = {'layer': lay, 'elem_type': e['type']}
        if best_dist < threshold:
            matches.append({'layer': dim['layer'], 'measurement': dim['measurement'], 'pos': dim['pos'], 'dim_text': dim['text']})
    return matches
