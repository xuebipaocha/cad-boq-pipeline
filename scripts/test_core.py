"""单元测试 — 核心功能验证"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

# 1. 算量引擎(step3 主路径 — v5.14 替代 calc_engine 死代码)
def test_engine():
    from step3_calculate import calculate
    r = calculate({'专业类型':'市政工程','面积区域':[{'面积_m2':800}]})
    assert len(r) > 0, f'算量引擎返回0条: {r}'
    assert any(i.get('分项名称','') == '路床整形' for i in r), f'缺路床整形: {[i.get("分项名称") for i in r]}'
test('算量引擎-市政能出结果', test_engine)

# 2. 专业计算器
def test_calc_building():
    from calc_building import calc
    r = calc({'面积区域':[{'面积_m2':500}],'建筑信息':{'主体':{'建筑面积_m2':1500,'含钢量_kgm2':65}},'装饰':{},'措施':{}})
    names = [i['分项名称'] for i in r]
    assert '钢筋' in names, f'钢筋未出现: {names}'
test('房建算量-钢筋', test_calc_building)

def test_calc_civil():
    from calc_civil import calc
    r = calc({'面积区域':[{'面积_m2':800}],'构造层':[{'名称':'沥青','厚度_mm':40}],'线性构件':[{'名称':'侧石','长度_m':196}]})
    names = [i['分项名称'] for i in r]
    assert any('侧石' in n for n in names), f'侧石未出现: {names}'
test('市政算量-侧石', test_calc_civil)

def test_calc_mep():
    from calc_mep import calc
    r = calc({'安装信息':{'管道':[{'名称':'DN100钢管','长度_m':150}]}})
    assert len(r) > 0
test('安装算量', test_calc_mep)

# 3. 数据库查询
def test_db():
    from pipeline.db import find_quota
    r = find_quota('沥青混凝土')
    assert len(r) > 0, f'查询失败: {r}'
test('数据库查询', test_db)

# 4. 专业识别
def test_specialty():
    from step1_recognize import detect_specialty
    r = detect_specialty(['道路','路面'], ['压实度','沥青'])
    assert r == '市政工程', f'识别为{r}'
    r = detect_specialty(['给水','排水'], ['管道','消防'])
    assert r == '安装工程', f'识别为{r}'
    r = detect_specialty(['绿化','乔木'], ['苗木','种植'])
    assert r == '园林绿化工程', f'识别为{r}'
test('专业识别', test_specialty)

# 5. CAD提取器
def test_cad_extract():
    from cad_extractor import extract_building_elements, extract_pipe_lengths, count_blocks
    # 这些需要实际DXF文件才能测，所以只测导入
    assert callable(extract_building_elements)
test('CAD提取器导入', test_cad_extract)

# 6. v5.2: 清单项目特征规格注入
def test_features_spec():
    from step4_compile_boq import build_features_map, _clean_spec
    assert _clean_spec('DN=DN100 POWER=7.5kW') == 'DN100 7.5kW'
    assert _clean_spec('未标管径') == '', '占位规格应过滤'
    cm = {
        '阀门': [{'编号': '阀门', '规格': 'DN=DN100', '数量': 4}],
        '设备': [{'编号': 'PUMP', '规格': 'POWER=7.5kW', '数量': 1}],
        '灯具': [{'编号': '双管荧光灯', '规格': '', '数量': 6}],
    }
    m = build_features_map(cm)
    assert m.get('阀门安装') == '阀门: 规格 DN100', f'阀门特征 {m}'
    assert m.get('PUMP安装') == 'PUMP: 规格 7.5kW'
    assert '双管荧光灯安装' not in m, '无规格不应生成映射'
test('清单特征规格注入', test_features_spec)

# 7. v5.4: 工程性质判定 + 大修计算器
def test_project_nature():
    from step1_recognize import detect_project_nature
    # 大修: 拆除关键词多
    nature, detail = detect_project_nature(['拆除外墙饰面', '拆除旧防水层', '大修工程', '维修裂缝'])
    assert nature == '大修与改造', f'应判大修: {nature} {detail}'
    assert detail['分数'] >= 4, f'分数不足: {detail}'
    # 新建: 无拆除词
    nature2, _ = detect_project_nature(['新建厂房', '结构施工图', '钢筋混凝土框架'])
    assert nature2 == '新建', f'应判新建: {nature2}'
    # 弱信号不误判: 仅1个更换
    nature3, detail3 = detect_project_nature(['更换一个阀门'])
    assert nature3 == '新建', f'弱信号应默认新建: {nature3} {detail3}'
test('工程性质判定', test_project_nature)

# 8. v5.4: 大修计算器分项输出 (v5.7: 外墙面积=人工算量口径公式)
def test_calc_renovation():
    from calc_renovation import calc
    data = {
        '面积区域': [{'面积_m2': 3000, '周长_m': 200}],
        '施工说明': ['建筑高度 16.0m', '外墙仿石涂料两道', '聚氨酯防水涂料', '雨水管拆除更换, 雨水管总长度约85m'],
        '标注关联': [],
    }
    r = calc(data)
    names = {i['分项名称'] for i in r}
    assert '外墙仿石涂料' in names, f'缺仿石涂料: {names}'
    assert '雨水管更换' in names, f'缺雨水管: {names}'
    assert '外立面脚手架' in names, f'缺脚手架: {names}'
    assert '土方开挖' not in names, f'大修不应有土方: {names}'
    # 雨水管 85m 精确(文字标注)
    pipe = [i for i in r if i['分项名称'] == '雨水管更换'][0]
    assert pipe['工程量'] == 85.0, f'雨水管 {pipe["工程量"]}'
    assert pipe['数据来源'] == '文字标注', f'来源 {pipe["数据来源"]}'
    # v5.7: 外墙面积需图纸参数(门窗表/立面标注); 提取不到 → 待提取(不估算)
    facade = [i for i in r if i['分项名称'] == '外墙仿石涂料'][0]
    assert facade['工程量'] == 0 and facade['数据来源'] == '待提取', f'无参数应待提取: {facade}'
test('大修计算器分项', test_calc_renovation)

# 9. v5.5: 大修文字结构化解析(通用样例)
def test_reno_parser():
    from reno_parser import parse_renovation_text
    texts = [
        '1. 将东、西立面外墙涂料层整体拆除，北立面外墙涂料层局部拆除，维修面积约500m2。',
        '2. 雨水斗个数约6个，雨水管总长度约120m，均更换为UPVC。',
        '3. 楼梯踏步混凝土面层拆除，拆除厚度约30mm，拆除后重新铺设混凝土地面。',
        '4. 屋面原有防水层拆除，重新铺设防水卷材。',
        '5. 外墙做法：5厚聚合物抗裂砂浆压入耐碱玻纤网格布，30厚1:2.5聚合物水泥砂浆，仿石涂料两道。',
    ]
    r = parse_renovation_text(texts)
    # 拆除项: 外墙/楼梯/屋面 3 项(措施性文字不误抓)
    assert len(r['拆除项']) >= 3, f'拆除项 {r["拆除项"]}'
    ext = [d for d in r['拆除项'] if '外墙' in d['部位']]
    assert ext and ext[0]['范围'] == '整体', f'外墙拆除范围 {ext}'
    # 数量参数: 雨水斗/雨水管/维修面积
    q = r['数量参数']
    assert q.get('雨水斗_个') == 6, f'雨水斗 {q}'
    assert q.get('雨水管_m') == 120, f'雨水管 {q}'
    assert q.get('维修面积_m2') == 500, f'维修面积 {q}'
    # 做法层
    assert r['做法层'], f'做法层 {r["做法层"]}'
    # 重做项
    assert any('重新铺设' in a['动作'] for a in r['重做项']), f'重做项 {r["重做项"]}'
    # 措施性文字不误判拆除
    r2 = parse_renovation_text(['进行相关拆除时应对建筑材料做好保护措施。'])
    assert r2['拆除项'] == [], f'措施性文字误判: {r2["拆除项"]}'
test('大修文字结构化解析', test_reno_parser)

# 10. v5.6: 数字三要素校验(防编造核心)
def test_quality_checks():
    from quality import check_number, SRC_PENDING, SRC_TEXT, extract_qty
    # 502 无单位 → 不可信
    ok, reason = check_number(502, '', '维修面积约502')
    assert not ok and '单位' in reason, f'502应缺单位: {reason}'
    # 176 m² 有单位 → 可信
    ok2, _ = check_number(176, 'm2', '总维修面积约176m2')
    assert ok2, '176m2应可信'
    # 量级存疑: 面积 1e9
    ok3, reason3 = check_number(1e9, 'm2', '面积')
    assert not ok3 and '量级' in reason3, f'1e9应量级存疑: {reason3}'
    # extract_qty: 无单位 → 待提取
    v, src, note = extract_qty('维修面积约502。', [r'维修面积[^0-9]{0,8}(\d+(?:\.\d+)?)'], '', '维修面积')
    assert v is None and src == SRC_PENDING, f'应待提取: {v} {src}'
    # 有单位 → 文字标注
    v2, src2, _ = extract_qty('雨水管总长度约85m。', [r'雨水管[^0-9]{0,15}?(\d+(?:\.\d+)?)\s*m'])
    assert v2 == 85 and src2 == SRC_TEXT, f'85m应文字标注: {v2} {src2}'
    # 公式注册表
    from quality import check_formula
    okf, _ = check_formula('外墙面积_立面图标注')
    assert okf, '已注册公式应通过'
    okf2, reasonf = check_formula('周长×高度')
    assert not okf2, f'未注册公式应拦截: {reasonf}'
test('数字三要素校验', test_quality_checks)

# 11. v5.6: rule_025 数据可信度
def test_data_trust_rule():
    from pipeline.rules.rule_025_data_trust import DataTrustRule
    r = DataTrustRule()
    p1 = r.check({'施工说明': ['北立面外墙维修面积约502。'], '面积区域': [], '构件模型': {}})
    assert any('502' in p['问题'] for p in p1), f'502应命中: {p1}'
    p2 = r.check({'施工说明': ['总维修面积约176m2。'], '面积区域': [], '构件模型': {}})
    assert not any('176' in p['问题'] for p in p2), f'176不应误报: {p2}'
    p3 = r.check({'施工说明': [], '面积区域': [{'面积_m2': 3191.92, '面积来源': '文字面积标注(图签权威)'}], '构件模型': {}})
    assert any('图签' in p['问题'] for p in p3), f'图签应提示: {p3}'
test('数据可信度规则', test_data_trust_rule)

print(f'\n结果: {passed}通过, {failed}失败, 共{passed+failed}项')
