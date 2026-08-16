"""钢结构算量"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from steel_weight import calc_weight_main as calc_weight, lookup, h_beam, pipe, plate

def calc(data):
    """钢结构算量：根据识图结果中的钢结构信息计算"""
    from quality import SRC_MEASURED, SRC_TEXT, SRC_ESTIMATED, SRC_PENDING
    r = []
    steel_info = data.get('钢结构', {})
    
    # 1. 从识图结果读取构件
    members = steel_info.get('构件', [])
    if members:
        for m in members:
            name = m.get('名称', '钢构件')
            section_type = m.get('截面类型', 'H')  # H/C/L/O/□
            params = m.get('截面参数', [])  # [h,b,tw,tf] 或 [D,t] 等
            length = m.get('长度_m', 0)
            # v6.6: 长度来源 — 图纸标注 L=9m → 文字标注; 默认 6.0m → 估算
            # (估算量不得冒充实测, 由 step4 卡口分流待核实清单)
            len_src = m.get('长度来源', '估算')
            src = SRC_TEXT if len_src == '文字标注' else SRC_ESTIMATED
            calc_tail = '' if len_src == '文字标注' else '(长度估算6.0m,待核实)'
            
            wt_info = calc_weight(section_type, params) if params else None
            if wt_info:
                weight = wt_info['weight_kgm'] * length / 1000  # kg→t
                sa = wt_info['surface_m2m'] * length
                r.append({'分项名称': f'{name}制作安装', '单位': 't', '工程量': round(weight, 3),
                         '计算式': f'{wt_info["weight_kgm"]}×{length}÷1000{calc_tail}',
                         '定额编号': '6-6', '数据来源': src})
                if sa > 0:
                    r.append({'分项名称': f'{name}防腐', '单位': 'm²', '工程量': round(sa, 2),
                             '计算式': f'{wt_info["surface_m2m"]}×{length}{calc_tail}',
                             '定额编号': '6-49', '数据来源': src})
            else:
                r.append({'分项名称': f'{name}制作安装', '单位': 't', '工程量': 0,
                         '计算式': f'待输入规格: {section_type}{params}', '定额编号': '6-6',
                         '数据来源': SRC_PENDING})
            # 防火涂料（按表面积估算）
            if wt_info:
                r.append({'分项名称': f'{name}防火涂料', '单位': 'm²', '工程量': round(sa, 2),
                         '计算式': f'{wt_info["surface_m2m"]}×{length}{calc_tail}',
                         '定额编号': '', '数据来源': src})
    
    # 2. 如果没有识图数据，用默认参数演示
    if not r:
        r.append({'分项名称': 'H型钢柱制作安装', '单位': 't', '工程量': 0, '计算式': '待CAD提取/HM340×250×9×14', '定额编号': '6-13', '数据来源': SRC_PENDING})
        r.append({'分项名称': 'H型钢梁制作安装', '单位': 't', '工程量': 0, '计算式': '待CAD提取/HN400×200×8×13', '定额编号': '6-17', '数据来源': SRC_PENDING})
        r.append({'分项名称': '钢支撑制作安装', '单位': 't', '工程量': 0, '计算式': '待CAD提取', '定额编号': '2-158', '数据来源': SRC_PENDING})
        r.append({'分项名称': '钢檩条制作安装', '单位': 't', '工程量': 0, '计算式': '待CAD提取', '定额编号': '5-100', '数据来源': SRC_PENDING})
        r.append({'分项名称': '钢结构防火涂料', '单位': 'm²', '工程量': 0, '计算式': '待CAD提取/钢结构表面积', '定额编号': '', '数据来源': SRC_PENDING})
        r.append({'分项名称': '钢结构防腐蚀', '单位': 'm²', '工程量': 0, '计算式': '待CAD提取/钢结构表面积', '定额编号': '6-49', '数据来源': SRC_PENDING})
    
    return r
