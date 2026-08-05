"""规则：多视图交叉验证（平面图 vs 剖面图 vs 节点）"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class MultiViewCrossCheckRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '多视图数据一致性检查：平面图 vs 剖面图'
        self.prerequisites = ['视图']
    
    def check(self, drawing_data):
        problems = []
        views = drawing_data.get('视图', [])
        if len(views) < 2:
            return problems
        
        # 按视图名称提取面积和厚度
        areas = {}  # 视图名 → 面积列表
        thicknesses = {}
        
        for v in views:
            name = v.get('名称', '未知视图')
            data = v.get('数据', {})
            
            for a in data.get('面积区域', []):
                if name not in areas: areas[name] = []
                areas[name].append(a.get('面积_m2', 0))
            
            total_thick = 0
            for l in data.get('构造层', []):
                t = l.get('厚度_mm', 0)
                if t: total_thick += t
            thicknesses[name] = total_thick
        
        # 对比不同视图中的面积
        area_list = [(k, sum(v)) for k, v in areas.items() if v]
        for i in range(len(area_list)):
            for j in range(i+1, len(area_list)):
                name1, a1 = area_list[i]
                name2, a2 = area_list[j]
                if a1 > 0 and a2 > 0:
                    ratio = max(a1, a2) / min(a1, a2)
                    if ratio > 1.05:
                        problems.append({
                            '问题': f'"{name1}"面积({a1:.0f}m²)与"{name2}"面积({a2:.0f}m²)不一致，差{abs(a1-a2):.0f}m²',
                            '位置': f'{name1} vs {name2}',
                            '类别': '逻辑冲突',
                            '影响造价': True,
                            '严重程度': '高',
                            '建议': '请核实两个视图中同一区域的面积'
                        })
        
        # 对比不同视图中的总厚度
        thick_list = [(k, v) for k, v in thicknesses.items() if v > 0]
        for i in range(len(thick_list)):
            for j in range(i+1, len(thick_list)):
                name1, t1 = thick_list[i]
                name2, t2 = thick_list[j]
                if abs(t1 - t2) > 20:  # 厚度差>20mm
                    problems.append({
                        '问题': f'"{name1}"总厚度({t1}mm)与"{name2}"总厚度({t2}mm)不一致',
                        '位置': f'{name1} vs {name2}',
                        '类别': '逻辑冲突',
                        '影响造价': True,
                        '严重程度': '高',
                        '建议': '请核实各视图中的构造层厚度'
                    })
        
        return problems
