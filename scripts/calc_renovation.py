# -*- coding: utf-8 -*-
"""房屋建筑大修/改造算量 — v5.4 D6 / v5.5 R7 / v5.6 H2 防编造

大修/改造工程的分项与新建不同: 无土方/基础/主体结构, 核心是
外墙/屋面/内装的维修、材料更换、局部加固。

v5.6 防编造改造:
- **废除 '周长×建筑高度' 公式**(周长来源不可靠, 乘积无工程意义)
- 外墙面积: 立面图 DIMENSION 提取(水平×垂直标注) > 文字面积标注 > **待提取**
- 所有数字走 quality 三要素校验; 无证据 → 待提取, 绝不估算
- 输出带 数据来源 四级标记(实测/文字标注/估算/待提取)
"""
import re

from reno_parser import parse_renovation_text
from quality import (SRC_MEASURED, SRC_TEXT, SRC_ESTIMATED, SRC_PENDING,
                     check_formula, check_number)


def _extract_height_m(texts, default=None):
    """建筑高度: '建筑高度 16.0m' → 16.0(带单位+量级校验)。无证据 → None"""
    tc = ' '.join(texts or [])
    for pat in (r'建筑高度[：:为]?\s*(\d+\.?\d*)\s*(m|米)', r'檐高[：:为]?\s*(\d+\.?\d*)\s*(m|米)'):
        m = re.search(pat, tc)
        if m:
            v = float(m.group(1))
            ok, _ = check_number(v, m.group(2), '建筑高度')
            if ok:
                return v
    return default


