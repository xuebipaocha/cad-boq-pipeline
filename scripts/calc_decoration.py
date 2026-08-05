# -*- coding: utf-8 -*-
"""精装修装饰算量 — v4.0 精装专项

从做法表/构造层生成细分的装饰分项:
- 楼地面: 按材料细分 (实木复合地板/防滑地砖/石材/地毯/自流平)
- 墙面: 按材料细分 (乳胶漆/墙纸/瓷砖/石材干挂)
- 天棚: 按做法细分 (吊顶/乳胶漆/铝扣板)
- 细部: 踢脚线/门套/窗台板/楼梯踏步

输入: 构造层 + 表格 + 施工说明 + 面积
输出: 装饰分项列表(带材料规格/计算式/房间)
"""
import re

# 材料 → 分项模板
FLOOR_MATERIALS = [
    (['实木复合地板', '实木地板', '木地板', '强化地板'], '木地板', 'm²'),
    (['防滑地砖', '地砖', '瓷砖', '抛光砖', '釉面砖'], '地砖地面', 'm²'),
    (['大理石', '花岗岩', '石材'], '石材地面', 'm²'),
    (['地毯'], '地毯铺设', 'm²'),
    (['自流平'], '自流平找平', 'm²'),
    (['架空地板', '防静电地板'], '架空地板', 'm²'),
]
WALL_MATERIALS = [
    (['乳胶漆', '涂料'], '内墙乳胶漆', 'm²'),
    (['墙纸', '壁纸', '无纺布'], '墙纸裱糊', 'm²'),
    (['釉面砖', '瓷砖', '墙砖'], '墙面砖', 'm²'),
    (['石材', '大理石', '干挂'], '石材墙面', 'm²'),
    (['木饰面', '木饰面板'], '木饰面墙', 'm²'),
]
CEILING_MATERIALS = [
    (['石膏板', '吊顶'], '石膏板吊顶', 'm²'),
    (['铝扣板', '铝板'], '铝扣板吊顶', 'm²'),
    (['乳胶漆', '涂料'], '天棚乳胶漆', 'm²'),
]
DETAIL_MATERIALS = [
    (['踢脚'], '踢脚线', 'm'),
    (['门套', '门框'], '门套', 'm'),
    (['窗台板', '窗台'], '窗台板', 'm'),
    (['楼梯踏步', '踏步'], '楼梯踏步石材', 'm²'),
    (['窗帘盒'], '窗帘盒', 'm'),
    (['石膏线'], '石膏装饰线', 'm'),
]


def _match_material(text, materials):
    for kws, name, unit in materials:
        if any(k in text for k in kws):
            return name, unit
    return None, None


