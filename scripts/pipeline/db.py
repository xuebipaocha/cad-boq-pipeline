"""数据库与匹配工具 — v3.0 accuracy upgrade

核心能力：
- 领域词典分词 + 加权相似度匹配，替代简单 LIKE 回退。
- 清单-定额映射优先，模糊匹配兜底。
- 单位推断、单位倍率、工程量换算。
- 专业费率名称映射，避免用前4字误匹配。
"""
import math
import os
import re
import sqlite3
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE, 'data')
LIAONING_DB = os.path.join(DATA_DIR, 'liaoning_24.db')
NATIONAL_DB = os.path.join(DATA_DIR, 'national_24.db')
STRUCTURE_RULES_DB = os.path.join(DATA_DIR, 'structure_rules.db')

# v5.14 工作流 P1: schema_version 迁移机制 — 数据表结构演进登记
# 每次改表结构: 版本号 +1, 并在 migrate_* 中写迁移 SQL(幂等)
SCHEMA_VERSIONS = {
    'liaoning': 1,
    'national': 1,
    'structure_rules': 1,
}


def get_structure_rules_conn():
    conn = sqlite3.connect(STRUCTURE_RULES_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_version(db_key):
    """读取库的 schema_version(v5.14)。无表 → 0。db_key: 'liaoning'/'national'/'structure_rules'"""
    path = {'liaoning': LIAONING_DB, 'national': NATIONAL_DB,
            'structure_rules': STRUCTURE_RULES_DB}.get(db_key)
    if not path or not os.path.exists(path):
        return 0
    try:
        c = sqlite3.connect(path)
        ver = c.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
        return ver[0] if ver else 0
    except Exception:
        return 0


def set_schema_version(db_key, version):
    """写入/更新 schema_version 表(幂等, 供 setup_* 建库后调用)"""
    path = {'liaoning': LIAONING_DB, 'national': NATIONAL_DB,
            'structure_rules': STRUCTURE_RULES_DB}.get(db_key)
    if not path:
        return
    c = sqlite3.connect(path)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, updated_at TEXT DEFAULT (datetime('now')))")
        c.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        c.commit()
    finally:
        c.close()


def ensure_schema(db_key, expected_version=None):
    """v5.14 迁移入口: 检查库版本, 与代码期望不符时告警(返回 (当前, 期望, 是否一致))。
    实际迁移由各 setup_* 脚本执行(幂等); 此处保证"版本可观测"。
    """
    expected = expected_version or SCHEMA_VERSIONS.get(db_key, 1)
    current = get_schema_version(db_key)
    if current == 0:
        # 老库无版本表: 视为当前版本(首次登记)
        set_schema_version(db_key, expected)
        return (0, expected, False)
    return (current, expected, current >= expected)

COLS = "quota_code, list_code, category, sub_category, item_name, search_text, base_price, labor_cost, material_cost, machine_cost, unit"

DOMAIN_TERMS = [
    '细粒式沥青混凝土','中粒式沥青混凝土','粗粒式沥青混凝土','沥青混凝土','水泥稳定碎石','水泥稳定砂砾',
    '级配碎石','级配砂砾','侧石','路缘石','平石','人行道','透层','粘层','封层','路床','路基','土方','石方',
    '沟槽','基坑','回填','余方弃置','混凝土','钢筋混凝土','模板','钢筋','砌体','砌筑','抹灰','防水','保温',
    '楼地面','墙面','天棚','门窗','柱','梁','板','基础','管道','阀门','电缆','桥架','配管','配电箱','灯具',
    '喷淋','消火栓','风管','风机','空调','乔木','灌木','草坪','种植土','绿地','钢结构','H型钢','槽钢','角钢',
    '钢管','方管','钢板','圆钢','AC-13','AC-16','AC-20','C15','C20','C25','C30','C35','C40','HRB400','HPB300',
    # v4.5 精装: 装饰材料/构件
    '楼地面','踢脚线','石材楼地面','块料楼地面','木地板','实木复合地板','地毯','乳胶漆','墙纸','壁纸',
    '吊顶','石膏板','铝扣板','石材墙面','块料墙面','墙面砖','石材地面','自流平','架空地板','门套','窗台板',
    '楼梯踏步','天棚','抹灰','釉面砖','抛光砖','大理石','花岗岩',
    # v4.9: 2字材料词(子词拆分可命中)
    '地砖','瓷砖','地毯','墙纸','壁纸','地板','石材','涂料','吊顶','石膏','铝板',
    # v6.4: 室内装饰材料(大修做法表驱动分项)
    'PVC','橡胶地面','塑胶地面','木质地板','无机涂料','面砖','面层','防潮板','吊顶板',
]

