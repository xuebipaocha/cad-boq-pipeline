"""轻量级回归测试：验证发布包的核心链路和数据库结构。"""
import compileall
import json
import os
import sqlite3
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(SKILL_DIR, 'data')
sys.path.insert(0, BASE_DIR)

passed = 0
failed = 0


def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f'  ✅ {name}')
        passed += 1
    except Exception as e:
        print(f'  ❌ {name}: {e}')
        failed += 1


def test_compile():
    ok = compileall.compile_dir(BASE_DIR, quiet=1)
    assert ok, '存在无法编译的 Python 文件'


def test_database_structure():
    liaoning = os.path.join(DATA_DIR, 'liaoning_24.db')
    national = os.path.join(DATA_DIR, 'national_24.db')
    assert os.path.exists(liaoning), '缺少 liaoning_24.db'
    assert os.path.exists(national), '缺少 national_24.db'

    conn = sqlite3.connect(liaoning)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ['quota_items', 'fee_rates', 'measure_rules', 'unit_convert']:
        assert table in tables, f'辽宁库缺少表 {table}'
    assert conn.execute('SELECT COUNT(*) FROM quota_items').fetchone()[0] > 1000
    conn.close()

    conn = sqlite3.connect(national)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'standard_items' in tables, '国标库缺少 standard_items'
    assert conn.execute('SELECT COUNT(*) FROM standard_items').fetchone()[0] > 1000
    assert conn.execute('SELECT COUNT(*) FROM list_quota_mapping').fetchone()[0] > 0, '映射表仍为空'
    conn.close()


def test_db_queries():
    from pipeline.db import find_list_item, find_quota, infer_unit, quota_qty_from_list_qty
    quotas = find_quota('细粒式沥青混凝土AC-13', category='市政工程', top_n=3)
    lists = find_list_item('沥青混凝土路面', category='市政工程', top_n=3)
    assert quotas, '定额查询无结果'
    assert lists, '清单查询无结果'
    assert quotas[0].get('_score', 0) > 0, '定额匹配缺少评分'
    assert lists[0].get('_confidence'), '清单匹配缺少置信度'
    assert infer_unit('100m² 楼地面') == '100m²'
    q, factor, note = quota_qty_from_list_qty(250, 'm²', '100m²')
    assert abs(q - 2.5) < 0.0001 and abs(factor - 0.01) < 0.0001, note


def test_fee_rates():
    from pipeline.db import fee_rates_for_specialty
    for sp in ['房屋建筑与装饰工程', '市政工程', '安装工程', '园林绿化工程']:
        rates = fee_rates_for_specialty(sp)
        assert '企业管理费' in rates and '利润' in rates, f'{sp} 费率缺失'
        assert rates['安全施工费']['rate'] > 0, f'{sp} 安全施工费缺失'


def test_sample_pipeline_without_cad():
    import step2_review
    import step3_calculate
    import step4_compile_boq
    import step5_price

    sample = {
        '专业类型': '市政工程',
        '图纸元数据': {'单位': '毫米', '实体总数': 10},
        '面积区域': [{'名称': '主区域', '面积_m2': 800, '周长_m': 120}],
        '构造层': [
            {'名称': '细粒式沥青混凝土', '厚度_mm': 40, '材料': '沥青'},
            {'名称': '水泥稳定碎石基层', '厚度_mm': 180, '材料': '水泥稳定碎石'},
        ],
        '线性构件': [{'名称': '侧石', '长度_m': 120}],
        '施工说明': ['道路工程，铺设沥青混凝土，水泥稳定碎石基层，安装侧石。'],
        '图纸问题候选': [],
    }

    with tempfile.TemporaryDirectory(prefix='cad_boq_reg_') as out:
        recog = os.path.join(out, '识图结果.json')
        with open(recog, 'w', encoding='utf-8') as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)

        step2_review.run(recog, out)
        calc = step3_calculate.run(recog, out)
        assert calc, '算量结果为空'

        boq = step4_compile_boq.run(os.path.join(out, '算量结果.json'), out)
        assert boq, '清单结果为空'

        step5_price.run(os.path.join(out, '清单结果.json'), out)
        for filename in ['图纸问题清单.xlsx', '工程量计算书.xlsx', '分部分项工程量清单计价表.xlsx', '已组价清单.xlsx', '算量质量报告.json']:
            assert os.path.exists(os.path.join(out, filename)), f'缺少输出 {filename}'
        with open(os.path.join(out, '清单结果.json'), encoding='utf-8') as f:
            boq = json.load(f)
            assert 'match_confidence' in boq[0], '清单结果缺少匹配置信度'


def test_structure_rules_db():
    """v5.0 P3: 规范知识库结构"""
    srdb = os.path.join(DATA_DIR, 'structure_rules.db')
    assert os.path.exists(srdb), '缺少 structure_rules.db (运行 setup_structure_rules.py)'
    conn = sqlite3.connect(srdb)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'structure_rules' in tables, '缺少 structure_rules 表'
    n = conn.execute('SELECT COUNT(*) FROM structure_rules').fetchone()[0]
    assert n >= 10, f'规范条文不足 10 条, 实际 {n}'
    row = conn.execute(
        "SELECT param_value, range_max FROM structure_rules WHERE check_type='h_span_ratio' AND component='梁' AND standard_code='GB 50010-2010'"
    ).fetchone()
    assert row, '缺少梁高跨比条文'
    lo, hi = row[0], row[1]
    # 语义: "1/8~1/12" 比值 ∈ [1/12≈0.0833, 1/8=0.125] — lo 是下限, hi 是上限
    assert abs(lo - 1 / 12) < 0.01 and abs(hi - 1 / 8) < 0.01, f'梁高跨比限值异常 {lo}~{hi}'
    conn.close()


def test_geometry_validation_works():
    """v5.9: 几何交叉验证可用性 — 防静默失败(dxf2svg 事件教训)。
    断言 analyze_cad 的独立几何验证真实可用(不是'能导入'而是'能出结果')"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('analyze_cad', os.path.join(BASE_DIR, 'analyze_cad.py'))
    cad = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cad)
    # 用最小 DXF 验证
    import ezdxf
    doc = ezdxf.new('R2010')
    doc.units = 4  # 毫米
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1000, 0), (1000, 1000), (0, 1000)], close=True)
    tmp = os.path.join(tempfile.mkdtemp(prefix='cad_geo_'), 't.dxf')
    doc.saveas(tmp)
    result = cad.analyze_cad(tmp)
    sv = result.get('svg_validation', {})
    assert sv.get('available'), f'几何验证不可用: {sv.get("notes")}'
    assert sv.get('largest_closed') and abs(sv['largest_closed'] - 1.0) < 0.01, \
        f'几何验证面积错误: {sv.get("largest_closed")}'


def test_dependency_health():
    """v5.9: 依赖健康自检 — 关键依赖必须全部健康"""
    from dep_health import ensure_healthy
    ok, results = ensure_healthy(verbose=False)
    bad = [k for k, v in results.items() if not v]
    assert ok and not bad, f'依赖失效: {bad}'


test('Python 文件可编译', test_compile)
test('数据库结构完整', test_database_structure)
test('规范知识库结构完整', test_structure_rules_db)
test('数据库查询可用', test_db_queries)
test('费率映射可用', test_fee_rates)
test('样例链路可生成交付文件', test_sample_pipeline_without_cad)
test('几何验证可用性', test_geometry_validation_works)
test('依赖健康自检', test_dependency_health)

print(f'\n结果: {passed}通过, {failed}失败, 共{passed + failed}项')
if failed:
    raise SystemExit(1)
