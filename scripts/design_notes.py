# -*- coding: utf-8 -*-
"""设计说明专项解析 — v6.2

设计说明(施工说明/图纸说明)是造价信息密度最高的位置:
材料规格型号 / 施工范围 / 材料做法表(文字版) / 门窗表 / 工程概况 都在其中。

功能:
1. 识别设计说明文字块(标题含 设计说明/施工说明/图纸说明/说明)
2. 材料规格型号提取: 800×800 / 300×600 / DN25 / C30 / 5+12+5 / 12mm 等
3. 施工范围提取: 本工程包括/施工范围/本次改造/施工内容 等
4. 文字做法结构化: "1.界面剂 2.自流平 3.实木复合地板" → [{层次,做法}]
5. 工程概况: 建筑面积/层数/檐高/结构类型/防水等级 等

用法:
  from design_notes import parse_design_notes
  notes = parse_design_notes(texts)   # texts: 施工说明原始行列表
"""
import re

sys_stdout_ok = True

# ── 设计说明标题识别 ──
NOTE_TITLES = ['设计说明', '施工说明', '图纸说明', '设计总说明', '工程说明', '说明', '设计依据',
               '施工范围', '工程概况', '本工程', '材料规格', '做法', '房间做法']
# ── 材料规格模式 ──
SPEC_PATTERNS = [
    (r'(?<!\d)(\d{2,4})\s*[×xX*]\s*(\d{2,4})(?!\d)', '尺寸'),          # 800×800 / 300*600(防粘连)    (r'(DN\d{2,3})', '管径'),                              # DN25 / DN100
    (r'\b(C\d{2})\b', '混凝土标号'),                        # C20 / C30
    (r'\b(M\d+(?:\.\d)?)\b', '砂浆标号'),                   # M5 / M10
    (r'(\d+\.?\d*)\s*mm', '厚度'),                         # 12mm / 100mm
    (r'(\d+)\s*\+\s*(\d+)\s*\+\s*(\d+)', '中空玻璃'),       # 5+12+5
    (r'(HRB\d{3}|HPB\d{3})', '钢筋牌号'),                  # HRB400 / HPB300
    (r'(φ|Φ)\s*(\d+)', '钢筋直径'),                        # φ12
    (r'(YJV|YJY|BV|BYJ|VV)[-\s]?(\d+(?:\.\d)?(?:[×xX*]\d+(?:\.\d)?)?\+?[0-9×xX*]*)', '线缆型号'),  # YJV22-4*95 / BV-2.5
    (r'([A-Z]{2,4}\s*\d{2,4}(?:\.\d)?)', '材料牌号'),       # DS M20 / DP M10
]
# ── 施工范围关键词 ──
SCOPE_KEYWORDS = ['施工范围', '本工程', '施工内容', '本次改造', '本次施工', '工程内容',
                  '承包范围', '包含', '包括', '不含']
# ── 工程内容/边界关键词(v6.3 B4) ──
INCLUDE_KW = ['包含', '包括', '含', '主要内容', '施工内容']
EXCLUDE_KW = ['不含', '不包括', '除外', '不包含', '本次不']
NATURE_KW = {  # 工程性质 → 设计意图信号
    '大修': ['大修', '改造', '翻新', '修缮', '维修', '拆除重建'],
    '新建': ['新建', '新建工程', '本工程为新建'],
    '扩建': ['扩建', '加建', '接建'],
    '装饰': ['装饰装修', '精装修', '室内装修', '二次装修'],
}
PURPOSE_KW = {  # 建筑用途 → 推断设计标准
    '住宅': ['住宅', '公寓', '宿舍', '商品房'],
    '办公': ['办公', '写字楼', '办公楼'],
    '商业': ['商业', '商铺', '商场', '购物'],
    '工业': ['厂房', '车间', '仓库', '工业'],
    '医疗': ['医院', '门诊', '医疗'],
    '教育': ['学校', '教学楼', '教室', '幼儿园'],
    '公共': ['体育馆', '展览', '剧院', '图书馆'],
}
# ── 工程概况关键词 ──
PROFILE_KEYWORDS = ['建筑面积', '层数', '檐高', '结构类型', '抗震', '防水等级', '耐火等级',
                    '地上', '地下', '标准层', '建筑高度', '工程概况']


