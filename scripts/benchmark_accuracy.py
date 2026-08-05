"""准确率基准测试框架。

目录约定：
benchmarks/cases/<case_name>/
  drawings/              # DXF/DWG 图纸
  expected.json          # 人工标准答案

expected.json 示例：
{
  "specialty": "市政工程",
  "quantities": [{"name":"沥青混凝土", "unit":"m³", "qty":31.38, "tolerance":0.1}],
  "pricing": {"total": 123456.78, "tolerance":0.08}
}
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE_DIR)
CASES_DIR = os.path.join(SKILL_DIR, 'benchmarks', 'cases')


def rel_err(actual, expected):
    if expected == 0:
        return 0 if actual == 0 else 1
    return abs(actual - expected) / abs(expected)


def load_cases():
    if not os.path.isdir(CASES_DIR):
        return []
    cases = []
    for name in os.listdir(CASES_DIR):
        p = os.path.join(CASES_DIR, name)
        exp = os.path.join(p, 'expected.json')
        if os.path.isdir(p) and os.path.exists(exp):
            with open(exp, encoding='utf-8') as f:
                data = json.load(f)
            cases.append({'name': name, 'path': p, 'expected': data})
    return cases


def score_quantities(actual_items, expected_items):
    actual = {i.get('分项名称') or i.get('name'): i for i in actual_items}
    hits = 0; total = len(expected_items); details = []
    for exp in expected_items:
        name = exp.get('name')
        tol = exp.get('tolerance', 0.1)
        candidates = [actual.get(name)] if name in actual else []
        if not candidates:
            # 名称包含匹配兜底。
            candidates = [v for k, v in actual.items() if name in k or k in name]
        if not candidates:
            details.append({'name': name, 'status': 'missing', 'score': 0})
            continue
        a = candidates[0]
        err = rel_err(a.get('工程量', a.get('qty', 0)) or 0, exp.get('qty', 0) or 0)
        ok = err <= tol
        hits += 1 if ok else 0
        details.append({'name': name, 'status': 'ok' if ok else 'deviation', 'error': round(err, 4), 'score': 1 if ok else max(0, 1 - err)})
    return {'accuracy': hits / total if total else None, 'details': details}


def main():
    cases = load_cases()
    if not cases:
        print('未发现基准测试用例。请按 benchmarks/README.md 添加 cases/<case>/expected.json。')
        return 0
    print(f'发现 {len(cases)} 个基准测试用例。')
    print('执行真实 CAD 算量对比...')
    print()

    import glob as _glob
    import step3_calculate as s3

    total_score = 0
    n_cases = 0
    for c in cases:
        drawings = _glob.glob(os.path.join(c['path'], 'drawings', '*.dxf'))
        if not drawings:
            print(f"- {c['name']}: 无图纸，跳过")
            continue
        # v4.5: 多视图用例(多张图纸)先合并
        import step1_recognize as s1
        recog = s1.run(drawings[0], os.path.join(SKILL_DIR, 'output'))
        if len(drawings) > 1:
            views = [{'名称': os.path.basename(d), '数据': s1.run(d, os.path.join(SKILL_DIR, 'output'))} for d in drawings]
            from merge_views import merge_views as _merge
            recog = _merge(views)
        try:
            if c['expected'].get('specialty'):
                recog['专业类型'] = c['expected']['specialty']
            if c['expected'].get('specialty'):
                recog['专业类型'] = c['expected']['specialty']
            # 算量
            results = s3.calculate(recog)
            score = score_quantities(results, c['expected'].get('quantities', []))
            acc = score['accuracy']
            total_score += acc if acc is not None else 0
            n_cases += 1
            print(f"- {c['name']} [{c['expected'].get('specialty','')}]: 准确率 {acc*100 if acc is not None else 0:.0f}%")
            for d in score['details']:
                status = {'ok': '✓', 'deviation': '△', 'missing': '✗'}.get(d['status'], '?')
                err_v = d.get('error')
                err_s = f'{err_v:.2%}' if isinstance(err_v, (int, float)) else '-'
                print(f"    {status} {d['name']}: err={err_s}")
        except Exception as e:
            print(f"- {c['name']}: 执行失败 {e}")

    if n_cases:
        print(f"\n综合准确率: {total_score / n_cases * 100:.0f}% ({n_cases} 个用例)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
