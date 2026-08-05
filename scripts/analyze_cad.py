#!/usr/bin/env python3
"""
CAD 图纸综合分析工具 — 施工+造价双视角 + SVG 交叉验证
自动识别图纸内容、提取工程量、分析施工工艺
"""
import sys, os, json, math, re, subprocess, tempfile, shutil, io
from collections import defaultdict
from pathlib import Path

# ========== 环境检测 ==========
SKILL_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = SKILL_DIR / "tools" / "oda"
ODA_EXE = TOOLS_DIR / "ODAFileConverter.exe"

# ODA替代查找路径（其他skill可能已安装）
ALT_ODA = Path(__file__).resolve().parent.parent.parent / ".reasonix" / "skills" / "cad-drawing-analysis" / "tools" / "oda" / "ODAFileConverter.exe"

def _ensure_oda():
    """确保ODA可用，找不到就自动下载"""
    if ODA_EXE.exists():
        return True
    if ALT_ODA.exists():
        # 从其他skill复制过来
        import shutil
        src_dir = ALT_ODA.parent
        dst_dir = TOOLS_DIR
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        return True
    # 自动下载
    print("[ODA] 未找到 ODA File Converter，正在自动下载...")
    try:
        import urllib.request
        import zipfile
        os.makedirs(TOOLS_DIR, exist_ok=True)
        # ODA MSI下载
        url = "https://download.opendesign.com/guestfiles/oda_file_converter/ODAFileConverter_27.1.16.msi"
        msi_path = TOOLS_DIR / "ODAFileConverter.msi"
        urllib.request.urlretrieve(url, msi_path)
        # 用msiexec静默安装
        import subprocess
        subprocess.run(["msiexec", "/a", str(msi_path), "/qn", f"TARGETDIR={TOOLS_DIR.parent}"], capture_output=True)
        print("[ODA] 安装完成")
        return ODA_EXE.exists()
    except Exception as e:
        print(f"[ODA] 下载失败: {e}")
        print("[ODA] 请手动下载安装: https://www.opendesign.com/guestfiles/oda_file_converter")
        return False

EZ_DXF_OK = False
D2S_OK = False

try:
    import ezdxf
    EZ_DXF_OK = True
except ImportError:
    pass

try:
    from dxf2svg.pycore import save_svg_from_dxf as dxf_to_svg  # 0.1.4: 函数在 pycore 子模块
    D2S_OK = True
except ImportError:
    try:
        from dxf2svg import dxf_to_svg  # 旧版 API 兼容
        D2S_OK = True
    except ImportError:
        pass

def check_env():
    """检测运行环境，输出JSON状态"""
    status = {"ezdxf": bool(EZ_DXF_OK), "oda": _ensure_oda(), "dxf2svg": bool(D2S_OK)}
    if not status["ezdxf"]:
        status["fix"] = "pip install ezdxf"
    if not status["oda"]:
        status["oda_note"] = "ODA File Converter 未安装，仅支持 DXF 文件"
    if not status["dxf2svg"]:
        status["dxf2svg_note"] = "dxf2svg 未安装，跳过 SVG 交叉验证 (pip install dxf2svg)"
    return status


def dwg_to_dxf(dwg_path, output_dir=None):
    """用 ODA 将 DWG 转为 DXF"""
    if not _ensure_oda():
        return None, "ODA File Converter 不可用"
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="cad_")
    else:
        os.makedirs(output_dir, exist_ok=True)

    dwg_path = Path(dwg_path).absolute()
    output_dir = Path(output_dir).absolute()

    with tempfile.TemporaryDirectory(prefix="oda_conv_") as tmp:
        tmp_path = Path(tmp)
        shutil.copy2(dwg_path, tmp_path / dwg_path.name)

        cmd = [str(ODA_EXE), str(tmp_path), str(tmp_path),
               "ACAD2013", "DXF", "0", "1", dwg_path.name]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True,
                           creationflags=0x08000000, timeout=180)
        except Exception as e:
            return None, str(e)

        dxf_files = list(tmp_path.glob("*.dxf"))
        if not dxf_files:
            return None, "转换后未生成 DXF 文件"
        dest = output_dir / dxf_files[0].name
        shutil.move(str(dxf_files[0]), str(dest))
        return dest, None


