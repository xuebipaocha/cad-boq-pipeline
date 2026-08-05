"""
安装工程算量 — v5.12 分系统构件化版

v5.12 P1-3 安装深化:
- 构件模型优先(分系统: 电气/给排水/暖通/消防), 旧安装信息回退
- 管道/电缆/桥架/配管/风管/喷淋管道 长度与规格从构件模型消费
- 设备/阀门/灯具/开关插座/配电箱柜/卫生器具/消防设施 计数从构件模型消费
- 分项名称与旧版一致(基准单调不降), 新增分系统标记(备注)
"""
def calc(data):
    r = []
    mep = data.get('安装信息', {})
    texts = ' '.join(data.get('施工说明', []))
    cm = data.get('构件模型') or {}

    def _count_from_cm(key):
        items = cm.get(key, []) or []
        if not items:
            return None
        out = {}
        for it in items:
            out[it.get('编号', key)] = out.get(it.get('编号', key), 0) + it.get('数量', 1)
        return out

    cm_counts = {k: _count_from_cm(k) for k in ('设备', '阀门', '灯具', '开关插座',
                                                '配电箱柜', '卫生器具', '消防设施')}

    # ═══════ 给排水 ═══════
    pipes = mep.get('管道', [])
    cm_pipes = cm.get('管道') or []
    if cm_pipes:
        pipes = cm_pipes
    if not pipes:
        # 从施工说明中提取
        for sys_name, keywords in [('给水', ['给水','给水管','PPR','衬塑']),
                                    ('排水', ['排水','排水管','UPVC','HDPE']),
                                    ('雨水', ['雨水','雨水管'])]:
            if any(k in texts for k in keywords):
                pipes.append({'名称': f'{sys_name}管道', '长度_m': 0})

    pipe_by_system = {}
    for p in pipes:
        name = p.get('名称', p.get('编号', '管道'))
        length = p.get('长度_m', 0)
        if not pipe_by_system.get(name):
            pipe_by_system[name] = 0
        pipe_by_system[name] += length

    for sys_name, total_len in pipe_by_system.items():
        if total_len > 0:
            r.append({'分项名称': f'{sys_name}', '单位': 'm', '工程量': round(total_len, 2),
                     '计算式': str(total_len), '定额编号': '030801001'})
        else:
            r.append({'分项名称': f'{sys_name}', '单位': 'm', '工程量': 0,
                     '计算式': '待CAD提取', '定额编号': '030801001'})

    # 阀门/水表 (v4.0: dict 值求和而非 len(dict)——修复数量低估 bug)
    valves = cm_counts.get('阀门') or mep.get('阀门', [])
    if not valves:
        for kw in ['阀门','闸阀','截止阀','蝶阀']:
            if kw in texts:
                r.append({'分项名称': '阀门安装', '单位': '个', '工程量': 0, '计算式': '待CAD提取', '定额编号': '030803001'})
                break
    elif isinstance(valves, dict):
        valve_n = sum(valves.values()) if valves else 0
        r.append({'分项名称': '阀门安装', '单位': '个', '工程量': valve_n, '计算式': str(valve_n), '定额编号': '030803001'})
    else:
        r.append({'分项名称': '阀门安装', '单位': '个', '工程量': len(valves), '计算式': str(len(valves)), '定额编号': '030803001'})

    # 卫生器具 (v4.0: 按数量求和)
    fixtures = cm_counts.get('卫生器具') or mep.get('卫生器具', [])
    if isinstance(fixtures, dict):
        for fname, fcount in fixtures.items():
            r.append({'分项名称': f'{fname}安装', '单位': '组', '工程量': fcount,
                     '计算式': str(fcount), '定额编号': '031004001'})
    elif fixtures:
        for f in fixtures:
            r.append({'分项名称': f.get('名称','卫生器具')+'安装', '单位': '组', '工程量': f.get('数量',1),
                     '计算式': str(f.get('数量',1)), '定额编号': '031004001'})

    # ═══════ 电气 ═══════
    cables = mep.get('电缆', [])
    cm_cables = cm.get('电缆') or []
    if cm_cables:
        cables = [{'型号': c.get('型号') or c.get('规格', ''), '长度_m': c.get('长度_m', 0)} for c in cm_cables]
    if cables:
        for c in cables:
            r.append({'分项名称': f'电缆敷设({c.get("型号","")})', '单位': 'm', '工程量': c.get('长度_m',0),
                     '计算式': str(c.get('长度_m',0)), '定额编号': '030902001'})
    else:
        if any(k in texts for k in ['电缆','YJV','VV','BV']):
            r.append({'分项名称': '电缆敷设', '单位': 'm', '工程量': 0, '计算式': '待CAD提取', '定额编号': '030902001'})

    # 桥架
    trays = mep.get('桥架', [])
    cm_trays = cm.get('桥架') or []
    if cm_trays:
        trays = [{'规格': t.get('规格', ''), '长度_m': t.get('长度_m', 0)} for t in cm_trays]
    if trays:
        for t in trays:
            r.append({'分项名称': f'桥架安装({t.get("规格","")})', '单位': 'm', '工程量': t.get('长度_m',0),
                     '计算式': str(t.get('长度_m',0)), '定额编号': ''})
    else:
        if '桥架' in texts:
            r.append({'分项名称': '桥架安装', '单位': 'm', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})

    # 配管 (电线管)
    conduits = mep.get('管线', [])
    cm_conduits = cm.get('配管') or []
    if cm_conduits:
        conduits = [{'规格': c.get('规格', ''), '长度_m': c.get('长度_m', 0)} for c in cm_conduits]
    if conduits:
        for c in conduits:
            r.append({'分项名称': f'电线管敷设({c.get("规格","")})', '单位': 'm', '工程量': c.get('长度_m',0),
                     '计算式': str(c.get('长度_m',0)), '定额编号': '030901001'})
    else:
        if 'SC管' in texts or 'JDG' in texts or '电线管' in texts:
            r.append({'分项名称': '电线管敷设', '单位': 'm', '工程量': 0, '计算式': '待CAD提取', '定额编号': '030901001'})

    # 配电箱 (v4.1.5: 支持 dict {名称: 数量} 与 list)
    panels = cm_counts.get('配电箱柜') or mep.get('配电箱', [])
    if isinstance(panels, dict):
        for pname, pcount in panels.items():
            r.append({'分项名称': f'{pname}安装', '单位': '台', '工程量': pcount,
                     '计算式': str(pcount), '定额编号': '031001001'})
    elif panels:
        for p in panels:
            r.append({'分项名称': f'{p.get("名称","配电箱")}安装', '单位': '台', '工程量': p.get('数量',1),
                     '计算式': str(p.get('数量',1)), '定额编号': '031001001'})
    else:
        if '配电箱' in texts or 'AP' in texts or 'AL' in texts:
            r.append({'分项名称': '配电箱安装', '单位': '台', '工程量': 0, '计算式': '待CAD提取', '定额编号': '031001001'})

    # 灯具 (v4.1.5: 支持 dict {名称: 数量} 与 list)
    lights = cm_counts.get('灯具') or mep.get('灯具', [])
    if isinstance(lights, dict):
        for lname, lcount in lights.items():
            r.append({'分项名称': f'{lname}安装', '单位': '套', '工程量': lcount,
                     '计算式': str(lcount), '定额编号': ''})
    elif lights:
        for l in lights:
            r.append({'分项名称': f'{l.get("类型","灯具")}安装', '单位': '套', '工程量': l.get('数量',1),
                     '计算式': str(l.get('数量',1)), '定额编号': ''})
    else:
        if '灯' in texts:
            r.append({'分项名称': '灯具安装', '单位': '套', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})

    # 开关插座 (v5.1: 构件模型优先)
    sw_cm = cm_counts.get('开关插座') or {}
    if sw_cm:
        for sname, scount in sw_cm.items():
            r.append({'分项名称': f'{sname}安装', '单位': '个', '工程量': scount,
                     '计算式': str(scount), '定额编号': ''})
    elif '开关' in texts or '插座' in texts:
        r.append({'分项名称': '开关插座安装', '单位': '个', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})

    # ═══════ 暖通 ═══════
    ducts = (mep.get('暖通', {}) or {}).get('风管', [])
    cm_ducts = cm.get('风管') or []
    if cm_ducts:
        ducts = [{'规格': d.get('规格', ''), '面积_m2': d.get('面积_m2', 0)} for d in cm_ducts]
    if ducts:
        for d in ducts:
            r.append({'分项名称': f'风管安装({d.get("规格","")})', '单位': 'm²', '工程量': d.get('面积_m2',0),
                     '计算式': str(d.get('面积_m2',0)), '定额编号': ''})
    else:
        if any(k in texts for k in ['风管','通风','空调']):
            r.append({'分项名称': '风管安装', '单位': 'm²', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})

    if any(k in texts for k in ['风机','空调','排风']):
        r.append({'分项名称': '风机安装', '单位': '台', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})
        r.append({'分项名称': '空调设备安装', '单位': '台', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})

    # ═══════ 消防 ═══════
    fire_pipes = (mep.get('消防', {}) or {}).get('喷淋管道', [])
    cm_fire = cm.get('喷淋管道') or []
    if cm_fire:
        fire_pipes = [{'管径': f.get('规格', ''), '长度_m': f.get('长度_m', 0)} for f in cm_fire]
    if fire_pipes:
        for p in fire_pipes:
            r.append({'分项名称': f'喷淋管道({p.get("管径","")})', '单位': 'm', '工程量': p.get('长度_m',0),
                     '计算式': str(p.get('长度_m',0)), '定额编号': '030901001'})
    else:
        if '喷淋' in texts or '消火栓' in texts:
            r.append({'分项名称': '喷淋管道', '单位': 'm', '工程量': 0, '计算式': '待CAD提取', '定额编号': '030901001'})

    # 消防设施 (v5.1: 构件模型优先 — 消火栓/喷淋头等图块计数)
    fire_cm = cm_counts.get('消防设施') or {}
    if fire_cm:
        for fname, fcount in fire_cm.items():
            r.append({'分项名称': f'{fname}安装', '单位': '套', '工程量': fcount,
                     '计算式': str(fcount), '定额编号': '031003001'})
    elif '消火栓' in texts:
        r.append({'分项名称': '消火栓安装', '单位': '套', '工程量': 0, '计算式': '待CAD提取', '定额编号': '031003001'})
    if '报警' in texts or '烟感' in texts or '探头' in texts:
        r.append({'分项名称': '火灾报警设备安装', '单位': '个', '工程量': 0, '计算式': '待CAD提取', '定额编号': ''})

    # ═══════ 管网（市政管网、室外管网） ═══════
    outdoor = mep.get('管网', {})
    opipes = outdoor.get('管道', [])
    if opipes:
        for p in opipes:
            r.append({'分项名称': f'室外管道({p.get("名称","")})', '单位': 'm', '工程量': p.get('长度_m',0),
                     '计算式': str(p.get('长度_m',0)), '定额编号': '040501001'})
    else:
        if any(k in texts for k in ['室外管网','室外管道','给水管网','排水管网']):
            r.append({'分项名称': '室外管道', '单位': 'm', '工程量': 0, '计算式': '待CAD提取', '定额编号': '040501001'})

    manholes = outdoor.get('检查井', [])
    if manholes:
        r.append({'分项名称': '检查井', '单位': '座', '工程量': len(manholes), '计算式': str(len(manholes)), '定额编号': '040504001'})
    elif '检查井' in texts:
        r.append({'分项名称': '检查井', '单位': '座', '工程量': 0, '计算式': '待CAD提取', '定额编号': '040504001'})

    # ═══════ 设备 (v5.1: 构件模型优先) ═══════
    equip = cm_counts.get('设备') or mep.get('设备', {})
    if isinstance(equip, dict):
        for ename, eqty in equip.items():
            r.append({'分项名称': f'{ename}安装', '单位': '台', '工程量': eqty, '计算式': str(eqty), '定额编号': ''})

    return r
