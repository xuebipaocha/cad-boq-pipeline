"""
钢材理论重量计算器 + CAD规格自动提取
"""
import math, re

DENSITY = 7850

def h_beam(h, b, tw, tf):
    area = (h - 2*tf) * tw + 2 * b * tf
    wt = area / 1e6 * DENSITY
    sa = (2*b + 2*(h-tf*2) + 4*tf - 2*tw) / 1000
    return round(wt, 2), round(sa, 3)

def pipe(D, t):
    area = math.pi * (D - t) * t
    wt = area / 1e6 * DENSITY
    sa = math.pi * D / 1000
    return round(wt, 2), round(sa, 3)

def square_tube(a, b, t):
    area = 2 * (a + b - 2*t) * t
    wt = area / 1e6 * DENSITY
    sa = 2 * (a + b) / 1000
    return round(wt, 2), round(sa, 3)

def round_bar(d):
    area = math.pi * d * d / 4
    wt = area / 1e6 * DENSITY
    sa = math.pi * d / 1000
    return round(wt, 2), round(sa, 3)

def plate(w, t):
    """钢板/扁钢: -120×20 → 宽120厚20
    v6.6: 原实现 plate(t) 把宽度当厚度算(t/1000×7850), -120×20 算成 942kg/m
    真实 18.84kg/m — 重量错 50 倍; 表面积固定 2.0 → 2×(宽+厚)/1000 (错 7 倍)
    """
    wt = w * t / 1e6 * DENSITY
    sa = 2 * (w + t) / 1000
    return round(wt, 2), round(sa, 3)

def angle_steel(b, t, b2=None):
    """角钢: 等边 L75×6 或 不等边 L75×50×6
    面积 = (b - t)*t + (b2 - t)*t  (两肢截面积)
    """
    b2 = b2 if b2 else b
    area = (b - t) * t + (b2 - t) * t
    wt = area / 1e6 * DENSITY
    sa = (b + b2) / 1000
    return round(wt, 2), round(sa, 3)

def channel_steel(h, b, tw, tf):
    """槽钢: 面积 = 腹板(h-2tf)×tw + 2×翼缘 b×tf - 圆角忽略
    C250×80×7×11 → 248×7 + 2×80×11 = 1736 + 1760 = 3496 mm²
    """
    area = (h - 2 * tf) * tw + 2 * b * tf
    wt = area / 1e6 * DENSITY
    sa = (2 * b + h) / 1000
    return round(wt, 2), round(sa, 3)


FUNCS = {
    'H': ('H型钢', h_beam, 4), '工': ('H型钢', h_beam, 4),
    'C': ('槽钢', channel_steel, 4),
    '[': ('槽钢', channel_steel, 4),
    'O': ('钢管', pipe, 2), 'Φ': ('钢管', pipe, 2),
    '□': ('方管', square_tube, 3),
    '●': ('圆钢', round_bar, 1),
    '—': ('钢板', plate, 2),
    'L': ('角钢', angle_steel, 2),
}

# 从CAD文字标注中提取钢结构规格
# "H300×200×8×12" → ('H', [300,200,8,12])
# "HW200×200×8×12" → ('H', [200,200,8,12])
# "Φ219×6" → ('O', [219,6])
# "□200×200×8" → ('□', [200,200,8])
def parse_steel_spec(text):
    patterns = [
        (r'[Hh][WwMmNnBb]?\d+[\s×Xx*]\d+[\s×Xx*]\d+[\s×Xx*]\d+', 'H'),
        (r'Φ\d+[\s×Xx*]\d+', 'O'),
        (r'□\d+[\s×Xx*]\d+[\s×Xx*]\d+', '□'),
        (r'●\d+', '●'),
        # v6.6: 扁钢 '-130x14' 必须 '负号+数字+×+数字' — 原 '[—\-]\d+' 只吃负号+数字,
        # 规范编号 'GB 50009-2012' 的 '-2012' 被当成扁钢构件(设计说明整段变钢构件)
        (r'[—\-]\d+[\s×Xx*]\d+', '—'),
        (r'[Ll]\d+[\s×Xx*]\d+', 'L'),
        (r'[Cc]\d+[\s×Xx*]\d+[\s×Xx*]\d+[\s×Xx*]\d+', 'C'),
    ]
    for pattern, stype in patterns:
        m = re.search(pattern, text)
        if m:
            spec = m.group()
            nums = [int(s) for s in re.findall(r'\d+', spec)]
            # v6.6: 参数合理性校验 — 角钢 L50x52(52>50/2)/扁钢 -100x82(82>100/2)
            # 是聚类拼接污染, 解析出负重量; 厚度>边宽一半无物理意义 → 跳过
            if stype in ('L', '—') and len(nums) >= 2:
                if nums[1] > nums[0] / 2:
                    continue
            if stype == 'H' and len(nums) >= 4:
                if 'W' in spec: return ('H', nums[:4])  # HW宽翼缘
                if 'M' in spec: return ('H', nums[:4])  # HM中翼缘
                if 'N' in spec: return ('H', nums[:4])  # HN窄翼缘
                if len(nums) == 4: return ('H', nums[:4])
            elif stype == 'O' and len(nums) >= 2: return ('O', nums[:2])
            elif stype == '□' and len(nums) >= 3: return ('□', nums[:3])
            elif stype == '●' and len(nums) >= 1: return ('●', nums[:1])
            elif stype == '—' and len(nums) >= 2:
                # v6.6: 扁钢必须 [宽, 厚] — 原只传宽度(nums[:1]), 厚度丢失
                return ('—', nums[:2])
            elif stype == 'L' and len(nums) >= 2: return ('L', nums[:2])
            elif stype == 'C' and len(nums) >= 4: return ('C', nums[:4])
    return None