# ========== 文本聚类拼接 ==========
def cluster_texts(text_entities, y_tolerance=600):
    """将碎片化的CAD文字按行聚类，按x坐标从左到右拼接"""
    rows = defaultdict(list)
    for t in text_entities:
        pos = t.get("position", {})
        y = pos.get("y", 0)
        x = pos.get("x", 0)
        bucket = round(y / y_tolerance) * y_tolerance
        rows[bucket].append((x, t.get("content", ""), t.get("layer", "")))
    result = []
    for y_bucket in sorted(rows.keys(), reverse=True):
        items = sorted(rows[y_bucket])
        line = "".join(txt for _, txt, _ in items)
        layers = list(set(l for _, _, l in items))
        result.append({"y": y_bucket, "text": line, "layers": layers, "items": len(items)})
    return result


# ========== 独立几何交叉验证(v5.8: 替代 dxf2svg — 老库与新版 ezdxf 不兼容) ==========
# dxf2svg 0.1.4(2016 停更)用 e.dxf.center[:2] 切片, ezdxf 1.4 的 Vec3 不支持 →
# 改为用 ezdxf 直接做"独立第三维度"验证(闭合区域/填充/范围), 效果等价且无老库依赖
def parse_svg_for_validation(dxf_path):
    """
    独立几何交叉验证(原 SVG 验证的 ezdxf 自研替代):
    - 闭合多段线聚合(独立于 key_entities 的再次扫描)
    - HATCH 填充区域面积
    - 模型空间范围
    - 文字样本(UTF-8 直接读取)
    """
    result = {
        "available": False,
        "filled_areas": [],
        "largest_closed": None,
        "text_samples": [],
        "geometry_extent": None,
        "notes": []
    }

    try:
        import ezdxf as _ez
        from units import shoelace_area, to_m2, length_to_m
        doc = _ez.readfile(dxf_path)
        msp = doc.modelspace()
        insunits = doc.header.get("$INSUNITS", 4)

        # 1. 闭合区域(v5.13: 复用 units.collect_closed_polys — 与主流程同源)
        from units import collect_closed_polys as _ccp
        closed_areas = _ccp(msp, insunits, min_area_m2=1.0)
        if closed_areas:
            result["largest_closed"] = round(max(a["area_m2"] for a in closed_areas), 1)

        # 2. HATCH 填充区域
        for e in msp.query("HATCH"):
            try:
                paths = e.paths
                area = 0.0
                for p in paths:
                    if p.PATH_TYPE == 'PolylinePath':
                        pts = [(v[0], v[1]) for v in p.vertices]
                        if len(pts) >= 3:
                            area += shoelace_area(pts)
                    elif p.PATH_TYPE == 'EdgePath':
                        for edge in p.edges:
                            if hasattr(edge, 'start') and hasattr(edge, 'end'):
                                area += abs(edge.start[0]*edge.end[1] - edge.end[0]*edge.start[1]) / 2
                a_m2 = to_m2(area, insunits)
                if a_m2 > 1:
                    result["filled_areas"].append({
                        "type": "hatch", "area_m2": round(a_m2, 1),
                        "layer": e.dxf.layer,
                    })
            except Exception:
                continue

        # 3. 模型空间范围
        xs, ys = [], []
        for e in msp:
            try:
                if e.dxftype() == 'LINE':
                    xs += [e.dxf.start.x, e.dxf.end.x]
                    ys += [e.dxf.start.y, e.dxf.end.y]
                elif e.dxftype() == 'LWPOLYLINE':
                    for p in e.get_points('xy'):
                        xs.append(p[0]); ys.append(p[1])
            except Exception:
                continue
        if xs:
            result["geometry_extent"] = {
                "width": round((max(xs) - min(xs)) / 1000, 1),
                "height": round((max(ys) - min(ys)) / 1000, 1),
            }

        # 4. 文字样本(UTF-8 直接读取, 无编码问题)
        for t in msp.query("TEXT"):
            txt = (t.dxf.text or '').strip()
            if len(txt) >= 2:
                result["text_samples"].append({"text": txt[:60]})
                if len(result["text_samples"]) >= 30:
                    break

        result["available"] = True
        result["notes"].append("独立几何验证完成(ezdxf 自研, 替代 dxf2svg)")
    except Exception as e:
        result["notes"].append(f"独立几何验证失败: {e}")

    return result


