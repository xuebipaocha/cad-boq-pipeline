"""钢筋平法计算器 — 按22G101规则解析配筋标注"""
import re, math

# 钢筋理论重量表(kg/m)
STEEL_WEIGHT = {
    6:0.222, 6.5:0.260, 8:0.395, 10:0.617, 12:0.888, 14:1.208,
    16:1.578, 18:1.998, 20:2.466, 22:2.984, 25:3.850, 28:4.830,
    32:6.313, 36:7.990, 40:9.865,
}

def _find_diameter(text):
    """提取钢筋直径"""
    m = re.search(r'[ΦфФφ]\s*(\d+)', text)
    return int(m.group(1)) if m else None

def _find_bars(text):
    """提取钢筋根数+直径, 如 '4Φ25' → (4,25)"""
    m = re.search(r'(\d+)\s*[ΦфФφ]\s*(\d+)', text)
    return (int(m.group(1)), int(m.group(2))) if m else None

def _find_spacing(text):
    """提取钢筋间距, 如 'Φ12@150' → 150"""
    m = re.search(r'[ΦфФφ]?\d+\s*[@＠]\s*(\d+)', text)
    return int(m.group(1)) if m else None

def weight_per_m(d):
    """直径d(mm)的钢筋每米重量(kg/m)"""
    if d in STEEL_WEIGHT: return STEEL_WEIGHT[d]
    # 按公式计算: π×d²/4×7.85e-6
    return round(math.pi * d * d / 4 * 0.00785, 3)

def calc_slab_rebar(text, area_m2, slab_thick_mm=120):
    """
    计算板钢筋(t)
    平法标注: "板底筋Φ12@150" "板面筋Φ10@200" "双层双向Φ12@150"
    """
    t = text
    total_weight_kg = 0
    
    # 判断双层双向
    double_layer = '双层' in t or '双向' in t or '双层双向' in t
    layer_factor = 2 if double_layer else 1
    
    # 提取钢筋标注
    # "Φ12@150" 或 "Φ12/14@150" 或 "Φ12@150/200"
    matches = re.findall(r'[ΦфФφ](\d+)\s*[@＠]\s*(\d+)', t)
    for d_str, sp_str in matches:
        d = int(d_str)
        spacing = int(sp_str)
        w = weight_per_m(d)
        # 每m²含量(kg/m²) = (1m÷间距)×每米重×2个方向×层系数
        content_per_m2 = (1 / (spacing/1000)) * w * 2 * layer_factor
        total_weight_kg += content_per_m2 * area_m2
    
    # 如果没有间距但找到了直径(如"Φ12@150"中的12和150)
    if not matches:
        d = _find_diameter(t)
        spacing = _find_spacing(t)
        if d and spacing:
            w = weight_per_m(d)
            content_per_m2 = (1 / (spacing/1000)) * w * 2 * layer_factor
            total_weight_kg += content_per_m2 * area_m2
    
    return round(total_weight_kg / 1000, 2) if total_weight_kg > 0 else None

def calc_beam_rebar(text, beam_count=1, beam_len=6):
    """
    计算梁钢筋(t)
    平法标注: "KL1(3) 300×600 Φ8@100/200 2Φ25;4Φ22"
    """
    total_kg = 0
    
    # 提取主筋: "2Φ25" "4Φ22" "2Φ25+2Φ22"
    bar_matches = re.findall(r'(\d+)\s*[ΦфФφ]\s*(\d+)', text)
    for bar_count, d_str in bar_matches:
        n = int(bar_count)
        d = int(d_str)
        w = weight_per_m(d)
        # 每根梁的主筋重 = 根数×每米重×梁长×锚固系数(1.05)
        single = n * w * beam_len * 1.05
        total_kg += single
    
    # 提取箍筋: "Φ8@100/200"
    stirrup = re.search(r'[ΦфФφ](\d+)\s*@\s*(\d+)/(\d+)', text)
    if stirrup:
        d = int(stirrup.group(1))
        enclosed = int(stirrup.group(2))  # 加密区间距
        non_enclosed = int(stirrup.group(3))  # 非加密区间距
        w = weight_per_m(d)
        # 每根梁箍筋总长估算: (截面周长+弯钩)×间距数
        beam_b = 300  # 默认梁宽
        beam_h = 600  # 默认梁高
        dim = re.search(r'(\d+)\s*×\s*(\d+)', text)
        if dim:
            beam_b = int(dim.group(1))
            beam_h = int(dim.group(2))
        perimeter = 2 * ((beam_b-50) + (beam_h-50)) / 1000  # m, 扣保护层
        # 箍筋数量 = 加密区长度/间距 + 非加密区长度/间距
        enc_len = min(beam_len, 1.5*beam_h/1000*2)  # 两端加密
        enc_count = enc_len / (enclosed/1000)
        non_enc_len = beam_len - enc_len
        non_enc_count = non_enc_len / (non_enclosed/1000)
        stirrup_kg = (enc_count + non_enc_count) * perimeter * w * beam_count
        total_kg += stirrup_kg
    
    total_kg *= beam_count
    return round(total_kg / 1000, 2) if total_kg > 0 else None

