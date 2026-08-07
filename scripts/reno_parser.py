# -*- coding: utf-8 -*-
"""大修/改造通用文字解析器 — v5.5 R7

大修图纸的施工内容主要存在于文字说明(图形多为原状/保留)。本模块提取
大修图纸的共性表达模式(不针对任何单张图纸):

  1. 拆除项:   'XX立面外墙涂料层整体拆除' → {部位, 对象, 范围, 数量}
               'XX立面外墙涂料层局部拆除, 维修面积约XX'
  2. 数量参数: '雨水斗个数约X个' / '雨水管总长度约Xm' / '总维修面积约Xm2'
  3. 做法层:   '5厚聚合物抗裂砂浆' / '30厚1:2.5聚合物水泥砂浆' / '仿石涂料两道'
  4. 拆除→重做对: '拆除后重新铺设混凝土地面' / '拆除并更换UPVC雨水管'

输出:
  {
    '拆除项': [{部位, 对象, 范围(整体/局部), 面积_m2?, 数量?, 原句}],
    '数量参数': {雨水管_根: X, 雨水管_长度_m: X, 维修面积_m2: X, ...},
    '做法层': [{厚度_mm?, 材料, 部位?, 原句}],
    '重做项': [{动作(重新铺设/更换/新做), 对象, 材料, 原句}],
  }
"""
import re

# 部位词(建筑常见部位)
PART_KEYWORDS = ['外墙', '内墙', '屋面', '楼面', '地面', '顶棚', '天棚', '楼梯',
                 '踏步', '台阶', '门窗', '栏杆', '雨水管', '雨水斗', '设备间',
                 '卫生间', '走廊', '设备间屋面', '幕墙', '散水', '坡道', '勒脚',
                 '落水管', '落水口', '泛水']

# 拆除范围词
SCOPE_KEYWORDS = ['整体', '全部', '局部', '根据破损位置', '破损', '部分']

# 拆除对象词
OBJ_KEYWORDS = ['涂料层', '面层', '防水层', '保温层', '抹灰层', '饰面', '饰面层',
                '木屋面', '屋面', '面砖', '石材', '混凝土面层', '找平层', '保护层',
                '门窗', '栏杆', '雨水管', '雨水斗', '管道', '吊顶', '隔墙', '墙']

# 做法材料词
MATERIAL_KEYWORDS = ['聚合物抗裂砂浆', '抗裂砂浆', '水泥砂浆', '混合砂浆', '砂浆',
                     '高延性混凝土', '混凝土', '网格布', '耐碱玻纤网格布', '玻纤网格布',
                     '聚氨酯防水涂料', '防水涂料', '仿石涂料', '真石漆', '涂料',
                     '乳胶漆', '腻子', '金属夹芯复合板', '夹芯板', '金属板', '防水卷材',
                     '保温板', '岩棉', '挤塑板', '界面剂', '底漆', '面漆', '罩光清漆',
                     '密封胶', 'UPVC', '大理石', '花岗岩', '地砖', '地板']

# 动作词(拆除→重做)
ACTION_KEYWORDS = ['重新铺设', '重新铺贴', '更换', '新做', '重做', '重新浇筑',
                   '重新抹', '重新喷涂', '改铺', '换成']


def _find_part(t):
    for p in PART_KEYWORDS:
        if p in t:
            return p
    return ''


def parse_demolitions(texts):
    """拆除项解析: '部位+对象+范围+拆除' 模式"""
    items = []
    for t in texts or []:
        if '拆除' not in t:
            continue
        # 措施性文字排除: '进行相关拆除时'/'拆除时应对' 是施工要求不是拆除项
        if re.match(r'^\s*(\d+\.)?\s*进行?相关?拆除|^\s*(\d+\.)?\s*拆除时|^\s*(\d+\.)?\s*拆除后应', t):
            continue
        # 拆除对象: 对象词出现在 '拆除' 前 15 字内
        obj = ''
        for kw in OBJ_KEYWORDS:
            if kw in t:
                obj = kw
                break
        part = _find_part(t)
        # 部位与对象去重: '雨水管及雨水斗拆除' → 部位=雨水管, 对象=雨水斗
        if part and obj and (part in obj or obj in part):
            if len(part) >= len(obj):
                obj = ''
            else:
                part = ''
        scope = '整体' if any(k in t for k in ('整体', '全部')) else (
            '局部' if any(k in t for k in ('局部', '破损', '部分')) else '')
        # 数量: 同句中的 '约X个'/'约Xm'/'面积约X'
        qty = None
        m = re.search(r'(?:约|共|总)?(\d+(?:\.\d+)?)\s*(?:个|根|处|只|樘|m2|平方米|㎡|m)\b', t)
        if m:
            v = float(m.group(1))
            if 1 <= v <= 1e6:
                qty = v
        items.append({
            '部位': part, '对象': obj, '范围': scope,
            '数量': qty, '原句': t[:80],
        })
    return items


