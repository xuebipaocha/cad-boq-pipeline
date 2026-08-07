# -*- coding: utf-8 -*-
"""真实图纸回归基线 — v6.5

真实船体大楼大修图(用户提供): 验证大修流程关键断言。
用法: VISION_OFF=1 python3 regression_real.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

PASS = 0
FAIL = 0

def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name} {detail}')

import step1_recognize as s1
from step3_calculate import calculate
from pipeline.engine import run_review

DXF = '../benchmarks/cases/真实船体大楼/drawings/船体大楼.dxf'
OUT = os.path.join(os.environ.get('TEMP', '.'), '_reg_real')
os.makedirs(OUT, exist_ok=True)

print('=== 真实船体大楼大修图回归 ===')
pid = s1.run(DXF, OUT)

check('专业识别为房建', pid.get('专业类型') == '房屋建筑与装饰工程', str(pid.get('专业类型')))
check('工程性质为大修', pid.get('工程性质') == '大修与改造', str(pid.get('工程性质')))
check('面积>1000m²', (pid.get('面积区域') or [{}])[0].get('面积_m2', 0) > 1000)
check('设计意图含拆除', (pid.get('设计意图') or {}).get('算量边界', {}).get('含拆除') is True)

# 算量 + 范围掩码
calc = calculate(pid)
check('算量分项>10', len(calc) > 10, str(len(calc)))
out_scope = [i for i in calc if i.get('范围外')]
check('范围外分项已标记', len(out_scope) >= 1, f'{len(out_scope)}项')

# 审图: 大修无门窗表 → 待确认
probs = run_review(pid)
dw = [p for p in probs if p.get('类别') == '门窗量口径']
check('rule_026 门窗待确认触发', len(dw) >= 1, str([p.get('问题') for p in dw]))

print(f'\n结果: {PASS}通过, {FAIL}失败, 共{PASS+FAIL}项')
sys.exit(1 if FAIL else 0)
