"""
规则：厚度标注完整性检查
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class ThicknessRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查各结构层是否有厚度标注'
        self.prerequisites = ['构造层']
    
    def check(self, drawing_data):
        problems = []
        layers = drawing_data.get('构造层', [])
        for layer in layers:
            name = layer.get('名称', '未知')
            thickness = layer.get('厚度_mm')
            if thickness is None:
                problems.append({
                    '问题': f'"{name}"厚度未标注',
                    '位置': '构造层列表',
                    '类别': '标注缺失',
                    '影响造价': True,
                    '严重程度': '高',
                    '建议': '需设计明确厚度，建议先暂按常规厚度估算'
                })
            elif thickness <= 0:
                problems.append({
                    '问题': f'"{name}"厚度异常({thickness}mm)',
                    '位置': '构造层列表',
                    '类别': '数据异常',
                    '影响造价': True,
                    '严重程度': '高',
                    '建议': '请核实厚度值'
                })
        return problems
