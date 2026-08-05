# -*- coding: utf-8 -*-
"""规范知识库审图规则测试 — v5.0 P3

模式: 直接构造含构件模型的 dict, 调 pipeline.engine.run_review, 断言规则命中情况。
覆盖: 合法模型零命中 / 违规模型命中(梁高跨比/配筋率) / 缺字段跳过不抛。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.engine import load_rules, run_review

PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f'  ✅ {name}')
    except AssertionError as e:
        FAIL += 1
        print(f'  ❌ {name}: {e}')
    except Exception as e:
        FAIL += 1
        print(f'  ❌ {name}: 异常 {type(e).__name__}: {e}')


def _problem_types(problems):
    return {(p.get('类别'), p.get('问题', '')[:30]) for p in problems}


def _legal_model():
    """合法模型: KL1 300×550 长6.0m(高跨比 550/6000=1/10.9 在 1/8~1/12 内),
    KZ1 500×500 12Φ22(配筋率 1.82%), 板厚150/跨6.0m(厚跨比 1/40, 恰在下限)"""
    return {
        '专业类型': '房屋建筑与装饰工程',
        '构件模型': {
            '柱': [{'编号': 'KZ1', '数量': 6, '截面宽_mm': 500, '截面高_mm': 500,
                    '钢筋': {'纵筋': [{'根数': 12, '直径_mm': 22}], '箍筋': {'直径_mm': 8, '间距_mm': 100}, '来源': '平法标注'}}],
            '梁': [{'编号': 'KL1', '数量': 2, '跨数': 1, '截面宽_mm': 300, '截面高_mm': 550, '长度_m': 6.0,
                    '钢筋': {'上部筋': [{'根数': 2, '直径_mm': 25}], '下部筋': [{'根数': 4, '直径_mm': 22}],
                            '箍筋': {'直径_mm': 8, '加密间距_mm': 100, '非加密间距_mm': 200, '肢数': 2}, '来源': '平法标注'}}],
            '板': [{'编号': '板-1', '厚度_mm': 150, '面积_m2': 360.0,
                    '配筋': {'底筋': [{'直径_mm': 10, '间距_mm': 200}], '面筋': [], '双层双向': True}}],
            '墙': [], '房间': [],
        },
    }


def _violating_model():
    """违规模型(抗震设计): KL1 300×400 长5m 单跨(高跨比 400/5000=1/12.5,
    超出抗震下限 1/10 的 15% 容差带 1/11.8 — 真违规), 柱 300×300 无配筋,
    图纸注明抗震设防烈度"""
    return {
        '专业类型': '房屋建筑与装饰工程',
        '施工说明': ['抗震设防烈度7度'],
        '构件模型': {
            '柱': [{'编号': 'KZ1', '数量': 1, '截面宽_mm': 300, '截面高_mm': 300, '钢筋': {}}],
            '梁': [{'编号': 'KL1', '数量': 1, '跨数': 1, '截面宽_mm': 300, '截面高_mm': 400, '长度_m': 5.0,
                    '钢筋': {'下部筋': [{'根数': 2, '直径_mm': 18}], '来源': '平法标注'}}],
            '板': [{'编号': '板-1', '厚度_mm': 120, '面积_m2': 100.0, '配筋': {}}],
            '墙': [], '房间': [],
        },
    }


def _real_violations(problems):
    """真正的规范违规(排除 rule_023 保护层复核的'低'严重度提示)"""
    return [p for p in problems if p.get('类别') == '规范审查' and p.get('严重程度') != '低']


def test_engine_loads_rules():
    rules = load_rules()
    names = {r.name for r in rules}
    for expected in ('BeamSpanRatioRule', 'MinRebarRatioRule', 'RebarCoverRule', 'SlabSpanRatioRule'):
        assert expected in names, f'规则 {expected} 未加载, 实际: {names}'


def test_legal_model_no_hits():
    problems = run_review(_legal_model())
    spec_hits = _real_violations(problems)
    assert not spec_hits, f'合法模型不应有规范违规: {spec_hits}'


def test_violating_model_beam_ratio_hits():
    problems = run_review(_violating_model())
    spec_hits = _real_violations(problems)
    assert spec_hits, '违规模型应有规范审查命中'
    beam_hits = [p for p in spec_hits if '高跨比' in p.get('问题', '')]
    assert beam_hits, f'应有梁高跨比命中: {[p["问题"] for p in spec_hits]}'
    assert beam_hits[0]['影响造价'] is True
    assert '规范审查' in beam_hits[0]['类别']


def test_missing_fields_skipped():
    """缺长度/配筋 → 跳过不抛, 不误报"""
    model = {
        '专业类型': '房屋建筑与装饰工程',
        '构件模型': {
            '柱': [{'编号': 'KZ1', '数量': 1, '截面宽_mm': 500, '截面高_mm': 500, '钢筋': {}}],
            '梁': [{'编号': 'KL1', '数量': 1, '截面宽_mm': 300, '截面高_mm': 600, '钢筋': {}}],
            '板': [{'编号': '板-1', '厚度_mm': 120, '面积_m2': 100.0, '配筋': {}}],
            '墙': [], '房间': [],
        },
    }
    problems = run_review(model)
    spec_hits = _real_violations(problems)
    assert not spec_hits, f'缺字段应跳过不误报: {spec_hits}'


def test_multispan_beam_no_false_positive():
    """v5.1 多跨修正: KL1(3) 300×600 长24m → 单跨 8m, 高跨比 600/8000=1/13.3
    在规范 1/8~1/12(含 15% 容差)内 — 不得误报(修复 v5.0 整跨误报)。
    板厚 180/跨8m = 1/44, 在 1/40 下限的 15% 容差带内 — 也不误报"""
    model = {
        '专业类型': '房屋建筑与装饰工程',
        '构件模型': {
            '柱': [], '墙': [],
            '梁': [{'编号': 'KL1', '数量': 1, '跨数': 3, '截面宽_mm': 300, '截面高_mm': 600,
                    '长度_m': 24.0, '钢筋': {'下部筋': [{'根数': 4, '直径_mm': 22}], '来源': '平法标注'}}],
            '板': [{'编号': '板-1', '厚度_mm': 180, '面积_m2': 360.0, '配筋': {}}],
            '房间': [],
        },
    }
    problems = run_review(model)
    spec_hits = _real_violations(problems)
    assert not spec_hits, f'多跨合规梁不应误报: {spec_hits}'


def test_no_component_model_data_insufficient():
    """无构件模型 → 前置条件不满足, engine 记数据不足, 不抛"""
    problems = run_review({'专业类型': '房屋建筑与装饰工程'})
    # run_review 对缺前置条件的规则产生 '数据不足' 提示, 不应抛异常
    assert isinstance(problems, list)


if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print('规范知识库审图规则测试:')
    test('规则自动加载(rule_021~024)', test_engine_loads_rules)
    test('合法模型零规范命中', test_legal_model_no_hits)
    test('违规模型梁高跨比命中', test_violating_model_beam_ratio_hits)
    test('缺字段跳过不误报', test_missing_fields_skipped)
    test('多跨合规梁不误报(v5.1)', test_multispan_beam_no_false_positive)
    test('无构件模型不抛异常', test_no_component_model_data_insufficient)
    print(f'\n结果: {PASS}通过, {FAIL}失败, 共{PASS + FAIL}项')
    sys.exit(1 if FAIL else 0)
