# -*- coding: utf-8 -*-
"""算量自检（v6.10.23）：把 PRINCIPLES.md「通用算量范式」六步变成**每次算量自动跑的检查**。

为什么要它（用户 2026-10-16 批评）：
  "之前就让你按照平法来计算难道你不记得了吗…优化到现在我觉得之前的优化很多东西是不是你都忘记了"
根因：原则写在 PRINCIPLES.md，但**没有变成工作流的强制检查** → 每次改动都只盯着局部 bug，
      整体是否仍符合六步范式，没人核 → 于是"有配筋图却用估算"能一路活到用户发现。

本模块在 step3 算量结束后调用，逐条核对并输出结构化结论：
  ① 全量看图       —— pid['全图文字'] 是否覆盖（>0）
  ② 识别构件/平法   —— **图内有配筋标注却走"待提取"→ 告警**（配筋没挂上构件）
  ③ 建模           —— 柱/梁几何是否有真实坐标/长度
  ④ 按规则算量     —— 计算式是否含规则依据（依据/规则/GB/规范）
  ⑤ 交叉校验       —— 建筑面积与来源是否有效
  ⑥ 无证据不编造   —— **仍出现"估算"字样 → 失败**（不得以估算冒充实测）
"""


def _has_rebar_marks(pid):
    """图内是否真有配筋标注（含 AutoCAD `%%` 编码与常见字形）。"""
    import re as _re
    pat = _re.compile(r'(%%1(3[012])|%%[cC]|[Φφ¶´µ§фФ])\s*\d')
    n = 0
    for it in (pid.get('全图文字') or []):
        t = str(it.get('文本') or '')
        if pat.search(t):
            n += 1
    return n


def run(pid, calc_rows):
    """返回 [{'步骤','结论','证据'}]，结论 ∈ 通过/告警/失败。"""
    out = []
    rows = calc_rows or []

    # ① 全量看图
    n_text = len(pid.get('全图文字') or [])
    out.append({'步骤': '① 全量看图', '结论': '通过' if n_text > 0 else '失败',
                '证据': '全图文字 %d 条' % n_text})

    # ② 识别构件 / 平法
    n_mark = _has_rebar_marks(pid)
    steel = next((r for r in rows if '钢筋' in str(r.get('分项名称'))), None)
    steel_note = str((steel or {}).get('计算式') or '')
    if n_mark > 0 and ('待提取' in steel_note or not steel):
        out.append({'步骤': '② 平法识别', '结论': '告警',
                    '证据': '图内有 %d 条配筋标注，但钢筋未精算（%s）→ 配筋未挂上构件' %
                            (n_mark, steel_note[:40] or '无钢筋分项')})
    elif '平法' in steel_note:
        out.append({'步骤': '② 平法识别', '结论': '通过',
                    '证据': '平法结构化解析（图内配筋标注 %d 条）' % n_mark})
    else:
        out.append({'步骤': '② 平法识别', '结论': '通过' if n_mark == 0 else '告警',
                    '证据': '图内配筋标注 %d 条；钢筋来源=%s' % (n_mark, steel_note[:40] or '无')})

    # ③ 建模
    cm = pid.get('构件模型') or {}
    n_cols = len(cm.get('柱') or [])
    beam_len = ((cm.get('梁几何') or {}).get('总长_m') or 0)
    ok3 = n_cols > 0 or beam_len > 0
    out.append({'步骤': '③ 建模（坐标/几何）', '结论': '通过' if ok3 else '告警',
                '证据': '柱 %d 组；梁轴线几何 %.2f m' % (n_cols, beam_len)})

    # ④ 按规则算量
    n_rule = sum(1 for r in rows
                 if any(k in str(r.get('计算式') or '') for k in ('依据', '规则', 'GB', '规范', '附录')))
    out.append({'步骤': '④ 按规则算量', '结论': '通过' if n_rule > 0 else '告警',
                '证据': '%d/%d 项计算式含规则依据' % (n_rule, len(rows))})

    # ⑤ 交叉校验
    bfa = pid.get('建筑面积_m2')
    src = str(pid.get('面积来源') or '')
    out.append({'步骤': '⑤ 交叉校验', '结论': '通过' if (bfa and src) else '告警',
                '证据': '建筑面积 %s m²（来源 %s）' % (bfa, src[:24] or '无')})

    # ⑥ 无证据不编造
    # ⚠ 判断"真估算"必须排除**已按第⑥条处理为"待提取"**的项 —— 其文案里也含"估算"二字
    # （如"不做含钢量估算"），实测 2-2 的钢筋已正确标待提取，却被旧判断误报"失败"。
    def _is_real_estimate(r):
        note = str(r.get('计算式') or '')
        if '待提取' in note:
            return False                     # 已按"无证据不编造"处理，合规
        return any(k in note for k in ('（估算）', '按含钢量', '按配筋率', 'kg/m²含钢量', '×65kg'))
    est = [r for r in rows if _is_real_estimate(r)]
    out.append({'步骤': '⑥ 无证据不编造', '结论': '失败' if est else '通过',
                '证据': ('仍有 %d 项使用估算：%s' % (len(est),
                          '、'.join(str(r.get('分项名称'))[:12] for r in est[:4]))) if est
                        else '未发现估算项'})
    return out


def summary(checks):
    """一行摘要（供日志/编制说明使用）。"""
    bad = [c for c in checks if c['结论'] == '失败']
    warn = [c for c in checks if c['结论'] == '告警']
    if bad:
        return '自检失败 %d 项：%s' % (len(bad), '、'.join(c['步骤'] for c in bad))
    if warn:
        return '自检通过（%d 项告警）：%s' % (len(warn), '、'.join(c['步骤'] for c in warn))
    return '自检全部通过（六步范式）'
