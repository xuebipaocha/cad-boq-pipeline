# -*- coding: utf-8 -*-
"""标高符号提取器 — v4.0 第一批

识别 DXF 图纸中的标高符号:
1. ▼/▲/▽ 字符 (TEXT) 或 标高块 (块名含 标高/EL/ELEV/BG 等)
2. 符号附近(右侧/上方)的数值文字 = 标高值(米)
3. 按位置与文字语境分组: 地面标高(原地面/设计地面)/楼层标高(结构标高/建筑标高)
4. 输出标高列表 + 派生参数: 挖深(设计-原地面)、层高(相邻楼层差)

标高字符: ▼(下三角=地面标高) ▲(上三角=顶标高) ▽(空心=地面) △(空心上=顶)
"""
import re

ELEV_CHARS = '▼▲▽△'
ELEV_BLOCK_KW = ['标高', 'EL', 'ELEV', 'BG', '高程', 'level']

# 数值提取: 标高通常为 ±X.XX 或 X.XX (米)
NUM_PAT = re.compile(r'[+-]?\d+\.?\d*')


def _is_elev_char(t):
    return any(c in t for c in ELEV_CHARS)


def extract_elevations(msp):
    """主入口: 提取图纸中的标高。返回 [{'value': float, 'type': '地面'|'楼层'|'未知', 'x','y','text'}]"""
    elevs = []
    texts = []
    for e in msp.query('TEXT MTEXT'):
        try:
            if e.dxftype() == 'TEXT':
                ins = e.dxf.insert
                txt = e.dxf.text or ''
            else:
                ins = e.dxf.insert
                txt = e.text or ''
            if txt.strip():
                texts.append({'x': float(ins.x), 'y': float(ins.y), 'text': txt.strip()})
        except Exception:
            continue

    # 1. 字符标高: ▼/▲ 符号
    for t in texts:
        if _is_elev_char(t['text']):
            # 数值: 符号本身带数值(如 '▼52.30') 或 同行右侧文字
            val = None
            m = NUM_PAT.search(t['text'])
            if m:
                v = float(m.group())
                if -100 <= v <= 1000:  # 标高合理范围(米)
                    val = v
            if val is None:
                # 右侧/上方最近文字取数值; 优先带小数的值(标高必有小数, 厚度等整数排除)
                best = None
                for t2 in texts:
                    if t2 is t:
                        continue
                    dx = t2['x'] - t['x']
                    dy = t2['y'] - t['y']
                    if 0 <= dx <= 10000 and abs(dy) <= 3000:
                        m2 = NUM_PAT.search(t2['text'])
                        if m2:
                            v = float(m2.group())
                            if -100 <= v <= 1000:
                                has_dec = '.' in m2.group()
                                if best is None or (has_dec and not best[2]) or \
                                   (has_dec == best[2] and dx < best[0]):
                                    best = (dx, v, has_dec)
                if best:
                    val = best[1]
            if val is not None:
                elevs.append({'value': val, 'x': t['x'], 'y': t['y'],
                              'text': t['text'], 'src': '字符'})

    # 2. 块标高: 块名含标高关键词, 属性/附近文字取数值
    for e in msp.query('INSERT'):
        try:
            name = e.dxf.name
            if not any(k in name for k in ELEV_BLOCK_KW):
                continue
            val = None
            # ATTRIB 属性找数值
            try:
                for attrib in e.attribs:
                    m = NUM_PAT.search(attrib.dxf.text or '')
                    if m:
                        val = float(m.group())
                        break
            except Exception:
                pass
            if val is None:
                # 插入点附近文字
                ix, iy = e.dxf.insert.x, e.dxf.insert.y
                best = None
                for t2 in texts:
                    dx = t2['x'] - ix
                    dy = t2['y'] - iy
                    if abs(dx) <= 5000 and abs(dy) <= 3000:
                        m2 = NUM_PAT.search(t2['text'])
                        if m2:
                            v = float(m2.group())
                            if -100 <= v <= 1000 and (best is None or abs(dx) + abs(dy) < best[0]):
                                best = (abs(dx) + abs(dy), v)
                if best:
                    val = best[1]
            if val is not None:
                elevs.append({'value': val, 'x': e.dxf.insert.x, 'y': e.dxf.insert.y,
                              'text': name, 'src': '块'})
        except Exception:
            continue

    # 3. 纯文字标高: '标高' 语境 + 数值 (如 '原地面标高 52.30')
    for t in texts:
        if ('标高' in t['text'] or '高程' in t['text']) and not _is_elev_char(t['text']):
            m = NUM_PAT.search(t['text'])
            if m:
                v = float(m.group())
                if -100 <= v <= 1000:
                    elevs.append({'value': v, 'x': t['x'], 'y': t['y'],
                                  'text': t['text'], 'src': '文字'})

    # 4. 类型分组: 符号本身文字 + 空间邻近的语境文字
    for e in elevs:
        ctx = e['text']
        # 邻近语境: 该标高 ±5000 单位内含'标高/原地面/设计'等词的文字
        for t in texts:
            if t is e or t['text'] == e['text']:
                continue
            dx = t['x'] - e['x']
            dy = t['y'] - e['y']
            if abs(dx) <= 8000 and abs(dy) <= 5000:
                if any(k in t['text'] for k in ['原地面', '自然', '现状', '设计', '道路', '路面', '楼层', '结构', '建筑']):
                    ctx = t['text']
                    break
        if '原地面' in ctx or '自然' in ctx or '现状' in ctx:
            e['type'] = '原地面'
        elif '设计' in ctx or '道路' in ctx or '路面' in ctx:
            e['type'] = '设计地面'
        elif '楼层' in ctx or '结构' in ctx or '建筑' in ctx:
            e['type'] = '楼层'
        else:
            e['type'] = '未知'
    return elevs


def derive_params(elevs):
    """从标高列表派生: 挖深/层高"""
    result = {}
    original = [e for e in elevs if e['type'] == '原地面']
    design = [e for e in elevs if e['type'] == '设计地面']
    floors = sorted([e for e in elevs if e['type'] == '楼层'], key=lambda e: e['value'])

    if original and design:
        # 挖深 = 设计 - 原地面 (正值=挖, 负值=填)
        result['挖深_m'] = round(design[0]['value'] - original[0]['value'], 2)
        result['挖深说明'] = f'设计标高{design[0]["value"]} - 原地面{original[0]["value"]}'
    if len(floors) >= 2:
        # 层高 = 相邻楼层差 (取最常见差值)
        diffs = {}
        for i in range(len(floors) - 1):
            d = round(floors[i + 1]['value'] - floors[i]['value'], 2)
            diffs[d] = diffs.get(d, 0) + 1
        if diffs:
            common = max(diffs, key=diffs.get)
            if 2.0 <= common <= 8.0:
                result['层高_m'] = common
    return result