SYNONYMS = {
    '砼': ['混凝土'], '混凝土': ['砼'], '侧石': ['路缘石','边石'], '路缘石': ['侧石','边石'], '边石': ['侧石','路缘石'],
    '粘层': ['乳化沥青'], '透层': ['石油沥青'], '沥青': ['石油沥青','沥青混凝土'], '水泥': ['水泥稳定'],
    '碎石': ['级配碎石','水泥稳定碎石'], '底基层': ['级配碎石','水泥稳定碎石'], '面层': ['沥青混凝土'],
    '管': ['管道'], '电线': ['电缆'], '苗木': ['乔木','灌木'], '栽植': ['种植'], '安装': ['安 装'],
    # v4.5 精装同义词
    '石材地面': ['石材楼地面','块料楼地面'], '地砖': ['块料楼地面','釉面砖'], '瓷砖': ['块料楼地面'],
    '抛光砖': ['块料楼地面'], '防滑地砖': ['块料楼地面'], '釉面砖': ['块料楼地面'],
    '地毯': ['地毯楼地面'], '地板': ['木地板','实木复合地板'], '木地板': ['实木复合地板'],
    '乳胶漆': ['墙面喷刷涂料','天棚喷刷涂料'], '涂料': ['墙面喷刷涂料'], '墙纸': ['墙纸裱糊','壁纸'],
    '喷刷涂料': ['墙面喷刷涂料'], '刷涂料': ['墙面喷刷涂料'], '墙面涂料': ['墙面喷刷涂料'],
    '吊顶': ['平面吊顶','跌级吊顶','天棚吊顶'], '石膏板': ['平面吊顶'], '铝扣板': ['铝扣板吊顶'],
    '踢脚': ['踢脚线','成品踢脚线'], '石材墙面': ['墙面砖','块料墙面'],
    # v6.6: 部位同义词 — '外墙'→'墙面'(外墙防水错配屋面卷材防水的根因修复),
    # '抹灰'→'一般抹灰'(外墙抹灰错配天棚抹灰)
    '外墙': ['墙面'], '内墙': ['墙面'], '抹灰': ['一般抹灰'],
    # v6.4: 室内装饰材料同义(大修做法表分项 → 国标清单)
    '橡胶地面': ['塑胶地面', 'PVC'], 'PVC': ['塑胶地面', '塑料地面', '橡胶地面'],
    '木质地板': ['木地板', '实木复合地板'], '无机涂料': ['墙面喷刷涂料', '天棚喷刷涂料'],
    '面砖': ['块料墙面', '墙面砖', '面砖墙面'], '防潮板': ['塑料板吊顶', '天棚吊顶'],
}

SPECIALTY_CATEGORY = {
    '房屋建筑与装饰工程': ['房屋建筑', '建筑', '装饰'],
    '市政工程': ['市政', '道路', '桥涵', '管网'],
    '安装工程': ['安装', '电气', '给排水', '暖通', '消防'],
    '园林绿化工程': ['园林', '绿化'],
    '钢结构工程': ['钢结构', '金属结构', '房屋建筑'],
}

# v6.4: 清单匹配的专业词根(宽松分类过滤 — 木地板在'仿古建筑工程'分类也应可选)
PROFESSION_KEYWORDS = {
    '房屋建筑与装饰工程': ['建筑', '装饰'],
    '通用安装工程': ['安装'],
    '市政工程': ['市政'],
    '园林绿化工程': ['园林', '绿化'],
    '钢结构工程': ['钢结构', '建筑'],
}

FEE_PROFESSION_MAP = {
    '房屋建筑与装饰工程': ['房屋建筑工程', '房屋建筑第1/16章', '房屋建筑'],
    '市政工程': ['市政公用工程', '市政工程', '市政第1/10册', '市政'],
    '安装工程': ['机电安装工程', '安装'],
    '园林绿化工程': ['市政工程（含园林绿化）', '园林', '绿化'],
    '钢结构工程': ['房屋建筑工程', '房屋建筑', '金属结构'],
}