def _facade_area_from_dims(recog):
    """外墙面积: 图纸 DIMENSION 提取(水平×垂直)。

    v6.4 修复(2026-08-06 现场图纸暴露):
    - 原实现取"全局最大水平标注×全局最大垂直标注", 不同视图(平面/剖面/立面)的
      标注会跨视图乱配, 且 value(标注文字)与 target_len(几何长度)可能严重不符
      (如 227830 vs 17009) — 造成虚假面积。
    - 现按标注 y 位置聚类为视图带, 带内组合 max(水平)×max(垂直);
    - value 与 target_len 偏差>20% 的标注视为矛盾, 面积结果标"待核"(图面文字
      证据保留, 但提醒人工复核)。
    """
    dims = recog.get('标注关联', []) or []
    if not dims:
        dims = recog.get('标注尺寸', []) or []
    rows = []
    for d in dims:
        if not isinstance(d, dict):
            continue
        t = d.get('type', '')
        v = d.get('value') or d.get('target_len') or 0
        tl = d.get('target_len') or 0
        try:
            v = float(v)
            tl = float(tl)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        rows.append({'t': t, 'v': v, 'tl': tl, 'x': d.get('x', 0), 'y': d.get('y', 0)})
    if not rows:
        return None, SRC_PENDING, '无立面图尺寸标注'
    # 同视图分带: 按 y 排序, 相邻标注 gap 超过"当前总跨度 30%"或 5000 单位 → 新带
    rows.sort(key=lambda r: r['y'])
    y0 = rows[0]['y']
    y_span = rows[-1]['y'] - y0
    bands, cur = [], [rows[0]]
    for r in rows[1:]:
        gap = r['y'] - cur[-1]['y']
        if gap > max(y_span * 0.3, 5000.0):
            bands.append(cur)
            cur = [r]
        else:
            cur.append(r)
    bands.append(cur)
    best, best_note = None, ''
    for band in bands:
        widths = [r['v'] for r in band if r['t'] in ('horizontal', '水平')]
        heights = [r['v'] for r in band if r['t'] in ('vertical', '垂直')]
        if not widths or not heights:
            continue
        w = max(widths) / 1000.0  # mm→m
        h = max(heights) / 1000.0
        area = round(w * h, 2)
        # v6.4: value(标注文字) vs target_len(几何长度) 一致性校验 — 先看整体缩放:
        # 若带内绝大多数标注的 value/target_len 比率接近同一常数(缩放出图),
        # 则 value 为设计值, 不标矛盾; 仅偏离群体的个别标注视为真矛盾。
        ratios = [r['v'] / r['tl'] for r in band if r['tl'] > 0]
        bad = []
        if ratios:
            ratios.sort()
            med = ratios[len(ratios) // 2]
            bad = [r for r in band if r['tl'] > 0
                   and abs(r['v'] / r['tl'] - med) / max(med, 1e-9) > 0.5]
        note = f'图纸尺寸标注 {w}×{h}'
        if bad:
            if len(bad) > 5:
                note += ' (标注文字与几何长度多处不符,待核)'
            else:
                note += f' (标注文字与几何长度不符{len(bad)}处,待核)'
        if best is None or area > best:
            best, best_note = area, note
    if best:
        return best, SRC_MEASURED, best_note
    return None, SRC_PENDING, '无立面图尺寸标注'


def _parse_finishing_table(recog):
    """v6.4: 从识图结果'表格'解析做法表 → [{编号, 名称, 位置, 做法, 备注}]。
    做法表为 类别|编号|名称|所用位置|构造做法|备注 结构(楼1/墙1/棚1 驱动)。
    """
    rows_out = []
    for tb in (recog.get('表格') or []):
        if tb.get('type') != '做法表':
            continue
        headers = tb.get('headers') or []
        if not any('编号' in h.replace(' ', '') for h in headers):
            continue
        for row in tb.get('rows') or []:
            cells = row.get('cells') or []
            if len(cells) < 2:
                continue
            rec = {}
            for i, h in enumerate(headers):
                if i >= len(cells):
                    break
                hh = h.replace(' ', '')
                cs = str(cells[i]).strip()
                if '编号' in hh:
                    rec['编号'] = cs
                elif '名称' in hh:
                    rec['名称'] = cs
                elif '位置' in hh or '部位' in hh:
                    rec['位置'] = cs
                elif '做法' in hh or '构造' in hh:
                    rec['做法'] = cs
                elif '备注' in hh:
                    rec['备注'] = cs
            if rec.get('编号'):
                rows_out.append(rec)
    return rows_out


def _room_kw_match(pos_text, rooms):
    """v6.6: 部位文本 → 命中的房间组列表。
    部位如 '分段：办公室' / '办公室，走廊，楼梯间' / '卫1、卫3、卫4'。
    返回 (命中房间组列表, 未命中部位词列表)。
    """
    from room_geometry import ROOM_KEYWORDS
    matched, unmatched = [], []
    # 提取部位词: 房间关键词 + 卫N 编号
    words = re.findall(r'卫\s*\d+|' + '|'.join(ROOM_KEYWORDS), pos_text or '')
    for w in words:
        w = w.strip()
        hit = None
        for r in rooms:
            if w in r.get('房间名', '') or r.get('房间名', '') in w:
                hit = r
                break
        if hit:
            if not any(m is hit for m in matched):
                matched.append(hit)
        else:
            if w not in unmatched:
                unmatched.append(w)
    return matched, unmatched


def _interior_finishing(data):
    """v6.4: 室内装饰重做算量 — 做法表(楼/墙/棚/窗台)驱动 + 施工范围(一至三层) + 平面面积×层数。

    大修工程核心内容(拆除后重新装饰: '一至三层房间内地板地面、涂料墙面及涂料顶棚面层拆除,
    拆除后重新装饰'), 原算量完全缺失(只出外墙系)。
    面积口径(估算并标'待核'):
    - 楼面/顶棚 = 平面闭合面积 × 层数
    - 墙面     = 平面周长 × 层高 × 层数
    - 窗台     = 无面积证据 → 待提取
    返回 (分项列表, {楼面_m2, 墙面_m2, 顶棚_m2}) — 面积字典供拆除分项复用。
    """
    rows = _parse_finishing_table(data)
    if not rows:
        return [], {}
    texts = data.get('施工说明', []) or []
    tc = ' '.join(texts)
    # 层数: 施工范围文字 '一至三层'/'一二三层'/'三层' → 3; 否则默认 2
    if re.search(r'一[至到~～]三|一二三|三层|1[~～-]3', tc):
        floors = 3
    else:
        floors = 2
    areas = data.get('面积区域') or []
    plane_area = max((a.get('面积_m2', 0) or 0 for a in areas), default=0)
    perimeter = max((a.get('周长_m', 0) or 0 for a in areas), default=0)
    if plane_area <= 0:
        return [], {}  # 无平面面积证据 → 不估算(拆除/重做分项走待提取)
    floor_h = 3.2
    elev = data.get('标高参数') or {}
    if elev.get('层高_m'):
        try:
            floor_h = float(elev['层高_m'])
        except (TypeError, ValueError):
            pass
    items = []
    area_map = {'楼面_m2': round(plane_area * floors, 2),
                '顶棚_m2': round(plane_area * floors, 2),
                '墙面_m2': round(perimeter * floor_h * floors, 2)}
    rooms = data.get('房间') or []
    for r in rows:
        code = (r.get('编号') or '').strip()
        name = (r.get('名称') or '').strip()
        pos = (r.get('位置') or '').strip()
        recipe = (r.get('做法') or '').strip()
        if not name:
            continue
        note = f'做法表[{code}]{name}'
        if pos:
            note += f' {pos}'
        if recipe:
            note += f' {recipe[:46]}'
        # v6.6: 做法分劈 — 部位全部命中房间几何 → 按房间实算; 部分/全不命中 → 保持估算
        room_hits, room_miss = _room_kw_match(pos, rooms) if rooms else ([], [])
        if room_hits and not room_miss:
            area_sum = sum(float(rr.get('面积_m2', 0) or 0) for rr in room_hits)
            perim_sum = sum(float(rr.get('周长_m', 0) or 0) for rr in room_hits)
            n_rooms = sum(int(rr.get('数量', 1) or 1) for rr in room_hits)
            if code.startswith('楼'):
                q = round(area_sum * floors, 2)
                note += f'; 面积=房间{area_sum}m²×{floors}层({n_rooms}间, 实测房间分区)'
                items.append(_q(f'室内楼地面{name}', 'm²', q, note, SRC_MEASURED, 部位=pos))
                continue
            elif code.startswith('墙'):
                q = round(perim_sum * floor_h * floors, 2)
                note += f'; 面积=房间周长{perim_sum}m×层高{floor_h}×{floors}层({n_rooms}间, 实测房间分区)'
                items.append(_q(f'室内墙面{name}', 'm²', q, note, SRC_MEASURED, 部位=pos))
                continue
            elif code.startswith('棚'):
                q = round(area_sum * floors, 2)
                note += f'; 面积=房间{area_sum}m²×{floors}层({n_rooms}间, 实测房间分区)'
                items.append(_q(f'室内顶棚{name}', 'm²', q, note, SRC_MEASURED, 部位=pos))
                continue
        if room_hits and room_miss:
            # v6.9.5 思维层①: 部分命中分劈 — 做法位置部分命中房间几何(如
            # PVC'办公室，走廊，楼梯间'只命中办公室) → 实测部分 + 未命中部位待提取
            # (造价人思维: 不能把未知部位按全面积估算, 也不编造; 实测+待核拆开)
            area_sum = sum(float(rr.get('面积_m2', 0) or 0) for rr in room_hits)
            perim_sum = sum(float(rr.get('周长_m', 0) or 0) for rr in room_hits)
            n_rooms = sum(int(rr.get('数量', 1) or 1) for rr in room_hits)
            miss_txt = '、'.join(room_miss[:3])
            if code.startswith('楼'):
                q = round(area_sum * floors, 2)
                note += f'; 面积=房间{area_sum}m²×{floors}层({n_rooms}间实测)'
                items.append(_q(f'室内楼地面{name}', 'm²', q, note, SRC_MEASURED, 部位=pos))
                items.append(_q(f'室内楼地面{name}({miss_txt}待核)', 'm²', 0,
                                f'待提取: 部位[{miss_txt}]无房间几何证据, 不得按全面积估算',
                                SRC_PENDING, 部位=pos))
                continue
            elif code.startswith('墙'):
                q = round(perim_sum * floor_h * floors, 2)
                note += f'; 面积=房间周长{perim_sum}m×层高{floor_h}×{floors}层({n_rooms}间实测)'
                items.append(_q(f'室内墙面{name}', 'm²', q, note, SRC_MEASURED, 部位=pos))
                items.append(_q(f'室内墙面{name}({miss_txt}待核)', 'm²', 0,
                                f'待提取: 部位[{miss_txt}]无房间几何证据', SRC_PENDING, 部位=pos))
                continue
            elif code.startswith('棚'):
                q = round(area_sum * floors, 2)
                note += f'; 面积=房间{area_sum}m²×{floors}层({n_rooms}间实测)'
                items.append(_q(f'室内顶棚{name}', 'm²', q, note, SRC_MEASURED, 部位=pos))
                items.append(_q(f'室内顶棚{name}({miss_txt}待核)', 'm²', 0,
                                f'待提取: 部位[{miss_txt}]无房间几何证据', SRC_PENDING, 部位=pos))
                continue
        if code.startswith('楼'):
            q = area_map['楼面_m2']
            note += f'; 面积=平面{plane_area}×{floors}层(待核,多做法需按房间分劈)'
            items.append(_q(f'室内楼地面{name}', 'm²', q, note, SRC_ESTIMATED, 部位=pos))
        elif code.startswith('墙'):
            q = area_map['墙面_m2']
            note += f'; 面积=周长{perimeter}×层高{floor_h}×{floors}层(待核)'
            items.append(_q(f'室内墙面{name}', 'm²', q, note, SRC_ESTIMATED, 部位=pos))
        elif code.startswith('棚'):
            q = area_map['顶棚_m2']
            note += f'; 面积=平面{plane_area}×{floors}层(待核)'
            items.append(_q(f'室内顶棚{name}', 'm²', q, note, SRC_ESTIMATED, 部位=pos))
        elif code.startswith('窗台'):
            items.append(_q(f'窗台{name}', 'm²', 0, '待提取: 无窗台面积证据', SRC_PENDING, 部位=pos))
    return items, area_map


def _facade_area(recog):
    """外墙面积 — 通用逻辑: 从图纸提取(文字面积标注 > 立面图尺寸标注)。
    v5.7: 删除项目特定公式(扣窗/侧壁/北侧局部)。任何项目的特定算法
    不得写进通用代码 — 通用逻辑只做: 有证据→提取, 无证据→待提取。
    """
    from quality import SRC_MEASURED, SRC_TEXT, SRC_ESTIMATED, SRC_PENDING
    texts = recog.get('施工说明', []) or []
    # 1. 文字面积标注(带单位+量级校验)
    for t in texts:
        m = re.search(r'(?:外墙|立面|粉刷)[^0-9]{0,10}?(\d+(?:\.\d+)?)\s*(m2|平方米|㎡)', t)
        if m:
            v = float(m.group(1))
            ok, _ = check_number(v, m.group(2), t)
            if ok:
                return v, SRC_TEXT, f'文字标注 {v}{m.group(2)}'
    # 2. 立面图尺寸标注(水平×垂直)
    area, src, note = _facade_area_from_dims(recog)
    if area:
        return area, src, note
    return None, SRC_PENDING, '无外墙面积证据'


def _q(name, unit, qty, note, src, **kw):
    item = {'分项名称': name, '单位': unit, '工程量': round(qty, 2),
            '计算式': note, '定额编号': '', '备注': '大修', '数据来源': src}
    item.update(kw)
    return item


def calc(data):
    """大修/改造算量(仅房屋建筑专业, 由 step3 按 工程性质 分发)"""
    r = []
    texts = data.get('施工说明', []) or []
    tc = ' '.join(texts)

    # 外墙面积(通用: 文字标注 > 立面图尺寸 > 待提取; 无项目特定公式)
    facade, facade_src, facade_note = _facade_area(data)

    # v6.4: 室内装饰重做(做法表驱动) — 大修核心内容, 先算面积供拆除分项复用
    interior_items, area_map = _interior_finishing(data)
    r.extend(interior_items)
    floor_area = area_map.get('楼面_m2', 0)
    wall_area = area_map.get('墙面_m2', 0)
    ceiling_area = area_map.get('顶棚_m2', 0)

    # ── 结构化解析(文字证据) ──
    parsed = parse_renovation_text(texts)
    demos = parsed['拆除项']
    qty = parsed['数量参数']
    layers = parsed['做法层']

    # ── 1. 拆除分项(每个拆除项一个分项) ──
    covered = set()
    if '雨水管' in tc or '落水管' in tc:
        covered.add(('雨水管', '雨水斗'))
        covered.add(('雨水管', ''))
    for d in demos:
        part = d['部位'] or ''
        obj = d['对象'] or ''
        if (part, obj) in covered:
            continue
        if '屋面' in (part + obj):
            continue  # 屋面拆除 → 屋面翻新分项覆盖
        name = f'{part}{obj}拆除' if part != obj else f'{part}拆除'
        if d['范围']:
            name = f'{part}{obj}{d["范围"]}拆除'
        if d.get('数量'):
            # v5.6: 有数量但单位可能缺失 → 三要素校验已在 parser 完成
            r.append(_q(name, 'm²', d['数量'], f'{d["数量"]}(文字标注)', SRC_TEXT))
        elif facade and ('外墙' in part or '涂料' in obj):
            r.append(_q(name, 'm²', facade, f'{facade_note}', facade_src))
        else:
            # v6.4: 拆除面积复用室内装饰面积(做法表+平面面积×层数, 同范围重做)
            area, area_note = 0, ''
            if any(k in (part + obj) for k in ('地面', '楼面', '地板', '找平')):
                area, area_note = floor_area, '室内楼面面积(平面×层数,待核)'
            elif any(k in (part + obj) for k in ('顶棚', '天棚')):
                area, area_note = ceiling_area, '室内顶棚面积(平面×层数,待核)'
            elif any(k in (part + obj) for k in ('墙', '涂料')):
                area, area_note = wall_area, '室内墙面面积(周长×层高×层数,待核)'
            if area > 0:
                r.append(_q(name, 'm²', area, f'{area_note}', SRC_ESTIMATED))
            else:
                r.append(_q(name, 'm²', 0, '待提取: 无面积证据', SRC_PENDING))

    # ── 2. 外墙做法分项(做法层, 按工程分项归并) ──
    # v5.6: 只归并"面层/基层/防水"三大类, 设计说明中的孤立材料词
    # (密封胶/界面剂/UPVC等)不独立成项 — 防做法层过细
    layer_names = []
    for l in layers:
        mat = l.get('材料', '')
        if not mat:
            continue
        if '抗裂砂浆' in mat or '网格布' in mat:
            key = '外墙抗裂砂浆网格布'
        elif '砂浆' in mat:
            key = '外墙抹灰'
        elif '防水' in mat:
            key = '外墙防水'
        elif '仿石' in mat or '真石漆' in mat:
            key = '外墙仿石涂料'
        elif '涂料' in mat or '漆' in mat or '腻子' in mat:
            key = '外墙涂料'
        else:
            continue  # 非三大类材料不独立成项
        if key not in layer_names:
            layer_names.append(key)
    if layer_names:
        for key in layer_names:
            if facade:
                r.append(_q(key, 'm²', facade, f'{facade_note}', facade_src))
            else:
                r.append(_q(key, 'm²', 0, '待提取: 无外墙面积证据', SRC_PENDING))
    elif ('防水' in tc or '涂料' in tc or '仿石' in tc) and facade:
        if '防水' in tc:
            r.append(_q('外墙防水', 'm²', facade, f'{facade_note}', facade_src))
        if '涂料' in tc or '仿石' in tc:
            r.append(_q('外墙仿石涂料' if '仿石' in tc else '外墙涂料', 'm²', facade,
                        f'{facade_note}', facade_src))
    elif ('防水' in tc or '涂料' in tc or '仿石' in tc):
        # 有关键词但外墙面积待提取 → 待提取分项(不估算)
        if '防水' in tc:
            r.append(_q('外墙防水', 'm²', 0, '待提取: 无外墙面积证据', SRC_PENDING))
        if '涂料' in tc or '仿石' in tc:
            r.append(_q('外墙仿石涂料' if '仿石' in tc else '外墙涂料', 'm²', 0,
                        '待提取: 无外墙面积证据', SRC_PENDING))

    # ── v6.8: 防水上翻口径(GB55030-2022 §4.6.4 强制条文触发) ──
    # 图纸注明上翻高度 → 独立列'防水层上翻'(周长×高度); 未注明 → 图纸疑问(不编造)
    if '防水' in tc:
        # v6.8: 上翻高度双单位识别 — 图纸常写 '≥300mm' 或 '0.3m'
        # (船体大楼原文: '余部位除特殊注明外均上翻…防水层(两遍成活) 0.3m')
        mm_m = (re.search(r'上翻[^\d]{0,20}[≥>]?(\d{2,4})\s*(?:mm|毫米)', tc)
                or re.search(r'翻起[^\d]{0,20}[≥>]?(\d{2,4})\s*(?:mm|毫米)', tc))
        m_m = re.search(r'上翻[^\d]{0,40}?(\d+\.?\d*)\s*m(?!m)', tc)
        if mm_m:
            up_val = float(mm_m.group(1)) / 1000
        elif m_m and 0.05 <= float(m_m.group(1)) <= 3.0:
            up_val = float(m_m.group(1))
        else:
            up_val = None

        perim_fd = max((float(a.get('周长_m', 0) or 0) for a in (data.get('面积区域') or [])), default=0)
        if up_val is not None and perim_fd > 0:
            r.append(_q('防水层上翻', 'm²', round(perim_fd * up_val, 2),
                        f'周长{perim_fd:.1f}×上翻{up_val}m(图纸标注, GB55030-2022 §4.6.4)', SRC_TEXT))
        else:
            r.append(_q('防水层上翻', 'm²', 0,
                        '待提取: 图纸未注明防水上翻高度, GB55030-2022 §4.6.4 强制要求'
                        '用水房间翻起≥250mm/盥洗处≥1200mm/淋浴区≥2000mm', SRC_PENDING))

    # ── 3. 雨水管更换(精确参数优先) ──
    if '雨水管' in tc or '落水管' in tc:
        if qty.get('雨水斗_个'):
            n = int(qty['雨水斗_个'])
            r.append(_q('雨水斗更换', '个', n, f'{n}个(文字标注)', SRC_TEXT))
        if qty.get('雨水管_m'):
            ln = qty['雨水管_m']
            r.append(_q('雨水管更换', 'm', ln, f'{ln}m(文字标注)', SRC_TEXT))
        elif qty.get('雨水管_根'):
            n = int(qty['雨水管_根'])
            r.append(_q('雨水管更换', '根', n, f'{n}根(文字标注)', SRC_TEXT))
        else:
            r.append(_q('雨水管更换', 'm', 0, '待提取: 无雨水管数量', SRC_PENDING))

    # ── 4. 楼梯维修(拆除+重做) ──
    if '楼梯' in tc or '踏步' in tc:
        if qty.get('维修面积_m2'):
            a = qty['维修面积_m2']
            r.append(_q('楼梯面层维修', 'm²', a, f'{a}m²(维修面积标注)', SRC_TEXT))
        else:
            r.append(_q('楼梯面层维修', 'm²', 0, '待提取: 无维修面积', SRC_PENDING))

    # ── 5. 屋面翻新 ──
    if any('屋面' in d.get('部位', '') or '屋面' in d.get('对象', '') for d in demos):
        # v6.4: 屋面面积复用平面闭合面积(屋顶投影=平面面积, 标待核)
        roof_area = max((a.get('面积_m2', 0) or 0 for a in data.get('面积区域') or []), default=0)
        if roof_area > 0:
            r.append(_q('屋面翻新', 'm²', round(roof_area, 2),
                        f'平面面积{roof_area}(屋顶投影,待核)', SRC_ESTIMATED))
        else:
            r.append(_q('屋面翻新', 'm²', 0, '待提取: 无屋面面积证据', SRC_PENDING))

    # ── 6. 外立面脚手架(依赖外墙面积) ──
    if facade:
        r.append(_q('外立面脚手架', 'm²', facade, f'{facade_note}', facade_src))
    else:
        r.append(_q('外立面脚手架', 'm²', 0, '待提取: 无外墙面积证据', SRC_PENDING))

    # ── v6.5: 门窗更换分项(设计内容"门窗" + 门窗表驱动) ──
    windows = data.get('门窗') or []
    if '门窗' in tc or any('门窗' in (d.get('部位') or '') for d in (data.get('设计意图') or {}).get('设计内容', []) or []):
        if windows:
            total_area = round(sum(float(w.get('洞口面积_m2', 0) or 0) * int(w.get('数量', 1) or 1) for w in windows), 2)
            # v6.6: 门窗归类 — LC-* 是铝合金窗(原 startswith('C') 漏数 LC, 94 樘窗全丢),
            # M-/WM- 是门; 编号含'窗'字也算窗
            def _is_door(wid):
                return wid.startswith('M') or wid.startswith('WM') or '门' in wid
            def _is_win(wid):
                return 'LC' in wid or wid.startswith('C') or '窗' in wid
            # v6.9.3: 按 类型(门/窗) × 材质 分组拆项 — 造价人列项方式, 组价按材质
            # 匹配定额(塑钢门→8-9 塑钢成品门安装, 塑钢窗→8-71); 材质未注明标'未注明'
            groups = {}
            for w in windows:
                wid = str(w.get('门窗号', ''))
                mat = str(w.get('材质', '') or '').strip() or '未注明'
                key = ('门' if _is_door(wid) else '窗', mat)
                area = float(w.get('洞口面积_m2', 0) or 0) * int(w.get('数量', 1) or 1)
                groups[key] = groups.get(key, 0) + area
            n_doors = sum(int(w.get('数量', 1) or 1) for w in windows if _is_door(str(w.get('门窗号', ''))))
            n_wins = sum(int(w.get('数量', 1) or 1) for w in windows if _is_win(str(w.get('门窗号', ''))))
            for (typ, mat), area in groups.items():
                area = round(area, 2)
                mat_disp = mat if mat != '未注明' else '未注明材质'
                r.append(_q(f'{mat_disp}{typ}更换', 'm²', area,
                            f'{mat_disp}{typ} 洞口面积{area}m²(门窗表)；按GB50854§4.0.8选m²', SRC_TEXT))
                r.append(_q(f'{mat_disp}{typ}拆除', 'm²', area,
                            f'{mat_disp}{typ} 洞口面积{area}m²(门窗表)；与更换配套', SRC_TEXT))
            r.append(_q('门窗更换(汇总)', '樘', n_doors + n_wins,
                        f'门{n_doors}樘+窗{n_wins}樘(门窗表, 面积分项见上)', SRC_TEXT))
            r.append(_q('门窗洞口面积', 'm²', total_area,
                        f'{total_area}m²(门窗表洞口合计, 墙扣减用)', SRC_TEXT))
        else:
            r.append(_q('门窗更换', '樘', 0, '待提取: 设计内容含门窗, 无门窗表', SRC_PENDING))

    # ── v6.5: 走廊分项(设计内容"走廊"触发) ──
    if any('走廊' in (d.get('部位') or '') for d in (data.get('设计意图') or {}).get('设计内容', []) or []) or '走廊' in tc:
        corridor_note = '待提取: 走廊面积需按平面图分区'
        r.append(_q('走廊墙面维修', 'm²', 0, corridor_note, SRC_PENDING))
        r.append(_q('走廊地面维修', 'm²', 0, corridor_note, SRC_PENDING))

    return r
