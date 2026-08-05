"""
规则：面积一致性检查
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class AreaConsistencyRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查文字标注面积与图形面积是否一致'
        self.prerequisites = ['面积区域']
    
    def check(self, drawing_data):
        problems = []
        areas = drawing_data.get('面积区域', [])
        texts = drawing_data.get('施工说明', [])
        notes = drawing_data.get('图纸问题候选', [])

        # 从文字中提取面积（仅匹配 m2/㎡ 标注, 避免把长度 120m 当面积）
        import re
        text_areas = []
        for t in texts:
            ms = re.findall(r'(\d+\.?\d*)\s*m\s*[2²]', t, re.I)
            text_areas.extend([float(m) for m in ms if 1 <= float(m) <= 2e7])

        # v4.0: 若识图层已生成面积偏差告警(choose_area 25%容差), 直接采信为问题
        for note in notes:
            if '偏差' in note and ('面积' in note or 'm²' in note):
                problems.append({
                    '问题': note,
                    '位置': '识图-面积裁决',
                    '类别': '数据矛盾',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '以闭合多段线实测为准，或向设计确认'
                })

        for area in areas:
            name = area.get('名称', '')
            graphic_area = area.get('面积_m2', 0)

            if text_areas and graphic_area > 0:
                for ta in text_areas:
                    if abs(ta - graphic_area) / max(ta, graphic_area) > 0.05:
                        problems.append({
                            '问题': f'"{name}"文字标注面积({ta:.2f}m²)与图形面积({graphic_area:.2f}m²)不符',
                            '位置': '平面图',
                            '类别': '数据矛盾',
                            '影响造价': True,
                            '严重程度': '中',
                            '建议': '以图形实际量取为准或向设计确认'
                        })

        return problems[:5]  # 最多报5条
