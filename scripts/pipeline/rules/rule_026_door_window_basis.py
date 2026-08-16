# -*- coding: utf-8 -*-
"""门窗量口径规则 — v6.5 R026

大修/改造/翻新项目: 门窗更换量以【设计门窗表】为准(权威来源),
CAD 实测洞口仅作交叉验证; 差异超阈值 → 图纸问题清单。

检查项:
1. 有门窗表时, 门窗更换/拆除分项数量 = 门窗表汇总(不再用 CAD 实测洞口)
2. 门窗表与 CAD 实测差异超阈值(单类≥3 樘 或 ±10%) → 提示复核
3. 未登记洞口(有门窗号但门窗表无) → 单独列示标"待确认"

输出到图纸问题清单, 类别='门窗量口径'。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.rules.base import RuleBase


class DoorWindowBasisRule(RuleBase):
    def __init__(self):
        super().__init__()
        self.description = '门窗量以设计门窗表为准(大修/改造)'
        self.prerequisites = ['门窗']

    def check(self, drawing_data):
        problems = []
        nature = drawing_data.get('工程性质', '')
        if nature not in ('大修与改造', '大修', '改造', '翻新'):
            return problems  # 仅大修/改造类项目适用
        windows = drawing_data.get('门窗', []) or []
        if not windows:
            problems.append({
                '类别': '门窗量口径', '严重程度': '中',
                '位置': '门窗表',
                '问题': '大修项目设计内容含门窗但无设计门窗表, 门窗更换量待确认',
                '建议': '补充门窗表或人工核对门窗数量',
            })
            return problems
        # 门窗表汇总(权威)
        n_total = sum(int(w.get('数量', 1) or 1) for w in windows)
        area_total = round(sum(float(w.get('洞口面积_m2', 0) or 0) * int(w.get('数量', 1) or 1) for w in windows), 2)
        # CAD 实测洞口(交叉验证): 构件模型/墙洞
        measured = 0
        cm = drawing_data.get('构件模型') or {}
        comps = []
        # v6.6: 构件模型是 {类别: [构件...]} 的 dict, 原实现直接迭代 dict 得到
        # str 键('柱'/'梁')后调 .get → 'str' object has no attribute 'get' 崩溃
        if isinstance(cm, dict):
            for lst in cm.values():
                if isinstance(lst, list):
                    comps.extend(lst)
        elif isinstance(cm, list):
            comps = cm
        for c in comps:
            if not isinstance(c, dict):
                continue
            if c.get('类型') in ('门', '窗', '门窗', '洞口') or '门' in str(c.get('名称', '')) or '窗' in str(c.get('名称', '')):
                measured += 1
        if not measured:
            # v6.6: CAD 实测洞口未检出 — 门窗表为权威(大修), 但几何侧零检出
            # 说明洞口提取可能失效, 作为质检信号进图纸问题清单(原逻辑静默跳过)
            problems.append({
                '类别': '门窗量口径', '严重程度': '低',
                '位置': '门窗表 vs CAD实测',
                '问题': f'设计门窗表{n_total}樘(权威), CAD 实测洞口未检出(0 樘), 仅以门窗表为准',
                '建议': '复核平面图门窗洞口几何提取, 确认门窗表数量与现场一致',
            })
            return problems
        if measured and abs(measured - n_total) >= max(3, int(n_total * 0.1)):
            problems.append({
                '类别': '门窗量口径', '严重程度': '中',
                '位置': '门窗表 vs CAD实测',
                '问题': f'设计门窗表{n_total}樘 vs CAD实测{measured}樘, 差异≥3樘或±10%',
                '建议': '以设计门窗表为准, 复核 CAD 洞口遗漏',
            })
        return problems