def parse_design_notes(texts):
    """设计说明专项解析入口。

    texts: 施工说明原始行列表(step1 的 pid['施工说明'] 或原始行)。
    返回:
      {
        '检测到设计说明': bool,
        '材料规格': [{'规格': '800×800', '类型': '尺寸', '上下文': '...'}],
        '施工范围': str,
        '做法层次': [{'名称': '客厅地面', '层次': ['1.界面剂', '2.自流平', '3.实木复合地板']}],
        '工程概况': {'建筑面积': '', '层数': '', '檐高': '', '结构类型': '', '其他': []},
        '原文': [行...]
      }
    """
    out = {
        '检测到设计说明': False,
        '材料规格': [],
        '施工范围': '',
        '做法层次': [],
        '工程概况': {'建筑面积': '', '层数': '', '檐高': '', '结构类型': '', '其他': []},
        '工程内容': {'性质': '', '用途': '', '含项': [], '不含项': []},  # v6.3 B4
        '原文': [],
    }
    if not texts:
        return out

    joined = '\n'.join(str(t) for t in texts)
    out['原文'] = list(texts)

    # 1. 检测设计说明
    if any(t in joined for t in NOTE_TITLES):
        out['检测到设计说明'] = True

    # 2. 材料规格提取(去重, 保留上下文)
    seen_specs = set()
    for line in texts:
        line = str(line)
        for pat, stype in SPEC_PATTERNS:
            for m in re.finditer(pat, line):
                spec = m.group(0)
                # 尺寸类数值范围校验(100~3000, 防粘连误配如 8300×300)
                if stype == '尺寸':
                    nums = re.findall(r'\d+', spec)
                    if not nums or any(not (100 <= int(n) <= 3000) for n in nums):
                        continue
                if spec in seen_specs:
                    continue
                seen_specs.add(spec)
                # 上下文: 规格前 12 字符
                ctx = line[max(0, m.start() - 12):m.start()].strip()
                out['材料规格'].append({'规格': spec, '类型': stype, '上下文': ctx})

    # 3. 施工范围
    scope_parts = []
    for line in texts:
        s = str(line)
        if any(k in s for k in SCOPE_KEYWORDS):
            # 截取关键词后的内容(限 60 字)
            for kw in SCOPE_KEYWORDS:
                idx = s.find(kw)
                if idx >= 0:
                    seg = s[idx:idx + 60].strip()
                    if seg and seg not in scope_parts:
                        scope_parts.append(seg)
                    break
    out['施工范围'] = '；'.join(scope_parts[:5])

    # v6.3 B4: 工程内容结构化 — 性质/用途/含项/不含项(设计意图边界)
    eng = out['工程内容']
    # 性质
    for nature, kws in NATURE_KW.items():
        if any(k in joined for k in kws):
            eng['性质'] = nature
            break
    # 用途
    for purpose, kws in PURPOSE_KW.items():
        if any(k in joined for k in kws):
            eng['用途'] = purpose
            break
    # 含/不含项(逐行扫描关键词后的分句)
    for line in texts:
        s = str(line)
        for kw in EXCLUDE_KW:
            idx = s.find(kw)
            while idx >= 0:
                seg = s[idx + len(kw):idx + len(kw) + 40].strip().rstrip('。；;，,')
                if seg and seg not in eng['不含项']:
                    eng['不含项'].append(seg)
                idx = s.find(kw, idx + len(kw))
        for kw in INCLUDE_KW:
            idx = s.find(kw)
            while idx >= 0:
                # 排除 "不含/不包括/不包含" 中的"含" (前缀否定)
                prefix = s[max(0, idx - 1):idx]
                if prefix in ('不',):
                    idx = s.find(kw, idx + len(kw))
                    continue
                seg = s[idx + len(kw):idx + len(kw) + 40].strip().rstrip('。；;，,')
                if seg and seg not in eng['含项'] and not any(k in seg for k in EXCLUDE_KW):
                    eng['含项'].append(seg)
                idx = s.find(kw, idx + len(kw))

    # 4. 做法层次结构化: "位置 1.xxx 2.xxx 3.xxx" 或 "1.界面剂 2.自流平 3.实木复合地板"
    layer_pattern = re.compile(r'([\u4e00-\u9fa5A-Za-z0-9]{2,12}?)((?:\d+\.[^0-9]+){2,})')
    for line in texts:
        s = str(line)
        for m in layer_pattern.finditer(s):
            name = m.group(1).strip()
            layers = re.findall(r'\d+\.([^0-9]+)', m.group(2))
            layers = [l.strip() for l in layers if l.strip()]
            if len(layers) >= 2 and name:
                # 名称需含部位词(地面/墙面/天棚/卫生间/厨房 等)或为首个词
                if any(k in name for k in ['地面', '墙面', '天棚', '卫生间', '厨房', '客厅',
                                           '卧室', '顶棚', '楼面', '屋面', '踢脚', '门套']):
                    out['做法层次'].append({'名称': name, '层次': layers})

    # 5. 工程概况
    profile = out['工程概况']
    for key, pat in [('建筑面积', r'建筑面积[为:：]?\s*([\d.]+)\s*m[²2]'),
                     ('层数', r'([\d.]+)\s*层'),
                     ('檐高', r'檐高[为:：]?\s*([\d.]+)\s*m'),
                     ('结构类型', r'结构类型[为:：]?\s*([\u4e00-\u9fa5]{2,10})')]:
        m = re.search(pat, joined)
        if m:
            profile[key] = m.group(1)
    # 其他概况(防水/抗震等级)
    for kw in ['防水等级', '抗震', '耐火等级', '建筑高度']:
        for m in re.finditer(kw + r'[为:：]?\s*([\u4e00-\u9fa5A-Za-z0-9]{1,10})', joined):
            v = m.group(1).strip()
            if v and v not in profile['其他']:
                profile['其他'].append(f'{kw}:{v}')

    # 去重规格(按 类型+规格)
    seen2 = set()
    uniq = []
    for s in out['材料规格']:
        k = (s['类型'], s['规格'])
        if k not in seen2:
            seen2.add(k)
            uniq.append(s)
    out['材料规格'] = uniq[:30]
    return out


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    import step1_recognize as s1
    import os
    out = os.path.join(os.environ.get('TEMP', '.'), '_dn_test')
    os.makedirs(out, exist_ok=True)
    pid = s1.run('../benchmarks/cases/精装规范/drawings/精装_住宅规范.dxf', out)
    r = parse_design_notes(pid.get('施工说明', []))
    print(f"检测到设计说明: {r['检测到设计说明']}")
    print(f"材料规格 ({len(r['材料规格'])}):")
    for s in r['材料规格'][:20]:
        print(f"  [{s['类型']}] {s['规格']}  (上下文: {s['上下文'][-10:]})")
    print(f"施工范围: {r['施工范围'][:80]}")
    print(f"做法层次 ({len(r['做法层次'])}):")
    for l in r['做法层次'][:5]:
        print(f"  {l['名称']}: {' → '.join(l['层次'][:4])}")
    print(f"工程概况: {r['工程概况']}")