def calc_column_rebar(text, col_count=1, col_height=3.6):
    """
    计算柱钢筋(t)
    平法标注: "KZ1 500×500 12Φ22 Φ8@100"
    """
    total_kg = 0
    
    # 主筋
    bars = re.findall(r'(\d+)\s*[ΦфФφ]\s*(\d+)', text)
    for n_str, d_str in bars:
        n = int(n_str)
        d = int(d_str)
        # 12Φ22 → 12根Φ22
        if n >= 4:  # 主筋(≥4根)
            w = weight_per_m(d)
            total_kg += n * w * col_height * col_count
    
    # 箍筋
    stirrup = re.search(r'[ΦфФφ](\d+)\s*@\s*(\d+)', text.split('主')[0] if '主' in text else text)
    if not stirrup:
        stirrup = re.findall(r'[ΦфФφ](\d+)\s*@\s*(\d+)', text)
        if stirrup:
            d = int(stirrup[-1][0])
            sp = int(stirrup[-1][1])
            w = weight_per_m(d)
            col_dim = 500
            dim_m = re.search(r'(\d+)\s*×\s*(\d+)', text)
            if dim_m: col_dim = int(dim_m.group(1))
            perimeter = 4 * (col_dim - 50) / 1000
            stirrup_count = col_height / (sp/1000)
            total_kg += stirrup_count * perimeter * w * col_count
    
    return round(total_kg / 1000, 2) if total_kg > 0 else None

def calc_rebar_total(texts, bfa, slab_thick=120, beam_count=6, col_count=4):
    """
    钢筋量综合计算入口
    返回: {'total_t':总吨数, 'note':计算说明, 'details':明细}
    """
    tc = ' '.join(texts)
    items = {'板筋':0, '梁筋':0, '柱筋':0, '箍筋':0, '其他':0}
    
    # 配筋率法（最可靠）
    for t in texts:
        m = re.search(r'配筋率[：:]?\s*(\d+\.?\d*)\s*%', t)
        if m:
            ratio = float(m.group(1)) / 100
            con_vol = bfa * slab_thick / 1000  # 板混凝土体积
            # 配筋率×混凝土体积×钢筋密度
            steel_t = ratio * con_vol * 7.85
            return round(steel_t, 2), f'按配筋率{ratio*100}%计算', None
    
    # 平法标注解析
    for t in texts:
        # 板筋
        if any(k in t for k in ['板筋','板底','板上','双层','双向','Φ']):
            s = calc_slab_rebar(t, bfa, slab_thick)
            if s: items['板筋'] += s
        # 梁筋
        if '梁' in t and ('Φ' in t or '筋' in t or '@' in t):
            s = calc_beam_rebar(t, beam_count)
            if s: items['梁筋'] += s
        # 柱筋
        if '柱' in t and ('Φ' in t or '筋' in t):
            s = calc_column_rebar(t, col_count)
            if s: items['柱筋'] += s
    
    total = sum(items.values())
    if total > 0:
        details = [f'{k}={v}t' for k, v in items.items() if v > 0]
        return round(total, 2), '平法标注解析', ' | '.join(details)
    
    return None, '未找到配筋标注', None

if __name__ == '__main__':
    tests = [
        (['板筋Φ12@150双层双向','梁KL1 300×600 Φ8@100/200 2Φ25;4Φ22','柱KZ1 500×500 12Φ22 Φ8@100'], 1500),
        (['配筋率:0.8%'], 1500),
        (['Φ12@150双层双向'], 1500),
    ]
    for texts, bfa in tests:
        total, note, detail = calc_rebar_total(texts, bfa)
        print(f'\n配筋标注: {texts[0][:30]}...')
        print(f'  钢筋量: {total}t ({note})')
        if detail: print(f'  明细: {detail}')
