# -*- coding: utf-8 -*-
"""规范知识库建库 — v5.0 P3 第一批

独立库 data/structure_rules.db(不塞进 national_24.db: 规范条文是长期累积资产,
而 rebuild 脚本会清表, 独立库免疫)。模式照 setup_data_quality.py: 裸 SQL +
INSERT OR IGNORE 幂等, 可重复执行。

首批 12 条 GB 条文(梁高跨比/最小配筋率/保护层/板厚跨比/构造限值)。
注意: 条文限值基于标准文本记忆, 上线前以正式 GB 文本复核; 限值全部参数化
存库, 修正只改本文件 seed 数据, 不动审图规则代码。
"""
import os
import sqlite3
import sys

if sys.stdout.encoding and 'utf-8' not in sys.stdout.encoding.lower():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
DB_PATH = os.path.join(DATA_DIR, 'structure_rules.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS structure_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_code TEXT NOT NULL,      -- 规范编号 'GB 50010-2010'
    clause TEXT,                      -- 条文号 '8.3.1'
    component TEXT,                   -- '梁'|'柱'|'板'|'墙'
    check_type TEXT NOT NULL,         -- 'h_span_ratio'|'min_rebar_ratio'|'cover'|'slab_span_ratio'|'thickness_range'
    param_name TEXT,                  -- 人读参数名 '梁高跨比下限'
    param_value REAL,                 -- 限值(1/8=0.125)
    param_unit TEXT,                  -- '无'|'%'|'mm'
    value_type TEXT,                  -- 'min'|'max'|'range'
    range_max REAL,                   -- range 上限(1/12=0.0833)
    category TEXT,                    -- '构造'|'配筋'|'保护层'
    rule_text TEXT,                   -- 条文原文
    source_file TEXT,
    page_num INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(standard_code, clause, check_type, param_name)
);
CREATE INDEX IF NOT EXISTS idx_sr_check ON structure_rules(check_type, component);
"""

# (standard_code, clause, component, check_type, param_name, value, unit, value_type, range_max, category, rule_text)
# 注意比值语义: "1/8~1/12" 表示 比值 ∈ [1/12, 1/8] — 1/12=0.0833 是下限, 1/8=0.125 是上限
STRUCTURE_RULES = [
    # GB 50010-2010 混凝土结构设计规范
    ('GB 50010-2010', '8.3.1', '梁', 'h_span_ratio', '梁高跨比', 1 / 12, '无', 'range', 1 / 8, '构造',
     '矩形截面梁, 截面高度 h 与计算跨度 l0 之比宜取 1/8~1/12'),
    ('GB 50010-2010', '8.3.1', '板', 'slab_span_ratio', '双向板厚跨比下限', 1 / 40, '无', 'min', None, '构造',
     '双向板板厚与计算跨度之比不宜小于 1/40(简支)'),
    ('GB 50010-2010', '8.3.1', '板', 'slab_span_ratio', '双向板厚跨比上限', 1 / 30, '无', 'max', None, '构造',
     '双向板板厚与计算跨度之比不宜大于 1/30(连续)'),
    ('GB 50010-2010', '8.5.1', '柱', 'min_rebar_ratio', '柱全部纵筋最小配筋率', 0.6, '%', 'min', None, '配筋',
     '柱全部纵向受力钢筋的配筋率不应小于 0.6%(轴压构件一侧)'),
    ('GB 50010-2010', '8.5.1', '梁', 'min_rebar_ratio', '梁受拉钢筋最小配筋率', 0.2, '%', 'min', None, '配筋',
     '受弯构件受拉钢筋最小配筋率不小于 max(0.2%, 0.45ft/fy)'),
    ('GB 50010-2010', '8.5.1', '板', 'min_rebar_ratio', '板最小配筋率', 0.2, '%', 'min', None, '配筋',
     '板类受弯构件受力钢筋最小配筋率不宜小于 0.2%'),
    ('GB 50010-2010', '8.2.1', '柱', 'cover', '柱保护层最小厚度', 20, 'mm', 'min', None, '保护层',
     '柱纵向受力钢筋的混凝土保护层最小厚度 20mm(室内正常环境)'),
    ('GB 50010-2010', '8.2.1', '梁', 'cover', '梁保护层最小厚度', 20, 'mm', 'min', None, '保护层',
     '梁纵向受力钢筋的混凝土保护层最小厚度 20mm(室内正常环境)'),
    ('GB 50010-2010', '8.2.1', '板', 'cover', '板保护层最小厚度', 15, 'mm', 'min', None, '保护层',
     '板受力钢筋的混凝土保护层最小厚度 15mm(室内正常环境)'),
    # GB 50011-2010 建筑抗震设计规范
    ('GB 50011-2010', '6.3.3', '梁', 'h_span_ratio', '抗震梁高跨比下限', 1 / 10, '无', 'min', None, '构造',
     '框架梁截面高度按 l0/10~l0/12 初定(一~四级抗震)'),
    ('GB 50011-2010', '6.3.4', '柱', 'thickness_range', '柱截面最小边', 400, 'mm', 'min', None, '构造',
     '矩形截面柱最小边长不宜小于 400mm(抗震设计)'),
    # GB 50003-2011 砌体结构设计规范
    ('GB 50003-2011', '6.3.1', '墙', 'thickness_range', '承重墙最小厚度', 180, 'mm', 'min', None, '构造',
     '承重墙体最小厚度 180mm'),
]


def seed_structure_rules(verbose=True):
    """建表 + 幂等灌数据(INSERT ... ON CONFLICT DO UPDATE, 可重复执行)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        n = 0
        for row in STRUCTURE_RULES:
            cur = conn.execute(
                """INSERT INTO structure_rules
                   (standard_code, clause, component, check_type, param_name, param_value,
                    param_unit, value_type, range_max, category, rule_text, source_file, page_num)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(standard_code, clause, check_type, param_name)
                   DO UPDATE SET param_value=excluded.param_value,
                                 range_max=excluded.range_max,
                                 value_type=excluded.value_type,
                                 param_unit=excluded.param_unit,
                                 category=excluded.category,
                                 rule_text=excluded.rule_text""",
                row + ('GB 标准文本(以正式文本复核为准)', 0),
            )
            n += cur.rowcount
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM structure_rules').fetchone()[0]
        if verbose:
            print(f'  规范知识库: {DB_PATH}')
            print(f'  新增 {n} 条, 累计 {total} 条')
        # v5.14 工作流 P1: schema_version 登记(幂等)
        try:
            from pipeline import db as _db
            _db.set_schema_version('structure_rules', _db.SCHEMA_VERSIONS['structure_rules'])
        except Exception:
            pass
        return total
    finally:
        conn.close()


if __name__ == '__main__':
    seed_structure_rules()
