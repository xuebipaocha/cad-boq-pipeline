"""
规则：工程量偏差检查 — v4.0 增强
- 面积过小异常（可能是单位错误）
- 量纲异常: $INSUNITS=米 的图若按毫米换算, 面积会缩水 1e6 倍
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db

class QuantityDeviationRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '工程量合理性检查：偏差过大或异常'

    def check(self, drawing_data):
        problems = []
        areas = drawing_data.get('面积区域', [])
        meta = drawing_data.get('图纸元数据', {})

        if not areas:
            return problems

        max_area = max(a.get('面积_m2', 0) for a in areas)

        # 极小面积检查（可能单位错误）
        if max_area < 1:
            problems.append({
                '问题': f'面积过小({max_area:.2f}m²)，可能识别有误或单位错误',
                '位置': '面积区域',
                '类别': '数据异常',
                '影响造价': True,
                '严重程度': '中',
                '建议': '请确认图纸单位是否正确'
            })

        # v4.0: 量纲异常检测 — 米制图面积异常小, 疑似按毫米换算(÷1e6)
        insunits = meta.get('insunits', 4)
        if max_area > 0 and insunits == 6 and max_area < 10:
            problems.append({
                '问题': f'图纸单位为米但识别面积仅{max_area:.2f}m²，疑似按毫米换算(÷1e6)导致面积缩小',
                '位置': '面积区域',
                '类别': '量纲异常',
                '影响造价': True,
                '严重程度': '高',
                '建议': '米制图纸坐标直接为米，不应除以1e6'
            })

        return problems
