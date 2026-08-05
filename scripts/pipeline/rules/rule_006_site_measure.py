"""
规则：现场措施/构造遗漏检查
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class SiteMeasureRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '检查施工现场需要的措施/构造是否标注'
    
    def check(self, drawing_data):
        problems = []
        texts = ''.join(drawing_data.get('施工说明', []))
        layers = drawing_data.get('构造层', [])
        layer_names = ' '.join([l.get('名称', '') for l in layers])
        
        # 新旧路面搭接
        if '旧' in texts or '加宽' in texts or '改建' in texts:
            if not any(k in texts for k in ['切缝', '土工格栅', '台阶', '搭接', '贴缝']):
                problems.append({
                    '问题': '涉及新旧路面/加宽段，但未注明搭接处理措施',
                    '位置': '施工说明',
                    '类别': '构造遗漏',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '需明确新旧路搭接方案（切缝/土工格栅/台阶搭接）'
                })
        
        # 排水设施
        if '道路' in layer_names or '路面' in layer_names:
            if not any(k in texts for k in ['排水', '边沟', '泄水', '盲沟', '雨水口', '坡度', '横坡']):
                problems.append({
                    '问题': '道路工程未注明排水设施做法',
                    '位置': '施工说明',
                    '类别': '构造遗漏',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '需明确路面排水方式（边沟/泄水孔/盲沟等）'
                })
        
        # 接缝/伸缩缝（仅对水泥混凝土，不包括水泥稳定碎石）
        if '水泥混凝土' in layer_names or '混凝土路面' in layer_names or '混凝土面层' in layer_names:
            if not any(k in texts for k in ['缝', '切缝', '胀缝', '缩缝']):
                problems.append({
                    '问题': '水泥混凝土路面未注明接缝做法',
                    '位置': '施工说明',
                    '类别': '构造遗漏',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '需明确胀缝/缩缝/施工缝的设置'
                })
        
        # 附属设施
        if '道路' in layer_names or '路面' in layer_names:
            if not any(k in texts for k in ['侧石', '缘石', '路缘', '平石', '人行道']):
                pass  # 不一定每条路都有，暂不报
        
        return problems