def parse_quantities(texts):
    """数量参数: '雨水斗个数约X个' / '雨水管总长度约Xm' / '维修面积约Xm2'
    v5.6: 数字三要素校验(单位+语境+量级) — 缺单位/量级存疑 → 待提取, 不猜值"""
    from quality import check_number, SRC_TEXT, SRC_PENDING
    out = {}
    for t in texts or []:
        # 对象+数量: 雨水斗X个 / X根 / Xm / Xm2
        for obj in ('雨水斗', '雨水管', '落水管', '门窗', '栏杆', '踏步', '台阶'):
            m = re.search(obj + r'[^0-9]{0,10}?(\d+(?:\.\d+)?)\s*(个|根|处|只|樘|m|米|m2|平方米|㎡)', t)
            if m:
                v = float(m.group(1))
                unit = m.group(2)
                ok, reason = check_number(v, unit, t)
                if ok:
                    key = f'{obj}_{unit}'
                    out[key] = max(out.get(key, 0), v)
                else:
                    # 量级存疑 → 记入待提取(不猜值)
                    out[f'{obj}_待提取'] = reason
        # 维修面积/总面积 (v5.6: 单位缺失 → 待提取, 不再直接采用)
        m = re.search(r'(?:维修面积|拆除面积|总面积|外墙面面积)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(m2|平方米|㎡)?', t)
        if m:
            v = float(m.group(1))
            unit = m.group(2) or ''
            ok, reason = check_number(v, unit, t)
            if ok:
                out['维修面积_m2'] = max(out.get('维修面积_m2', 0), v)
            else:
                out['维修面积_待提取'] = reason
    return out


def parse_layers(texts):
    """做法层: '5厚聚合物抗裂砂浆' / '30厚1:2.5聚合物水泥砂浆' / '仿石涂料两道'"""
    layers = []
    tc = ' '.join(texts or [])
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(?:mm)?厚\s*([^，。；]{2,25}?)(?=[，。；]|$)', tc):
        thick = float(m.group(1))
        mat = m.group(2).strip()
        if not mat:
            continue
        # 截断到材料词结尾
        cut = 0
        for kw in MATERIAL_KEYWORDS:
            idx = mat.find(kw)
            if idx >= 0:
                cut = max(cut, idx + len(kw))
        if cut > 0:
            mat = mat[:cut]
        if 3 <= thick <= 200:
            layers.append({'厚度_mm': thick, '材料': mat, '部位': '', '原句': m.group(0)[:50]})
    # 两道/一遍: '仿石涂料两道' / '腻子两道' (材料词后紧邻, 不含数字/单位)
    for m in re.finditer(r'([^，。；0-9]{1,12}?(?:涂料|腻子|漆|防水)[^，。；]{0,6}?)(两|二|三|一)(道|遍|层)', tc):
        mat = m.group(1).strip()
        if not mat:
            continue
        # 材料词截断到关键词结尾(去掉 '16.0m 外墙' 这类前缀噪声)
        for kw in MATERIAL_KEYWORDS:
            idx = mat.find(kw)
            if idx >= 0:
                mat = mat[idx:]
                break
        n = {'两': 2, '二': 2, '三': 3, '一': 1}.get(m.group(2), 1)
        layers.append({'厚度_mm': None, '材料': mat, '遍数': n, '原句': m.group(0)[:50]})
    # 独立材料词: '聚氨酯防水涂料' / '仿石涂料' 单独出现(无厚度无遍数)
    seen_mats = {l['材料'] for l in layers}
    for kw in MATERIAL_KEYWORDS:
        if kw in tc and kw not in seen_mats:
            # 只在材料词确实作为做法出现时加(排除 '防水设计'/'涂料厂' 等语境)
            for m in re.finditer(re.escape(kw), tc):
                start = max(0, m.start() - 6)
                ctx = tc[start:m.end() + 6]
                if any(w in ctx for w in ('做法', '采用', '为', '用', '施工', '面层', '刷', '涂', '喷', '铺', '压', '抹')):
                    layers.append({'厚度_mm': None, '材料': kw, '原句': kw})
                    break
    return layers