SAFETY_RATE = {
    '房屋建筑与装饰工程': 0.0327,
    '安装工程': 0.0218,
    '市政工程': 0.0171,
    '园林绿化工程': 0.0171,
    '钢结构工程': 0.0327,
}
REGULATORY_RATE = 0.0165
VAT_RATE = 0.09

UNIT_PATTERNS = [
    (r'100\s*m[2²]|100\s*㎡|百平方米', '100m²'),
    (r'10\s*m[3³]|10\s*立方|十立方', '10m³'),
    (r'100\s*m(?![²³])|百米', '100m'),
    (r'10\s*m(?![²³])|十米', '10m'),
    (r'm[2²]|㎡|平方米', 'm²'),
    (r'm[3³]|立方米', 'm³'),
    (r'吨|\bt\b|T', 't'),
    (r'kg|千克', 'kg'),
    (r'株', '株'),
    (r'套', '套'),
    (r'台', '台'),
    (r'个', '个'),
    (r'樘', '樘'),
    (r'座', '座'),
    (r'根', '根'),
    (r'块', '块'),
    (r'm(?![²³])|米', 'm'),
]

# v4.0: 非规范单位字符串 → 规范单位（'m3'→'m³', '100m2'→'100m²' 等）
UNIT_NORMALIZE = {
    'm2': 'm²', 'm3': 'm³', 'm4': 'm⁴', '100m2': '100m²', '100m3': '100m³',
    '10m2': '10m²', '10m3': '10m³', '1000m2': '1000m²', '1000m3': '1000m³',
    'm': 'm', 't': 't', 'kg': 'kg', 'g': 'g', 'kw': 'kW', 'kwh': 'kWh',
}

NAME_UNIT_HINTS = [
    (['土方','石方','混凝土','砼','砌体','回填','水泥稳定','沥青混凝土','砂浆'], 'm³'),
    (['模板','防水','保温','抹灰','楼地面','墙面','天棚','草坪','绿地','面层','基层','底基层','路床','人行道'], 'm²'),
    (['管道','电缆','桥架','配管','侧石','路缘石','边石','栏杆','线'], 'm'),
    (['钢筋','钢结构','H型钢','槽钢','角钢','钢管','方管','钢板','圆钢'], 't'),
    (['乔木','灌木','苗木'], '株'),
    (['阀门','灯具','配电箱','风机','设备','检查井','井','门','窗'], '个'),
]

UNIT_BASE = {
    '1000m²': ('area', 1000.0), '1000m³': ('volume', 1000.0),
    '1000m2': ('area', 1000.0), '1000m3': ('volume', 1000.0),
    '100m²': ('area', 100.0), 'm²': ('area', 1.0),
    '100m2': ('area', 100.0), 'm2': ('area', 1.0),
    '10m³': ('volume', 10.0), 'm³': ('volume', 1.0),
    '10m3': ('volume', 10.0), 'm3': ('volume', 1.0),
    '100m': ('length', 100.0), '10m': ('length', 10.0), 'm': ('length', 1.0),
    '1000m': ('length', 1000.0),
    't': ('weight', 1.0), 'kg': ('weight', 0.001),
    '株': ('count', 1.0), '套': ('count', 1.0), '台': ('count', 1.0), '个': ('count', 1.0),
    '樘': ('count', 1.0), '座': ('count', 1.0), '根': ('count', 1.0), '块': ('count', 1.0),
    '组': ('count', 1.0), '项': ('count', 1.0),
}


