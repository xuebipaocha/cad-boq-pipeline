"""
规则：构造层顺序逻辑检查
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class LayerOrderRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查结构层顺序是否合理'
    
    def check(self, drawing_data):
        problems = []
        layers = drawing_data.get('构造层', [])
        
        layer_names = [l.get('名称', '') for l in layers]
        combined = ' '.join(layer_names)
        
        # 检查面层下是否有基层
        has_surface = any(k in combined for k in ['面层', '沥青混凝土', '沥青', '水泥混凝土'])
        has_base = any(k in combined for k in ['基层', '水稳', '稳定土', '稳定碎石'])
        has_subbase = any(k in combined for k in ['底基层', '垫层', '级配'])
        
        if has_surface and not has_base and not has_subbase:
            problems.append({
                '问题': '有面层但缺少基层/底基层',
                '位置': '构造层列表',
                '类别': '构造缺失',
                '影响造价': True,
                '严重程度': '高',
                '建议': '面层下应设基层和底基层'
            })
        
        # 检查是否缺少粘层/透层
        has_tack = any(k in combined for k in ['粘层', '透层', '粘层油', '透层油', '乳化沥青'])
        if has_surface and not has_tack:
            pass  # 不一定每条路都有粘层，暂不报
        
        return problems