def parse_redo_actions(texts):
    """拆除→重做: '拆除后重新铺设混凝土地面' / '拆除并更换UPVC雨水管'"""
    items = []
    for t in texts or []:
        if '拆除' not in t:
            continue
        for act in ACTION_KEYWORDS:
            idx = t.find(act)
            if idx < 0:
                continue
            # 动作后的对象/材料
            tail = t[idx + len(act):idx + len(act) + 20]
            items.append({'动作': act, '对象': tail[:15], '原句': t[:70]})
            break
    return items


def parse_renovation_text(texts):
    """大修文字全量解析入口"""
    return {
        '拆除项': parse_demolitions(texts),
        '数量参数': parse_quantities(texts),
        '做法层': parse_layers(texts),
        '重做项': parse_redo_actions(texts),
    }


# ---- v6.5: 设计范围解析(大修/翻新项目) ----

# 楼层词
FLOOR_KEYWORDS = ['一层', '二层', '三层', '四层', '五层', '六层', '顶层', '首层',
                  '底层', '标准层', '屋面层', '夹层', '全部楼层', '各层']

# 动作词(设计内容条目)
DESIGN_ACTION_KEYWORDS = ['拆除', '更换', '重做', '新做', '翻新', '修缮', '加固',
                          '涂刷', '重新', '修复', '增设', '外运', '安装', '贴', '铺']

# 分区词(设计内容范围)
ZONE_KEYWORDS = ['东侧', '西侧', '南侧', '北侧', '山墙', '檐口', '女儿墙', '散水',
                 '入口', '雨篷', '外檐', '墙裙', '勒脚', '1-8', '8-15', '15-21',
                 '1-21', 'A-C', 'C-E', 'E-G', 'A-G', '①-⑧', '⑧-⑮', '⑮-㉑',
                 '①②', '②③', '③④', '④⑤', '⑤⑥', '⑥⑦', '⑦⑧']


def parse_design_scope(texts):
    """设计内容逐条解析 → [{部位, 动作, 对象, 楼层, 分区, 材料, 原文}]
    用于大修项目: 设计说明/施工说明中的设计内容条目结构化为施工范围。"""
    items = []
    for t in texts or []:
        t = str(t).strip()
        if not t or len(t) < 4:
            continue
        # 跳过纯措施/规范引用行
        if re.match(r'^(本工程|本图|本设计|图纸|说明|注|备注|做法见|详见|参见)', t):
            continue
        if not any(k in t for k in DESIGN_ACTION_KEYWORDS):
            continue
        parts = [p for p in PART_KEYWORDS if p in t]
        objs = [o for o in OBJ_KEYWORDS if o in t]
        mats = [m for m in MATERIAL_KEYWORDS if m in t]
        floors = [f for f in FLOOR_KEYWORDS if f in t]
        zones = [z for z in ZONE_KEYWORDS if z in t]
        if not parts and not objs:
            continue  # 无部位无对象 → 非设计内容条目
        items.append({
            '部位': parts[0] if parts else '',
            '对象': objs[0] if objs else '',
            '材料': mats[0] if mats else '',
            '楼层': floors[0] if floors else '',
            '分区': zones[0] if zones else '',
            '动作': t[:40],
            '原文': t,
        })
    return items


def build_scope_mask(design_scope, calc_items):
    """范围掩码: 算量分项是否在设计范围内。
    分项名称含范围部位词 → 范围内(1); 否则范围外(0)。
    返回 {'范围内': [...], '范围外': [...], 'mask': {分项名: 0/1}}"""
    scope_parts = set()
    for it in design_scope or []:
        p = it.get('部位') or ''
        o = it.get('对象') or ''
        if p:
            scope_parts.add(p)
        if o and o != '墙':
            scope_parts.add(o)
    mask = {}
    in_scope, out_scope = [], []
    for it in calc_items or []:
        name = it.get('分项名称') or it.get('name') or ''
        hit = any(p in name for p in scope_parts if p)
        mask[name] = 1 if hit else 0
        (in_scope if hit else out_scope).append(name)
    return {'范围内': in_scope, '范围外': out_scope, 'mask': mask}
