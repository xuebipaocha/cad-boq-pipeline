"""
规则：规格/型号不明确检查
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class SpecDetailRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查材料规格、型号、等级是否明确'
    
    def check(self, drawing_data):
        problems = []
        texts = drawing_data.get('施工说明', [])
        layers = drawing_data.get('构造层', [])
        
        for layer in layers:
            name = layer.get('名称', '')
            material = layer.get('材料', '')
            
            # 混凝土未标强度等级（排除沥青混凝土）
            if '混凝土' in name and '沥青' not in name and not re.search(r'C\d+', name) and not re.search(r'C\d+', material):
                problems.append({
                    '问题': f'"{name}"未注明混凝土强度等级（如C30）',
                    '位置': '构造层',
                    '类别': '规格不明确',
                    '影响造价': True,
                    '严重程度': '高',
                    '建议': '需明确混凝土强度等级，不同等级单价差异大'
                })
            
            # 砂浆未标标号
            if '砂浆' in name and not re.search(r'M\d+|1\s*:\s*\d+', name):
                problems.append({
                    '问题': f'"{name}"未注明砂浆标号',
                    '位置': '构造层',
                    '类别': '规格不明确',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '需明确砂浆标号'
                })
        
        for t in texts:
            # 钢筋未标规格
            if '钢筋' in t and not re.search(r'[Φφ∅]\d+|HRB\d+|HPB\d+|螺纹\d+', t):
                problems.append({
                    '问题': f'钢筋规格不明确: "{t[:50]}..."',
                    '位置': '施工说明',
                    '类别': '规格不明确',
                    '影响造价': True,
                    '严重程度': '高',
                    '建议': '需明确钢筋直径和等级（如HRB400Φ16）'
                })
            
            # 沥青未标标号
            if '沥青' in t and not re.search(r'AC-\d+|AH-\d+|[Ａ-Ｚ]级沥青|石油沥青.*#\d+', t):
                pass  # 沥青标号可能在其他地方标注，暂不报
        
        return problems[:5]
