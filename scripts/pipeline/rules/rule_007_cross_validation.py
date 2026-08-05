"""
规则：多视图/多数据源逻辑一致性检查
对比同一部位在不同维度上的数据是否一致
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class CrossValidationRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查总厚度与各层之和、多个面积数据等逻辑一致性'
    
    def check(self, drawing_data):
        problems = []
        layers = drawing_data.get('构造层', [])
        areas = drawing_data.get('面积区域', [])
        texts = drawing_data.get('施工说明', [])
        
        # ─── 检查1：总厚度 vs 各层厚度之和 ───
        layers_with_thickness = [l for l in layers if l.get('厚度_mm')]
        if len(layers_with_thickness) >= 2:
            sum_thickness = sum(l['厚度_mm'] for l in layers_with_thickness)
            
            # 从文字中提取总厚度
            total_thickness_from_text = None
            for t in texts:
                ms = re.findall(r'总厚[度]?\s*(\d+)', t)
                if ms:
                    total_thickness_from_text = int(ms[0])
                    break
            
            if total_thickness_from_text and sum_thickness != total_thickness_from_text:
                diff = abs(sum_thickness - total_thickness_from_text)
                problems.append({
                    '问题': f'各层厚度之和({sum_thickness}mm)与文字标注总厚度({total_thickness_from_text}mm)不一致，差{diff}mm',
                    '位置': '构造层 vs 施工说明',
                    '类别': '逻辑冲突',
                    '影响造价': True,
                    '严重程度': '高',
                    '建议': '请核实各层厚度或总厚度标注'
                })
        
        # ─── 检查2：多个面积区域是否有明显不合理差异 ───
        if len(areas) >= 2:
            areas_sorted = sorted(areas, key=lambda a: a.get('面积_m2', 0), reverse=True)
            largest = areas_sorted[0].get('面积_m2', 0)
            for a in areas_sorted[1:]:
                name_a = areas_sorted[0].get('名称', '区域1')
                name_b = a.get('名称', '区域2')
                area_b = a.get('面积_m2', 0)
                if largest > 0 and area_b > 0:
                    ratio = largest / area_b
                    if ratio > 10:  # 相差10倍以上
                        problems.append({
                            '问题': f'两面积区域差异过大："{name_a}"({largest:.0f}m²)是"{name_b}"({area_b:.0f}m²)的{ratio:.0f}倍',
                            '位置': '面积区域',
                            '类别': '数据矛盾',
                            '影响造价': False,
                            '严重程度': '低',
                            '建议': '请确认是否为不同区域或存在重复测量'
                        })
        
        # ─── 检查3：文字厚度是否与构造层厚度一致 ───
        for layer in layers:
            name = layer.get('名称', '')
            thick = layer.get('厚度_mm')
            if thick and name:
                for t in texts:
                    if name[:4] in t:
                        # 提取厚度: v4.0 排除配筋规格(Φ8@100 的 8 不是厚度)
                        # 优先匹配 'Xmm厚' 语境, 再匹配独立厚度数字
                        ms = re.findall(r'(\d+)\s*mm\s*(?:厚|的)|厚[度]?[为:]?\s*(\d+)\s*mm', t)
                        candidates = [float(a or b) for a, b in ms if a or b]
                        if not candidates:
                            # 退路: 'Xmm' 且 X 与层名中的规格无关
                            ms2 = re.findall(r'(?<!Φ)(?<!φ)(?<!@)(\d+)\s*mm', t)
                            candidates = [float(m2) for m2 in ms2 if 5 <= float(m2) <= 2000]
                        if candidates:
                            text_thick = candidates[0]
                            if text_thick != thick:
                                problems.append({
                                    '问题': f'"{name}"厚度在构造层中为{thick}mm，但在施工说明中为{text_thick:.0f}mm',
                                    '位置': f'构造层 vs 施工说明',
                                    '类别': '逻辑冲突',
                                    '影响造价': True,
                                    '严重程度': '高',
                                    '建议': '数据矛盾，请核实'
                                })
                            break

        return problems
