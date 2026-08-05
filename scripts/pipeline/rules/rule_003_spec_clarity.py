"""
规则：施工做法明确性检查
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class SpecClarityRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查做法遍数、规格尺寸是否明确'
        self.prerequisites = ['施工说明']
    
    def check(self, drawing_data):
        problems = []
        texts = drawing_data.get('施工说明', [])
        
        for t in texts:
            # 检查做法遍数（排除"压实度"等指标性描述）
            has_verb = re.search(r'刷|涂|喷|抹|铺|压', t)
            has_measures = re.search(r'\d+遍|\d+道|\d+层|厚\d+|\d+cm厚|\d+mm厚|[一二三四五六七八九十两]+遍|[一二三四五六七八九十两]+道|[一二三四五六七八九十两]+层', t)
            is_excluded = '压实度' in t or '密度' in t or '强度' in t
            if has_verb and not has_measures and not is_excluded:
                problems.append({
                    '问题': f'做法遍数/厚度不明确: "{t[:50]}..."',
                    '位置': '施工说明',
                    '类别': '做法不明确',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '需明确施工遍数和厚度，以准确计价'
                })
            
            # 检查"按图施工""详见××"等模糊表述
            if re.search(r'按图|详见|按要求|按规范|按设计', t):
                problems.append({
                    '问题': f'表述模糊: "{t[:60]}..."',
                    '位置': '施工说明',
                    '类别': '表述不明确',
                    '影响造价': True,
                    '严重程度': '低',
                    '建议': '需提供具体的图集号或规范编号'
                })
        
        return problems[:5]
