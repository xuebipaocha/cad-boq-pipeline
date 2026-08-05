"""
规则：图纸合规检查（0层违规/空图层/图块归属）
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class CadComplianceRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = 'CAD图纸合规检查：0层使用、空图层'
    
    def check(self, drawing_data):
        problems = []
        meta = drawing_data.get('图纸元数据', {})
        entities = meta.get('实体总数', 0)

        # 检查实体数量异常
        if entities == 0:
            problems.append({
                '问题': '图纸无实体，可能为空图纸',
                '位置': '图纸元数据',
                '类别': '合规问题',
                '影响造价': False,
                '严重程度': '高',
                '建议': '请确认图纸是否完整'
            })

        # v4.0: 0层使用检查 — 若识图结果包含 CAD分析/图层信息
        layers_info = drawing_data.get('CAD分析', {})
        if layers_info:
            from units import is_excluded_layer
            # 0层实体占比过高提示
            layer_ents = layers_info.get('elem', {}).get('图层实体', {})
            if layer_ents:
                zero = layer_ents.get('0', 0)
                total = sum(layer_ents.values())
                if total > 0 and zero / total > 0.5:
                    problems.append({
                        '问题': f'图层"0"上实体占比{zero/total:.0%}，图层规划不合规',
                        '位置': 'CAD图层',
                        '类别': '合规问题',
                        '影响造价': False,
                        '严重程度': '中',
                        '建议': '应按构件分类归入专业图层'
                    })

        return problems