# ========== 核心分析函数 ==========
def analyze_cad(filepath):
    """主分析函数：返回包含所有分析结果的字典"""
    import ezdxf
    from ezdxf.math import Vec2

    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()

    result = {
        "metadata": {},
        "layers": [],
        "text_clusters": [],
        "key_entities": {},
        "svg_validation": {},
        "construction": {},
        "quantity": {},
        "validation": {},
    }

    # ---- 1. 元数据 ----
    unit_map = {0: "无单位", 1: "英寸", 2: "英尺", 4: "毫米", 5: "厘米", 6: "米"}
    insunits = doc.header.get("$INSUNITS", 0)
    type_counts = defaultdict(int)
    for e in msp:
        type_counts[e.dxftype()] += 1

    result["metadata"] = {
        "dxf_version": doc.dxfversion,
        "unit": unit_map.get(insunits, f"代码{insunits}"),
        "insunits": insunits,
        "entity_total": len(msp),
        "entity_types": dict(type_counts),
        "layer_count": len(list(doc.layers)),
        "block_count": len([b for b in doc.blocks if b.is_block_layout and not b.name.startswith("*")]),
        "layout_count": len(list(doc.layouts)),
        "file": os.path.abspath(filepath),
    }

    # ---- 2. 图层分析 ----
    layer_conventions = {
        "road": "道路", "GROUND": "场地/地面", "HONGXIAN": "红线",
        "PUB_TEXT": "公共文字", "PUB_TITLE": "标题栏", "DIM_SYMB": "标注符号",
        "DOTE": "点划线(管线)", "STAIR": "楼梯/台阶", "AXIS_TEXT": "轴线文字",
        "PUB_WALL": "墙体", "LLW1": "给排水", "Defpoints": "不可打印层",
    }
    for layer in doc.layers:
        name = layer.dxf.name
        if name.startswith("*"):
            continue
        count = len(msp.query(f'*[layer=="{name}"]'))
        entry = {
            "name": name,
            "color_index": layer.dxf.color,
            "linetype": layer.dxf.linetype,
            "is_on": layer.is_on(),
            "entity_count": count,
        }
        if name in layer_conventions:
            entry["convention"] = layer_conventions[name]
        result["layers"].append(entry)

    # ---- 3. 文字提取与聚类 ----
    # v5.3: MTEXT 格式码剥离({\fSimHei|b1|i0|c134|p2;...} → ...)
    def _clean_mtext(t):
        t = re.sub(r'\{[^}]*\\f[^;]*;', '', t)  # 字体格式码
        t = re.sub(r'\{\\[a-z][^}]*\}', '', t)  # 其他格式码(上标/下划线等)
        t = t.replace('%%C', 'Φ').replace('%%c', 'Φ')  # 直径符号
        return t.strip()

    texts_raw = []
    for t in msp.query("TEXT"):
        texts_raw.append({
            "content": t.dxf.text,
            "position": {"x": t.dxf.insert.x, "y": t.dxf.insert.y},
            "layer": t.dxf.layer,
        })
    for t in msp.query("MTEXT"):
        texts_raw.append({
            "content": _clean_mtext(t.text),
            "position": {"x": t.dxf.insert.x, "y": t.dxf.insert.y},
            "layer": t.dxf.layer,
        })
    result["text_clusters"] = cluster_texts(texts_raw)

    # ---- 4. 关键实体分析 ----
    insunits = doc.header.get("$INSUNITS", 0)
    from units import unit_scale, shoelace_area, to_m2, length_to_m, is_excluded_layer, collect_closed_polys
    mm_per_unit = unit_scale(insunits)[0]

    # 4a. 搜索面积/尺寸标注文字
    area_patterns = []
    for t in texts_raw:
        txt = t["content"].strip()
        if re.search(r'[\d]+\.?[\d]*\s*m[2²]', txt, re.I):
            area_patterns.append({"text": txt, "pos": t["position"]})
    result["key_entities"]["area_annotations"] = area_patterns

    # 4b. 闭合多段线面积 (v4.0: 单位感知换算 + 周长; v4.3: 近闭合容差;
    #     v5.13: 复用 units.collect_closed_polys 统一扫描 — 与验证流程同源)
    closed_polylines = collect_closed_polys(msp, insunits, min_area_m2=1.0)
    result["key_entities"]["closed_polylines"] = closed_polylines

    # 4c. HATCH 分析
    hatches = []
    for e in msp.query("HATCH"):
        pat = getattr(e.dxf, "pattern_name", "SOLID")
        hatches.append({"layer": e.dxf.layer, "pattern": pat})
    result["key_entities"]["hatches"] = hatches

    # 4d. 块参照
    inserts = []
    for e in msp.query("INSERT"):
        inserts.append({
            "block": e.dxf.name,
            "position": {"x": e.dxf.insert.x, "y": e.dxf.insert.y},
            "layer": e.dxf.layer,
        })
    result["key_entities"]["inserts"] = inserts

    # ---- 4e. SVG 交叉验证解析 ----
    result["svg_validation"] = parse_svg_for_validation(filepath)

    # ---- 5. 施工视角分析 ----
    construction_texts = []
    for t in texts_raw:
        txt = t["content"]
        if any(kw in txt for kw in ["施工", "保护", "校核", "清理", "压实",
                                      "碾压", "摊铺", "夯实", "养护", "衔接",
                                      "排水", "坡度", "材料", "厚度"]):
            construction_texts.append({"text": txt[:80], "pos": t["position"]})
    result["construction"]["notes"] = construction_texts

    for cluster in result["text_clusters"]:
        txt = cluster["text"]
        if re.match(r'^\d+\.', txt.strip()):
            cluster["is_list_item"] = True

    # ---- 6. 造价视角 ----
    # v4.0: 面积提取改为只匹配明确的面积标注，不再抓"长度 120m"类文字
    # v5.3: 用聚类文本(相邻文字已拼接), 且支持 '面积' 后 0-6 个字符容错
    area_value = None
    area_candidates = []
    for c in result["text_clusters"]:
        txt = (c.get("text", "") or "").strip()
        if not txt:
            continue
        if 'L/m' in txt or 'mm' in txt:
            continue
        # 只匹配 m2/㎡/平方米 或 "面积" 语境
        m = re.search(r'(\d+\.?\d*)\s*(?:m\s*[2²]|㎡|平方米)', txt, re.I)
        if m:
            val = float(m.group(1))
            if 1 < val < 20000000:
                area_candidates.append(val)
        else:
            # "面积XXXm" 语境(0-6字符容错)
            m2 = re.search(r'面积[^0-9]{0,6}(\d+\.?\d*)\s*m\b', txt)
            if m2:
                val = float(m2.group(1))
                if 1 < val < 20000000:
                    area_candidates.append(val)
    if area_candidates:
        area_value = max(area_candidates)

    if area_value:
        # v4.0: 移除硬编码道路算量模板(原地面夯实/水稳基层等固定7项)
        # 构造层由 step1.build_layers 从施工说明真实提取, 不再使用模板
        result["quantity"] = {
            "total_area_m2": area_value,
            "items": [],
            "materials": [],
        }

    # ---- 7. 交叉验证 ----
    validation_notes = []

    # 7a. 文字面积 vs 多段线面积
    if area_value and closed_polylines:
        poly_areas = [p["area_m2"] for p in closed_polylines
                      if p["layer"] not in ("图框", "PUB_TITLE", "A-STAIR")]
        if poly_areas:
            max_poly = max(poly_areas)
            ratio = area_value / max_poly if max_poly > 0 else 0
            if 0.5 < ratio < 2.0:
                validation_notes.append(f"[DXF验证] 文字面积({area_value}m2)与多段线面积({max_poly}m2)接近，可信")
            else:
                validation_notes.append(f"[DXF验证] 文字面积({area_value}m2)与多段线最大面积({max_poly}m2)差距较大(比值{ratio:.1f})，需核对")

    # 7b. 独立几何验证结果(v5.8: ezdxf 自研, 替代 dxf2svg)
    svg = result["svg_validation"]
    if svg.get("available"):
        if svg.get("largest_closed") and area_value:
            svg_area = svg["largest_closed"]
            ratio = area_value / svg_area if svg_area > 0 else 0
            if 0.3 < ratio < 3.0:
                validation_notes.append(f"[几何验证] 闭合区域最大面积({svg_area}m2)与文字面积({area_value}m2)可对照(比值{ratio:.1f})")
            else:
                validation_notes.append(f"[几何验证] 最大闭合面积({svg_area}m2)与文字面积({area_value}m2)差异大(比值{ratio:.1f})，可能区域不同")
        if svg.get("filled_areas"):
            total_fill = sum(a["area_m2"] for a in svg["filled_areas"])
            validation_notes.append(f"[几何验证] 填充区域共{len(svg['filled_areas'])}块，总面积约{total_fill:.1f}m2")
        if svg.get("geometry_extent"):
            ext = svg["geometry_extent"]
            validation_notes.append(f"[几何验证] 图纸范围 {ext['width']:.0f}m x {ext['height']:.0f}m")
        # 文字样本（UTF-8 直接读取）
        svg_texts = svg.get("text_samples", [])
        if svg_texts:
            for st in svg_texts[:5]:
                txt = st["text"]
                if re.search(r'\d+\.?\d*\s*m', txt):
                    validation_notes.append(f"[几何验证文字] \"{txt}\"")
    else:
        for note in svg.get("notes", []):
            validation_notes.append(f"[几何验证] {note}")

    # 7c. 合理性判断
    if area_value:
        implied_length_4m = area_value / 4.0
        validation_notes.append(f"[合理性] 如宽4m则长约{implied_length_4m:.0f}m，属{'合理' if 50 < implied_length_4m < 500 else '异常'}范围")

    result["validation"] = {"notes": validation_notes}

    return result