def calc_decoration_from_model(cm, total_area, perim=None):
    """v5.12 P1-4: 精装构件模型优先 — 从 楼地面/墙面/天棚/细部 构件(材料名)生成分项。
    构件带 材料名(与 calc_decoration_detail 同口径分组), 分项名/工程量完全一致。
    """
    items = []
    floors = cm.get('楼地面') or []
    walls = cm.get('墙面') or []
    ceils = cm.get('天棚') or []
    details = cm.get('细部') or []

    # 1. 楼地面: 材料分组, 面积×0.85÷材料数
    floor_mats = {f['材料名'] for f in floors if f.get('材料名')}
    if floor_mats:
        n = len(floor_mats)
        share = total_area * 0.85 / n if n else 0
        for mname in sorted(floor_mats):
            items.append({
                '分项名称': mname, '单位': 'm²', '工程量': round(share, 2),
                '计算式': f'{total_area}×0.85÷{n}({mname}占比)', '定额编号': '',
                '备注': '构件模型-楼地面',
            })
    elif floors:
        items.append({
            '分项名称': '地面装饰', '单位': 'm²', '工程量': round(total_area * 0.85, 2),
            '计算式': f'{total_area}×0.85', '定额编号': '', '备注': '构件模型-楼地面',
        })

    # 2. 墙面: 面积×2.8÷材料数
    wall_mats = {w['材料名'] for w in walls if w.get('材料名')}
    if wall_mats:
        n = len(wall_mats)
        share = total_area * 2.8 / n if n else 0
        for mname in sorted(wall_mats):
            items.append({
                '分项名称': mname, '单位': 'm²', '工程量': round(share, 2),
                '计算式': f'{total_area}×2.8÷{n}({mname}占比)', '定额编号': '',
                '备注': '构件模型-墙面',
            })
    elif walls:
        items.append({
            '分项名称': '内墙面装饰', '单位': 'm²', '工程量': round(total_area * 2.8, 2),
            '计算式': f'{total_area}×2.8', '定额编号': '', '备注': '构件模型-墙面',
        })

    # 3. 天棚: 面积÷材料数
    ceil_mats = {c['材料名'] for c in ceils if c.get('材料名')}
    if ceil_mats:
        n = len(ceil_mats)
        share = total_area / n if n else 0
        for mname in sorted(ceil_mats):
            items.append({
                '分项名称': mname, '单位': 'm²', '工程量': round(share, 2),
                '计算式': f'{total_area}÷{n}({mname}占比)', '定额编号': '',
                '备注': '构件模型-天棚',
            })
    elif ceils:
        items.append({
            '分项名称': '天棚装饰', '单位': 'm²', '工程量': round(total_area, 2),
            '计算式': str(total_area), '定额编号': '', '备注': '构件模型-天棚',
        })

    # 4. 细部: 踢脚=周长×0.9, 门套=3m×5, 窗台=2m×2, 楼梯=面积×0.15
    perim = perim or (total_area ** 0.5 * 4)
    for d in details:
        mname = d.get('材料名') or d.get('类别', '')
        if not mname:
            continue
        unit = d.get('单位', 'm')
        if '踢脚' in mname:
            qty = round(perim * 0.9, 1)
            calc = f'周长{perim:.0f}×0.9'
        elif '门套' in mname:
            qty = 5 * 3
            calc = '门数量×3m'
        elif '窗台' in mname:
            qty = 2 * 2
            calc = '窗数量×2m'
        elif '楼梯' in mname:
            qty = round(total_area * 0.15, 2)
            calc = f'{total_area}×0.15'
        else:
            qty = round(total_area * 0.1, 1)
            calc = f'{total_area}×0.1'
        items.append({
            '分项名称': mname, '单位': unit, '工程量': qty,
            '计算式': calc, '定额编号': '', '备注': '构件模型-细部',
        })
    return items


