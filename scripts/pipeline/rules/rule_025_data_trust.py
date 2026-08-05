# -*- coding: utf-8 -*-
"""数据可信度规则 — v5.6 H5

扫描识图结果中的数字可信度问题:
1. 面积/数量语境数字缺单位 → 量级存疑
2. 面积区域来源为估算/图签且无交叉验证 → 提示
3. 施工说明中孤立数字(无单位无语境) → 不误报(设计说明噪声多)

输出到图纸问题清单, 类别='数据可信度'。
"""
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase
from pipeline import db
from pipeline.rules.spec_db import find_rules


class DataTrustRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '数据可信度检查(数字单位/量级/来源)'
        self.prerequisites = ['施工说明']

    def check(self, drawing_data):
        problems = []
        texts = drawing_data.get('施工说明', []) or []

        # 1. 面积/数量语境数字缺单位
        for t in texts:
            # 面积语境 + 数字, 但数字后不是单位(缺单位)
            # (负向断言: 数字完整(后面非数字)且后跟单位 m2/m/㎡/平方米/米 视为有单位)
            m = re.search(r'(?:维修面积|拆除面积|总面积|外墙面面积|外墙面积)[^0-9]{0,10}?(\d{2,6})(?!\d)(?!\s*(?:m2|平方米|㎡|m\b|米))', t)
            if m:
                v = int(m.group(1))
                if 10 <= v <= 100000:
                    problems.append({
                        '问题': f"面积语境数字 {v} 缺单位({t[:40]}…)",
                        '位置': '施工说明',
                        '类别': '数据可信度',
                        '影响造价': True,
                        '严重程度': '中',
                        '建议': '核实单位(m²/m), 避免量级误读',
                    })

        # 2. 面积区域来源提示(图签权威/估算)
        for a in drawing_data.get('面积区域', []) or []:
            src = a.get('面积来源', '')
            if '图签' in src:
                problems.append({
                    '问题': f"面积 {a.get('面积_m2')}m² 来自图签文字标注",
                    '位置': '面积区域',
                    '类别': '数据可信度',
                    '影响造价': True,
                    '严重程度': '低',
                    '建议': '与闭合轮廓/立面图交叉验证',
                })
            elif '估算' in src:
                problems.append({
                    '问题': f"面积 {a.get('面积_m2')}m² 为估算值",
                    '位置': '面积区域',
                    '类别': '数据可信度',
                    '影响造价': True,
                    '严重程度': '中',
                    '建议': '补齐图纸参数后重算',
                })

        # 3. 构件模型待提取项(如有)
        cm = drawing_data.get('构件模型', {}) or {}
        for cls, comps in cm.items():
            for c in comps or []:
                src = c.get('截面来源', '')
                if src == '默认':
                    problems.append({
                        '问题': f"{cls}构件 {c.get('编号','')} 截面为默认值(无标注证据)",
                        '位置': f'构件模型.{cls}',
                        '类别': '数据可信度',
                        '影响造价': True,
                        '严重程度': '低',
                        '建议': '补充标注或核实默认值',
                    })
        return problems