def print_report(result):
    """输出可读报告"""
    print("=" * 65)
    print("[分析报告] CAD 图纸综合分析报告")
    print("   视角：施工人员 + 造价人员 + SVG交叉验证")
    print("=" * 65)

    m = result["metadata"]
    print(f"\n[图纸概况] DXF版本: {m['dxf_version']}  单位: {m['unit']}")
    print(f"  实体总数: {m['entity_total']}  图层数: {m['layer_count']}")
    print(f"  块定义: {m['block_count']}  布局空间: {m['layout_count']}")
    print(f"  实体类型: {json.dumps(m['entity_types'], ensure_ascii=False)}")

    print(f"\n[图层] ({len(result['layers'])} 个)")
    for l in sorted(result["layers"], key=lambda x: -x["entity_count"]):
        if l["entity_count"] == 0:
            continue
        conv = f" <- {l.get('convention','')}" if "convention" in l else ""
        print(f"  {l['name']:20s}  {l['entity_count']:4d} 个实体{conv}")

    print(f"\n[文字聚类] ({len(result['text_clusters'])} 行，仅显示含编号的)")
    for c in result["text_clusters"]:
        if c.get("is_list_item") or '铺装' in c['text'] or '面积' in c['text']:
            print(f"  {c['y']:>8.0f} | {c['text'][:120]}")

    q = result.get("quantity", {})
    if q:
        print(f"\n[铺装面积] {q['total_area_m2']} m2")
        if q.get("items"):
            print(f"\n  工程量清单")
            print(f"  {'分项工程':30s} {'工程量':>10s} {'单位':>6s}")
            print(f"  {'-'*48}")
            for item in q["items"]:
                print(f"  {item['name']:30s} {item['qty']:>10.2f} {item['unit']:>6s}")
        if q.get("materials"):
            print(f"\n  材料用量")
            for mat in q["materials"]:
                print(f"  {mat['name']:12s} {mat['m3']:>8.1f} m3 = {mat['ton']:>7.1f} 吨")

    v = result.get("validation", {})
    if v.get("notes"):
        print(f"\n[验证] 验证 ({len(v['notes'])} 条)")
        for note in v["notes"]:
            print(f"  {note}")

    c = result.get("construction", {})
    if c.get("notes"):
        print(f"\n[施工要点] ({len(c['notes'])} 条，前5条)")
        for n in c["notes"][:5]:
            print(f"  . {n['text']}")

    print("\n" + "=" * 65)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python analyze_cad.py <DWG/DXF 文件>")
        print("       python analyze_cad.py --check-env")
        sys.exit(1)

    if sys.argv[1] == "--check-env":
        print(json.dumps(check_env(), ensure_ascii=False, indent=2))
        sys.exit(0)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"文件不存在: {filepath}")
        sys.exit(1)

    ext = os.path.splitext(filepath)[1].lower()

    dxf_path = filepath
    if ext == ".dwg":
        if not _ensure_oda():
            print("DWG 文件需要 ODA File Converter")
            sys.exit(1)
        print("正在转换 DWG -> DXF...")
        dxf_path, err = dwg_to_dxf(filepath, tempfile.mkdtemp(prefix="cad_"))
        if err:
            print(f"转换失败: {err}")
            sys.exit(1)
        print(f"转换完成")

    if not EZ_DXF_OK:
        print("需要 ezdxf 库: pip install ezdxf")
        sys.exit(1)

    result = analyze_cad(dxf_path)
    print_report(result)

    if dxf_path != filepath and os.path.exists(dxf_path):
        shutil.rmtree(os.path.dirname(dxf_path), ignore_errors=True)