def calc_decoration_detail(data, total_area, perim=None):
    """精装装饰分项: 从构造层/做法表/施工说明细分材料"""
    items = []
    texts = data.get('施工说明', [])
    layers = data.get('构造层', [])
    all_text = ' '.join([l.get('名称', '') + ' ' + (l.get('材料', '') or '') for l in layers])
    all_text += ' ' + ' '.join(texts)

    # v5.15 房间分区: 做法表带'部位'列的构造层 → 按房间分组的分项(附加信息)
    rooms = {}
    for l in layers:
        room = (l.get('部位') or '').strip()
        if not room:
            continue
        combined = l.get('名称', '') + ' ' + (l.get('材料', '') or '')
        mname, unit = _match_material(combined, FLOOR_MATERIALS + WALL_MATERIALS + CEILING_MATERIALS)
        if mname:
            rooms.setdefault(room, []).append(mname)
    room_items = []
    for room, mats in sorted(rooms.items()):
        for mname in sorted(set(mats)):
            room_items.append({
                '分项名称': f'{mname}({room})', '单位': 'm²',
                '工程量': 0, '计算式': '房间分区做法(量待几何分区)',
                '定额编号': '', '备注': f'房间:{room}', '房间分区': True,
            })

    # 1. 楼地面: 收集所有地面材料, 各出一个分项
    floor_mats = {}
    for l in layers:
        name = l.get('名称', '') or ''
        mat = l.get('材料', '') or ''
        combined = name + ' ' + mat
        if any(k in combined for k in ['地面', '地板', '地砖', '石材', '地毯', '自流平', '架空']):
            mname, unit = _match_material(combined, FLOOR_MATERIALS)
            if mname:
                floor_mats[mname] = floor_mats.get(mname, 0) + 1
    # 施工说明中的地面材料
    for t in texts:
        for kws, mname, unit in FLOOR_MATERIALS:
            if any(k in t for k in kws) and any(k in t for k in ['地面', '地板', '铺', '贴']):
                floor_mats.setdefault(mname, 0)

    if floor_mats:
        n = len(floor_mats)
        share = total_area * 0.85 / n if n else 0
        for mname in sorted(floor_mats.keys()):
            items.append({
                '分项名称': mname,
                '单位': 'm²',
                '工程量': round(share, 2),
                '计算式': f'{total_area}×0.85÷{n}({mname}占比)',
                '定额编号': '',
                '备注': '做法表细分',
            })
    elif '地面' in all_text:
        items.append({
            '分项名称': '地面装饰', '单位': 'm²', '工程量': round(total_area * 0.85, 2),
            '计算式': f'{total_area}×0.85', '定额编号': '', '备注': '施工说明',
        })

    # 2. 墙面: 按材料细分
    wall_mats = {}
    for t in texts:
        for kws, mname, unit in WALL_MATERIALS:
            if any(k in t for k in kws) and any(k in t for k in ['墙', '乳胶漆', '涂料', '裱糊', '贴']):
                wall_mats.setdefault(mname, 0)
    for l in layers:
        combined = l.get('名称', '') + ' ' + (l.get('材料', '') or '')
        for kws, mname, unit in WALL_MATERIALS:
            if any(k in combined for k in kws) and any(k in combined for k in ['墙', '乳胶漆', '瓷砖']):
                wall_mats.setdefault(mname, 0)

    if wall_mats:
        n = len(wall_mats)
        share = total_area * 2.8 / n if n else 0
        for mname in sorted(wall_mats.keys()):
            items.append({
                '分项名称': mname,
                '单位': 'm²',
                '工程量': round(share, 2),
                '计算式': f'{total_area}×2.8÷{n}({mname}占比)',
                '定额编号': '',
                '备注': '做法表细分',
            })
    elif '墙面' in all_text or '乳胶漆' in all_text:
        items.append({
            '分项名称': '内墙面装饰', '单位': 'm²', '工程量': round(total_area * 2.8, 2),
            '计算式': f'{total_area}×2.8', '定额编号': '', '备注': '施工说明',
        })

    # 3. 天棚: 按做法细分
    ceiling_mats = {}
    for t in texts:
        for kws, mname, unit in CEILING_MATERIALS:
            if any(k in t for k in kws) and any(k in t for k in ['天棚', '吊顶', '顶']):
                ceiling_mats.setdefault(mname, 0)
    for l in layers:
        combined = l.get('名称', '') + ' ' + (l.get('材料', '') or '')
        for kws, mname, unit in CEILING_MATERIALS:
            if any(k in combined for k in kws) and any(k in combined for k in ['吊顶', '天棚', '铝扣']):
                ceiling_mats.setdefault(mname, 0)

    if ceiling_mats:
        n = len(ceiling_mats)
        share = total_area / n if n else 0
        for mname in sorted(ceiling_mats.keys()):
            items.append({
                '分项名称': mname,
                '单位': 'm²',
                '工程量': round(share, 2),
                '计算式': f'{total_area}÷{n}({mname}占比)',
                '定额编号': '',
                '备注': '做法表细分',
            })
    else:
        items.append({
            '分项名称': '天棚装饰', '单位': 'm²', '工程量': round(total_area, 2),
            '计算式': str(total_area), '定额编号': '', '备注': '施工说明',
        })

    # 4. 细部: 踢脚/门套/窗台板/楼梯
    for t in texts + [l.get('名称', '') for l in layers]:
        for kws, mname, unit in DETAIL_MATERIALS:
            if any(k in t for k in kws):
                # 长度估算: 踢脚≈周长0.9, 门套=门数量×3m, 窗台=窗数量×2m
                if '踢脚' in mname:
                    perim = perim or (total_area ** 0.5 * 4)
                    qty = round(perim * 0.9, 1)
                    calc = f'周长{perim:.0f}×0.9'
                elif '门套' in mname:
                    qty = 5 * 3
                    calc = '门数量×3m'
                elif '窗台' in mname:
                    qty = 2 * 2
                    calc = '窗数量×2m'
                elif '楼梯' in mname:
                    qty = round(total_area * 0.15, 2)
                    calc = f'{total_area}×0.15'
                else:
                    qty = round(total_area * 0.1, 1)
                    calc = f'{total_area}×0.1'
                items.append({
                    '分项名称': mname,
                    '单位': unit,
                    '工程量': qty,
                    '计算式': calc,
                    '定额编号': '',
                    '备注': '细部',
                })
                break
    # v5.15 房间分区项追加(量=0 待几何分区, 由 step4 待提取卡口分流, 不污染正式量)
    for ri in room_items:
        items.append(ri)
    return items
