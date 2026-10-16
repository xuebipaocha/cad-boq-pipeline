# -*- coding: utf-8 -*-
"""t4 定额单位修正（v6.10.10）。

依据：用户提供的《258 油漆库项目算量与建模经验总结》——库中 `unit` 列丢失计量单位系数，
`unit_convert` 表无"樘/扇"计数单位（16-77 单位丢成"见表"的系统性根因），
并经**外证法**（搜辽宁本省公开招标清单：明示定额编号+单位+工程量）核定 6 条。

原则：**只改有实证的**；无实证的（16-78/79、17-66/67）不动，留待核。
执行前已备份 data/liaoning_24.db → %LOCALAPPDATA%\\Temp\\liaoning_24_backup_61010.db
"""
import sqlite3

DB = 'data/liaoning_24.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()

# ① 补 unit_convert 缺失的放大系数（系统性缺陷：原有 100m²/100m³/100m，缺 10m²/10m³/10樘）
ADD = [
    ('m²', '10m²', 'qty/10', '面积放大10倍(定额单位 10m²)'),
    ('m³', '10m³', 'qty/10', '体积放大10倍(定额单位 10m³)'),
    ('樘', '10樘', 'qty/10', '计数放大10倍(定额单位 10樘)；原表缺"樘"→ 16-77 单位丢失'),
    ('扇', '10扇', 'qty/10', '计数放大10倍(定额单位 10扇)'),
]
for f, t, fm, d in ADD:
    if not cur.execute('SELECT 1 FROM unit_convert WHERE from_unit=? AND to_unit=?', (f, t)).fetchone():
        cur.execute('INSERT INTO unit_convert (from_unit, to_unit, formula, description) VALUES (?,?,?,?)',
                    (f, t, fm, d))
        print('  + unit_convert: %s → %s' % (f, t))

# ② quota_items.unit 修正（外证法结论；只改有实证的）
FIX = {
    '16-62': ('10m²', '外证:锦州/辽东湾/沈阳 3 条招标清单'),
    '16-77': ('10樘', '外证:跨省 6 条'),
    '16-133': ('10m³', '外证:故宫西所等 6 条'),
    '1-88': ('10m³', '运输类与 16-133 同口径'),
    '17-65': ('10m²', '用户核定(措施·脚手架)'),
    '17-3': ('100m²', '用户核定'),
}
for code, (unit, note) in FIX.items():
    row = cur.execute('SELECT unit FROM quota_items WHERE quota_code=?', (code,)).fetchone()
    if not row:
        print('  ! %s 未找到' % code)
        continue
    cur.execute('UPDATE quota_items SET unit=? WHERE quota_code=?', (unit, code))
    print('  ~ %-7s %-8s → %-8s [%s]' % (code, row[0], unit, note))

conn.commit()

print('\n③ 验证（改后 + 未改的存疑项）')
for code in list(FIX) + ['16-78', '16-79', '17-66', '17-67']:
    r = cur.execute('SELECT quota_code, unit, base_price FROM quota_items WHERE quota_code=?', (code,)).fetchone()
    tag = '' if code in FIX else '  ← 未改(无实证, 待核)'
    print('   %-7s unit=%-8s 基价=%-9s%s' % (r[0], r[1], r[2], tag))
n = cur.execute('SELECT COUNT(*) FROM unit_convert').fetchone()[0]
print('\nunit_convert 行数: %d' % n)
