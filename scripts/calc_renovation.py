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
    """外墙面积: 立面图 DIMENSION 提取(水平×垂直)。
    找立面图标注: 水平标注(宽)×垂直标注(高) → 面积。
    无 → (None, SRC_PENDING, '无立面图尺寸标注')
    """
    dims = recog.get('标注关联', []) or []
    if not dims:
        dims = recog.get('标注尺寸', []) or []
    # 水平标注(宽)与垂直标注(高)各取最大
    widths, heights = [], []
    for d in dims:
        if isinstance(d, dict):
            t = d.get('type', '')
            v = d.get('value') or d.get('target_len') or 0
        else:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        if t in ('horizontal', '水平'):
            widths.append(v)
        elif t in ('vertical', '垂直'):
            heights.append(v)
    if widths and heights:
        w = max(widths) / 1000.0  # mm→m
        h = max(heights) / 1000.0
        area = round(w * h, 2)
        return area, SRC_MEASURED, f'立面图标注 {w}×{h}'
    return None, SRC_PENDING, '无立面图尺寸标注'


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
        r.append(_q('屋面翻新', 'm²', 0, '待提取: 无屋面面积证据', SRC_PENDING))

    # ── 6. 外立面脚手架(依赖外墙面积) ──
    if facade:
        r.append(_q('外立面脚手架', 'm²', facade, f'{facade_note}', facade_src))
    else:
        r.append(_q('外立面脚手架', 'm²', 0, '待提取: 无外墙面积证据', SRC_PENDING))

    return r