def extract_steel_from_texts(texts):
    """从施工说明/CAD文字中提取钢构件清单
    v6.6: 过滤设计说明噪声 — 规范引用/设计参数/图签行不是钢构件
    (真实图纸实测: '《建筑结构荷载规范》GB 50009-2012'/'耐火等级二级…' 被当构件)
    """
    members = []
    for t in texts:
        # v6.6: 规范引用/设计说明/图签行直接跳过
        if not t or len(t) > 40:
            continue
        if re.search(r'GB\s?\d|JGJ|JG/T|GB/T|规范|规程|标准|图集|耐火等级|抗震等级|抗震设防|特征周期|基础型式|DRAWING|图\s*号', t):
            continue
        spec = parse_steel_spec(t)
        if spec:
            stype, params = spec
            # 估算长度（默认6m）
            length = 6.0
            len_src = '估算'
            for kw, val in [('长',None),('L=',None),('l=',None)]:
                m = re.search(rf'{kw}(\d+\.?\d*)', t)
                if m:
                    length = float(m.group(1))
                    len_src = '文字标注'
                    break
            wt_info = calc_weight_main(stype, params)
            if wt_info:
                # v6.6: 名称截断 20 字符(与 v6.5 一致) — 恰好截掉 '钢柱/钢梁/檩条'
                # 后缀, 分项名 'H400×200×8×13 L=9m 钢制作安装' 与人工清单一致;
                # 输入改为原始 TEXT 后无需拼接编号(聚类串才有编号+规格粘连)
                members.append({'名称': t[:20], '截面类型': stype, '截面参数': params,
                                '长度_m': length, '长度来源': len_src})
    return members

# 从CAD标注中提取钢构件规格（替代方法）
def extract_steel_from_dims(dims, scale=1.0):
    """从标注尺寸推断钢构件（H型钢）"""
    members = []
    # 按位置分组标注
    from collections import defaultdict
    by_layer = defaultdict(list)
    for d in dims:
        by_layer[d['layer']].append(d)
    
    for layer, dims_list in by_layer.items():
        if any(k in layer for k in ['钢','柱','梁','steel']):
            vals = sorted([d['measurement'] for d in dims_list], reverse=True)
            if len(vals) >= 2:
                h = int(vals[0])
                b = int(vals[1]) if len(vals) > 1 else h//2
                members.append({'名称':f'{layer}钢构件','截面类型':'H','截面参数':(h,b,8,12),'长度_m':6.0})
    return members

def calc_weight_main(stype, params):
    """通用计算入口"""
    for key, (name, fn, nargs) in FUNCS.items():
        if stype == key and fn:
            if len(params) >= nargs:
                wt, sa = fn(*params[:nargs])
                return {'type': name, 'weight_kgm': wt, 'surface_m2m': sa}
    return None

COMMON_H = {
    'HW100×100':(100,100,6,8),'HW125×125':(125,125,6.5,9),'HW150×150':(150,150,7,10),'HW200×200':(200,200,8,12),
    'HM194×150':(194,150,6,9),'HM340×250':(340,250,9,14),'HM390×300':(390,300,10,16),
    'HN400×200':(400,200,8,13),'HN500×200':(500,200,10,16),'HN700×300':(700,300,13,24),
}

def lookup(name):
    if name in COMMON_H:
        wt, sa = h_beam(*COMMON_H[name])
        return {'type':'H型钢','spec':name,'weight_kgm':wt,'surface_m2m':sa}
    p = parse_steel_spec(name)
    if p:
        wt = calc_weight_main(*p)
        if wt: return {'type':wt['type'],'spec':name,'weight_kgm':wt['weight_kgm'],'surface_m2m':wt['surface_m2m']}
    return None

if __name__ == '__main__':
    texts = ['H300×200×8×12 L=9m','HW200×200×8×12','Φ219×6 钢管','□200×200×8','不锈钢钢板 δ=10mm']
    members = extract_steel_from_texts(texts)
    for m in members:
        wt = calc_weight_main(m['截面类型'], m['截面参数'])
        if wt:
            total_wt = wt['weight_kgm'] * m['长度_m'] / 1000
            print(f'{m["名称"]:20s} → {wt["type"]} {m["截面参数"]}: {wt["weight_kgm"]}kg/m × {m["长度_m"]}m = {total_wt:.3f}t')
