# -*- coding: utf-8 -*-
"""规范知识库查询 — v5.0 P3

structure_rules.db 的共享查询函数(独立库, 与定额/清单库隔离)。
v5.14 工作流 P1: 路径/连接收口到 pipeline.db。
"""
import sqlite3
from pipeline import db


def find_rules(check_type, component=None):
    """按检查类型查询规范条文(component 可空=不限构件)"""
    try:
        conn = db.get_structure_rules_conn()
        try:
            if component:
                rows = conn.execute(
                    'SELECT * FROM structure_rules WHERE check_type=? AND component=?',
                    (check_type, component)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM structure_rules WHERE check_type=?', (check_type,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []
