"""数据质量初始化/修复脚本。

可重复执行：
- 为定额库和清单库补推断单位。
- 重建 search_text，保留规格数字与领域词。
- 为匹配字段创建索引。
- 初始化一批清单-定额映射关系。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from pipeline.db import (
    get_liaoning_conn, get_national_conn, infer_unit, normalize_text,
    find_list_item, find_quota
)

COMMON_MAPPING_QUERIES = [
    ('土方开挖', '市政工程'), ('沟槽土方', '市政工程'), ('基坑土方', '市政工程'), ('余方弃置', '市政工程'),
    ('水泥稳定碎石基层', '市政工程'), ('级配碎石基层', '市政工程'), ('沥青混凝土路面', '市政工程'),
    ('细粒式沥青混凝土', '市政工程'), ('中粒式沥青混凝土', '市政工程'), ('透层', '市政工程'), ('粘层', '市政工程'),
    ('侧石安装', '市政工程'), ('路缘石安装', '市政工程'), ('人行道铺装', '市政工程'), ('检查井', '市政工程'),
    ('混凝土基础', '房屋建筑与装饰工程'), ('矩形柱混凝土', '房屋建筑与装饰工程'), ('有梁板混凝土', '房屋建筑与装饰工程'),
    ('钢筋制作安装', '房屋建筑与装饰工程'), ('模板工程', '房屋建筑与装饰工程'), ('砌筑墙体', '房屋建筑与装饰工程'),
    ('墙面抹灰', '房屋建筑与装饰工程'), ('楼地面', '房屋建筑与装饰工程'), ('屋面防水', '房屋建筑与装饰工程'),
    ('给排水管道', '安装工程'), ('阀门安装', '安装工程'), ('电缆敷设', '安装工程'), ('桥架安装', '安装工程'),
    ('配管', '安装工程'), ('配电箱', '安装工程'), ('灯具安装', '安装工程'), ('风管制作安装', '安装工程'),
    ('乔木栽植', '园林绿化工程'), ('灌木栽植', '园林绿化工程'), ('草坪铺种', '园林绿化工程'), ('种植土回填', '园林绿化工程'),
    ('H型钢', '钢结构工程'), ('钢柱', '钢结构工程'), ('钢梁', '钢结构工程'), ('钢板', '钢结构工程')
]


def rebuild_liaoning():
    conn = get_liaoning_conn()
    cur = conn.cursor()
    rows = cur.execute('SELECT id, quota_code, item_name, category, sub_category, unit, search_text FROM quota_items').fetchall()
    changed = 0
    for r in rows:
        unit = infer_unit(r['item_name'], r['unit'])
        st = normalize_text(' '.join(str(x or '') for x in [r['quota_code'], r['item_name'], r['category'], r['sub_category']]))
        cur.execute('UPDATE quota_items SET unit=?, search_text=? WHERE id=?', (unit, st, r['id']))
        changed += 1
    cur.execute('CREATE INDEX IF NOT EXISTS idx_quota_code ON quota_items(quota_code)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_quota_list_code ON quota_items(list_code)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_quota_category ON quota_items(category)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_quota_search ON quota_items(search_text)')
    conn.commit(); conn.close()
    return changed


def rebuild_national():
    conn = get_national_conn()
    cur = conn.cursor()
    rows = cur.execute('SELECT id, item_code, item_name, category, unit, search_text FROM standard_items').fetchall()
    changed = 0
    for r in rows:
        name = r['item_name'] or ''
        unit = infer_unit(name, r['unit'])
        st = normalize_text(' '.join(str(x or '') for x in [r['item_code'], name, r['category']]))
        cur.execute('UPDATE standard_items SET unit=?, search_text=? WHERE id=?', (unit, st, r['id']))
        changed += 1
    cur.execute('CREATE INDEX IF NOT EXISTS idx_standard_code ON standard_items(item_code)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_standard_category ON standard_items(category)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_standard_search ON standard_items(search_text)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_mapping_list ON list_quota_mapping(list_code)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_mapping_quota ON list_quota_mapping(quota_code)')
    conn.commit(); conn.close()
    return changed


def seed_mapping():
    conn = get_national_conn(); cur = conn.cursor()
    inserted = 0
    # 1. 现有 list_code 与 item_code 直接/后缀关联。
    lconn = get_liaoning_conn()
    qrows = lconn.execute("SELECT DISTINCT list_code, quota_code FROM quota_items WHERE list_code IS NOT NULL AND trim(list_code)<>'' LIMIT 5000").fetchall()
    for q in qrows:
        candidates = cur.execute('SELECT item_code FROM standard_items WHERE item_code=? OR item_code LIKE ? LIMIT 5', (q['list_code'], '%' + q['list_code'])).fetchall()
        for c in candidates:
            cur.execute('INSERT OR IGNORE INTO list_quota_mapping(list_code, quota_code, relation_type, source_file, page_num) VALUES (?,?,?,?,?)',
                        (c['item_code'], q['quota_code'], 'auto_list_code', 'setup_data_quality.py', None))
            inserted += cur.rowcount
    lconn.close()
    # 2. 高频工程词通过新匹配算法生成映射。
    for query, specialty in COMMON_MAPPING_QUERIES:
        lists = find_list_item(query, category=specialty, top_n=3)
        quotas = find_quota(query, category=specialty, top_n=5)
        if not lists or not quotas:
            continue
        for li in lists[:2]:
            if li.get('_score', 0) < 0.25:
                continue
            for qu in quotas[:3]:
                if qu.get('_score', 0) < 0.30:
                    continue
                cur.execute('INSERT OR IGNORE INTO list_quota_mapping(list_code, quota_code, relation_type, source_file, page_num) VALUES (?,?,?,?,?)',
                            (li['item_code'], qu['quota_code'], 'auto_similarity', 'setup_data_quality.py', None))
                inserted += cur.rowcount
    conn.commit(); conn.close()
    return inserted


def main():
    print('数据质量初始化开始')
    qn = rebuild_liaoning(); print(f'  定额库更新: {qn} 条')
    sn = rebuild_national(); print(f'  清单库更新: {sn} 条')
    mn = seed_mapping(); print(f'  映射新增: {mn} 条')
    print('完成')


if __name__ == '__main__':
    main()