def get_liaoning_conn():
    conn = sqlite3.connect(LIAONING_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_national_conn():
    conn = sqlite3.connect(NATIONAL_DB)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_text(text):
    text = str(text or '').upper()
    repl = {'（':'(', '）':')', '，':',', '、':' ', '；':';', '㎡':'M²', 'Ｍ':'M', 'ｍ':'M', '³':'3', '²':'2'}
    for a, b in repl.items():
        text = text.replace(a, b)
    text = re.sub(r'[^0-9A-Z\u4e00-\u9fff@Φφ\-\.]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def tokenize(text):
    text = normalize_text(text)
    tokens = []
    used = set()
    for term in sorted(DOMAIN_TERMS, key=len, reverse=True):
        t = normalize_text(term)
        if t and t in text and t not in used:
            tokens.append(t); used.add(t)
            for syn in SYNONYMS.get(term, []):
                st = normalize_text(syn)
                if st not in used:
                    tokens.append(st); used.add(st)
        elif t and len(t) >= 3:
            # v4.8: 领域词未完整命中时拆子词(2字) — '地砖地面'→'地砖'
            for i in range(0, len(t) - 1):
                sub = t[i:i + 2]
                if sub in text and sub not in used:
                    tokens.append(sub); used.add(sub)
                    # v4.9: 子词若为同义词键, 其同义词也加入 ('地砖'→'块料楼地面')
                    for skey, syns in SYNONYMS.items():
                        if normalize_text(skey) == sub:
                            for syn in syns:
                                st = normalize_text(syn)
                                if st not in used:
                                    tokens.append(st); used.add(st)
                            break
                    for syn in SYNONYMS.get(term, []):
                        st = normalize_text(syn)
                        if st not in used:
                            tokens.append(st); used.add(st)
                    break
    for part in re.findall(r'[A-Z]+-?\d+|C\d+|HRB\d+|HPB\d+|DN\d+|Φ?\d+@?\d*|[\u4e00-\u9fff]{2,}|[A-Z]{2,}', text):
        if part not in used:
            tokens.append(part); used.add(part)
        for syn in SYNONYMS.get(part, []):
            st = normalize_text(syn)
            if st not in used:
                tokens.append(st); used.add(st)
    return tokens


def infer_unit(name, unit=None):
    if unit and str(unit).strip():
        u = str(unit).strip()
        # v4.0: 规范化非规范写法 ('m3'→'m³', '100m2'→'100m²')
        norm = UNIT_NORMALIZE.get(u.lower())
        if norm:
            return norm
        # 容忍 'm³ 100' 等混合
        m = re.search(r'(100|10|1000)?\s*(m[2²3³]|㎡|m3|m2|m)\b', u, re.I)
        if m:
            base = {'m2': 'm²', 'm3': 'm³', 'm²': 'm²', 'm³': 'm³', 'm': 'm'}.get(m.group(2).lower(), m.group(2))
            prefix = m.group(1) or ''
            return f'{prefix}{base}' if prefix else base
        return u
    txt = normalize_text(name)
    for pat, u in UNIT_PATTERNS:
        if re.search(pat, txt, re.I):
            return u
    for keys, u in NAME_UNIT_HINTS:
        if any(normalize_text(k) in txt for k in keys):
            return u
    return ''


def unit_dimension(unit):
    return UNIT_BASE.get(unit or '', ('unknown', 1.0))[0]


def unit_factor(unit):
    return UNIT_BASE.get(unit or '', ('unknown', 1.0))[1]


def quota_qty_from_list_qty(qty, list_unit, quota_unit):
    list_unit = infer_unit('', list_unit)
    quota_unit = infer_unit('', quota_unit)
    if not quota_unit or not list_unit:
        return qty, 1.0, '单位缺失未换算'
    ld, qd = unit_dimension(list_unit), unit_dimension(quota_unit)
    if ld == qd and ld != 'unknown':
        factor = unit_factor(list_unit) / unit_factor(quota_unit)
        return qty * factor, factor, f'{list_unit}->{quota_unit}'
    if list_unit == quota_unit:
        return qty, 1.0, f'{list_unit}->{quota_unit}'
    return qty, 1.0, f'单位维度不一致：{list_unit}->{quota_unit}，未换算'


def _candidate_score(query, row, category=None):
    q_tokens = tokenize(query)
    text = normalize_text(' '.join(str(row.get(k, '') or '') for k in ['item_name','search_text','quota_code','item_code','category','sub_category']))
    name = normalize_text(row.get('item_name') or row.get('name') or '')
    if not q_tokens:
        return 0.0
    weights = Counter(q_tokens)
    hit_weight = 0.0
    total_weight = 0.0
    # v4.2: 嵌入检测 — token 在名称中被汉字夹住视为嵌入(非独立词), 权重×0.3
    # 修复: '土方' 嵌入 '混凝土方沟' 不再得高分
    def _is_embedded(tok, name):
        if len(tok) > 3 or not name:
            return False
        idx = name.find(tok)
        if idx < 0:
            return False
        before = name[idx - 1] if idx > 0 else ''
        after = name[idx + len(tok)] if idx + len(tok) < len(name) else ''
        return bool(re.match(r'[一-鿿]', before) and re.match(r'[一-鿿]', after))

    for tok, cnt in weights.items():
        w = 2.2 if tok in [normalize_text(t) for t in DOMAIN_TERMS] else 1.0
        if re.match(r'(C\d+|AC-?\d+|DN\d+|HRB\d+|HPB\d+|Φ?\d+)', tok):
            w = 2.5
        total_weight += w * cnt
        if tok in text:
            embedded = _is_embedded(tok, name)
            hit_weight += w * (0.3 if embedded else 1.0) * cnt
        else:
            # v4.7: 同义词命中 — 查询词的行业同义词出现在目标名中(如 乳胶漆→墙面喷刷涂料)
            syn_hit = False
            for syn_list in SYNONYMS.values():
                if tok in [normalize_text(s) for s in syn_list]:
                    if any(normalize_text(s) in text for s in syn_list):
                        syn_hit = True
                        break
            if syn_hit:
                hit_weight += w * 0.8 * cnt
            elif w > 1.0 and len(tok) >= 5:
                # v4.0: 子词拆分 — 长领域词(≥5字)在目标中不存在时, 拆成2-3字片段匹配
                sub_hits = 0
                for L in (4, 3, 2):
                    for i in range(0, len(tok) - L + 1):
                        if tok[i:i + L] in text:
                            sub_hits += 1
                            break
                if sub_hits:
                    hit_weight += w * 0.5 * cnt
    recall = hit_weight / total_weight if total_weight else 0
    precision = sum(1 for tok in q_tokens if not _is_embedded(tok, name) and tok in text) / max(1, len(q_tokens))
    exact = 0.25 if normalize_text(query) and normalize_text(query) in text else 0
    # v4.2: 名称位置 bonus — 查询首 token 出现在名称开头(前4字)时更高权重
    # 修复: '侧石' 应优先 '侧石石质规格...'(D02道路) 而非 '楼梯电缆沟车道侧石...'(D04隧道)
    name_bonus = 0
    first_tok = q_tokens[0] if q_tokens else ''
    if first_tok and first_tok in name:
        if name.startswith(first_tok) or first_tok in name[:4]:
            name_bonus = 0.15
        else:
            name_bonus = 0.08
    cat_bonus = 0
    if category:
        cats = SPECIALTY_CATEGORY.get(category, [category])
        if any(normalize_text(c) in text for c in cats):
            cat_bonus = 0.12
    score = min(0.99, recall * 0.52 + precision * 0.25 + exact + name_bonus + cat_bonus)
    # v6.6: 部位一致性 — '外墙防水'不得匹配'屋面卷材防水'(异部位×0.6),
    # '外墙抹灰'不得匹配'天棚抹灰'; 同部位×1.1(真实图纸实测错配: 外墙防水→屋面卷材防水,
    # 外墙抹灰→天棚抹灰, 外墙的量被算到屋面/天棚科目)
    POS_GROUPS = [
        ('墙面', ['外墙', '内墙', '墙面', '墙体']),
        ('屋面', ['屋面', '屋顶']),
        ('天棚', ['天棚', '吊顶', '顶棚']),
        ('楼地面', ['楼地面', '地坪']),
    ]
    q_norm = normalize_text(query)
    q_group = next((g for g, kws in POS_GROUPS if any(k in q_norm for k in kws)), None)
    if q_group:
        c_groups = {g for g, kws in POS_GROUPS if any(k in name for k in kws)}
        if c_groups:
            if q_group in c_groups:
                score *= 1.1
            else:
                score *= 0.6
    return round(min(0.99, score), 4)


def _confidence(score, method='fuzzy'):
    if method == 'mapping': return '高'
    if score >= 0.72: return '高'
    if score >= 0.45: return '中'
    if score >= 0.25: return '低'
    return '待确认'


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


def _or_query_tokens(tokens, column='search_text'):
    main = [t for t in tokens if len(t) >= 2][:10]
    if not main:
        return '1=1', []
    # v4.2: item_name 的 LIKE 与 search_text 的 LIKE 分开打分
    # search_text 嵌入命中(如 '混凝土方沟' 含 '土方')会在评分时降权, 但预过滤仍可进入
    return '(' + ' OR '.join([f'{column} LIKE ? OR item_name LIKE ?' for _ in main]) + ')', [p for t in main for p in (f'%{t}%', f'%{t}%')]


def find_quota(name, category=None, top_n=5, list_code=None, unit=None):
    conn = get_liaoning_conn()
    results = []
    seen = set()
    try:
        if list_code:
            rows = conn.execute(f"SELECT {COLS} FROM quota_items WHERE list_code=? LIMIT ?", (list_code, top_n * 3)).fetchall()
            for r in rows:
                d = dict(r); d['unit'] = infer_unit(d.get('item_name'), d.get('unit'))
                d['_score'] = 0.96; d['_confidence'] = '高'; d['_match_method'] = 'list_code'
                results.append(d); seen.add(d.get('quota_code'))
        tokens = tokenize(name)
        cond, params = _or_query_tokens(tokens)
        sql = f"SELECT {COLS} FROM quota_items WHERE {cond}"
        if category:
            cats = SPECIALTY_CATEGORY.get(category, [category])
            cat_cond = ' OR '.join(['category LIKE ? OR sub_category LIKE ?' for _ in cats])
            sql += f" AND ({cat_cond})"
            for c in cats:
                params.extend([f'%{c}%', f'%{c}%'])
        sql += " LIMIT 500"
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            rows = conn.execute(f"SELECT {COLS} FROM quota_items WHERE item_name LIKE ? OR search_text LIKE ? LIMIT 200", (f'%{name[:4]}%', f'%{name[:4]}%')).fetchall()
        for r in rows:
            d = dict(r)
            if d.get('quota_code') in seen: continue
            d['unit'] = infer_unit(d.get('item_name'), d.get('unit'))
            score = _candidate_score(name, d, category)
            if unit and d.get('unit'):
                if unit_dimension(unit) == unit_dimension(d['unit']): score += 0.08
                else: score -= 0.12
            d['_score'] = round(max(0, min(0.99, score)), 4)
            d['_confidence'] = _confidence(d['_score'])
            d['_match_method'] = 'similarity'
            results.append(d); seen.add(d.get('quota_code'))
        results.sort(key=lambda x: (x.get('_score', 0), x.get('base_price') or 0), reverse=True)
        return results[:top_n]
    finally:
        conn.close()


def find_list_item(name, category=None, top_n=5):
    conn = get_national_conn()
    results = []
    seen = set()
    try:
        tokens = tokenize(name)
        cond, params = _or_query_tokens(tokens)
        sql = "SELECT item_code, item_name, unit, category, search_text FROM standard_items WHERE " + cond
        if category:
            # v6.4: 专业词根匹配 — '房屋建筑与装饰工程'→['建筑','装饰'] OR 匹配,
            # 原 LIKE '%category%' 会把 '木地板'(仿古建筑工程) 等有效项误过滤
            cat_keys = PROFESSION_KEYWORDS.get(category) or [
                c for c in re.split(r'[、,，/]', category or '') if c and c != '工程']
            if not cat_keys:
                cat_keys = [category]
            cat_cond = ' AND (' + ' OR '.join(['category LIKE ?'] * len(cat_keys)) + ')'
            sql += cat_cond
            params += [f'%{k}%' for k in cat_keys]
        sql += " LIMIT 500"
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            rows = conn.execute("SELECT item_code, item_name, unit, category, search_text FROM standard_items WHERE item_name LIKE ? LIMIT 200", (f'%{name[:4]}%',)).fetchall()
        for r in rows:
            d = dict(r)
            if d['item_code'] in seen: continue
            if re.match(r'.*_\d+$', d.get('item_name') or ''): continue
            d['unit'] = infer_unit(d.get('item_name'), d.get('unit'))
            score = _candidate_score(name, d, category)
            # v6.4: 超长拼接脏名(数据库占位残留, 如 '细石混凝土楼地面面层厚度、混凝压强度等级一尸...')降权
            if len(str(d.get('item_name') or '')) > 40:
                score *= 0.5
            d['_score'] = score
            d['_confidence'] = _confidence(score)
            d['_match_method'] = 'similarity'
            results.append(d); seen.add(d['item_code'])
        results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        return results[:top_n]
    finally:
        conn.close()


def get_mapped_quotas(list_code, top_n=10):
    if not list_code: return []
    nconn = get_national_conn()
    try:
        maps = nconn.execute("SELECT list_code, quota_code, relation_type FROM list_quota_mapping WHERE list_code=? LIMIT ?", (list_code, top_n)).fetchall()
    finally:
        nconn.close()
    if not maps: return []
    lconn = get_liaoning_conn()
    try:
        out = []
        for m in maps:
            r = lconn.execute(f"SELECT {COLS} FROM quota_items WHERE quota_code=? LIMIT 1", (m['quota_code'],)).fetchone()
            if r:
                d = dict(r); d['unit'] = infer_unit(d.get('item_name'), d.get('unit'))
                d['_score'] = 0.98; d['_confidence'] = '高'; d['_match_method'] = 'mapping'; d['_relation_type'] = m['relation_type']
                out.append(d)
        return out
    finally:
        lconn.close()


def get_fee_rate(specialty, fee_type, rate_type='基础费率'):
    conn = get_liaoning_conn()
    try:
        keys = FEE_PROFESSION_MAP.get(specialty, [specialty])
        for key in keys:
            r = conn.execute("SELECT base_rate, base_calc, profession FROM fee_rates WHERE category=? AND rate_type=? AND profession LIKE ? ORDER BY id LIMIT 1", (fee_type, rate_type, f'%{key}%')).fetchone()
            if r:
                return {'rate': r['base_rate'] or 0, 'base_calc': r['base_calc'] or '', 'profession': r['profession'] or ''}
        r = conn.execute("SELECT base_rate, base_calc, profession FROM fee_rates WHERE category=? AND rate_type=? LIMIT 1", (fee_type, rate_type)).fetchone()
        return {'rate': (r['base_rate'] if r else 0) or 0, 'base_calc': (r['base_calc'] if r else '') or '', 'profession': (r['profession'] if r else '') or ''}
    finally:
        conn.close()


def base_factor_from_calc(base_calc):
    return 0.35 if '35%' in (base_calc or '') or '×35' in (base_calc or '') else 1.0


def fee_rates_for_specialty(specialty):
    return {
        '文明施工和环境保护费': get_fee_rate(specialty, '文明施工和环境保护费'),
        '雨季施工费': get_fee_rate(specialty, '雨季施工费'),
        '冬季施工费': get_fee_rate(specialty, '冬季施工费'),
        '企业管理费': get_fee_rate(specialty, '企业管理费'),
        '利润': get_fee_rate(specialty, '利润'),
        '安全施工费': {'rate': SAFETY_RATE.get(specialty, 0.0171) * 100, 'base_calc': '税前分部分项费', 'profession': specialty},
        '规费': {'rate': REGULATORY_RATE * 100, 'base_calc': '分部分项+措施', 'profession': specialty},
        '增值税': {'rate': VAT_RATE * 100, 'base_calc': '税前造价', 'profession': '一般计税'},
    }


def decompose_cost(base_price, category=None):
    base = base_price or 0
    if base <= 0:
        return 0, 0, 0
    cat = normalize_text(category or '')
    if '安装' in cat:
        return base * 0.28, base * 0.62, base * 0.10
    if '市政' in cat:
        return base * 0.18, base * 0.58, base * 0.24
    if '园林' in cat:
        return base * 0.35, base * 0.55, base * 0.10
    if '钢' in cat:
        return base * 0.12, base * 0.78, base * 0.10
    return base * 0.20, base * 0.65, base * 0.15


# ───────────────────────── 主材价格库(v5.19) ─────────────────────────

def find_material_price(name, unit=None, top_n=3):
    """material_prices 表主材询价: 名称模糊匹配(仅正向: 材料名包含查询词),
    优先精确匹配/最长名称, 返回 [{material_name,unit,price,source,note}]。无匹配返回 []。"""
    conn = get_liaoning_conn()
    try:
        # 精确优先
        rows = conn.execute(
            "SELECT material_name, unit, price, source, note FROM material_prices "
            "WHERE material_name = ? ORDER BY price DESC LIMIT ?",
            (name, top_n)).fetchall()
        if not rows:
            # 模糊(正向包含), 长名优先
            rows = conn.execute(
                "SELECT material_name, unit, price, source, note FROM material_prices "
                "WHERE material_name LIKE ? ORDER BY LENGTH(material_name) DESC, price DESC LIMIT ?",
                (f'%{name}%', top_n)).fetchall()
        out = []
        for r in rows:
            out.append({'material_name': r[0], 'unit': r[1], 'price': r[2],
                        'source': r[3], 'note': r[4]})
        return out
    finally:
        conn.close()
