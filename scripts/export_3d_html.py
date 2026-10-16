# -*- coding: utf-8 -*-
"""P3 三维模型导出：构件几何 → 单文件可旋转 HTML（零外部依赖）。

用途（用户 2026-10-16 需求）：
  ① 人工/视觉查看模型是否合理（悬空、重叠、比例失真、缺失、层数不对）
  ② 视觉模型看图 → 反推算量合理性 → 修正（P4 闭环）

为什么不用 three.js：本机外网不通（github/jsdelivr 均不可达），CDN 会加载失败。
故用**纯 Canvas 2D + 手写透视投影 + painter's algorithm**（按面深度排序绘制）。

诚实标注：构件分两类——
  · **实测坐标**（柱的位置/截面/高度、梁轴线几何段）→ 按真实坐标摆放；
  · **示意摆放**（板/墙/钢构件只有面积或长度、图上无独立坐标）→ 排在示意网格上，界面标"示意"。
界面顶部显式说明，避免把示意图当成实测几何。

用法：
  python export_3d_html.py <识图结果.json> <输出.html> [--title 名称]
输出 HTML 支持 ?view=iso|top|front|side 切换初始视角（便于分别截图审图）。
"""
import json
import os
import sys

COLORS = {
    '柱': '#d9534f', '梁': '#337ab7', '板': '#8c8c8c', '墙': '#e0a800',
    '钢构件': '#20c997', '基础': '#795548',
}

HTML_TMPL = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>__TITLE__ — 三维模型</title>
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:#0f1419;color:#e8e8e8;
            font:13px/1.5 "Segoe UI",system-ui,sans-serif}
  #c{display:block;width:100vw;height:100vh;cursor:grab}
  #c.drag{cursor:grabbing}
  .panel{position:absolute;background:rgba(20,26,33,.9);border:1px solid #2c3947;
         border-radius:6px;padding:10px 12px;backdrop-filter:blur(4px)}
  #hud{top:12px;left:12px;max-width:430px}
  #hud h1{margin:0 0 6px;font-size:15px;color:#fff}
  #hud .warn{color:#ffcc66;margin:4px 0 8px}
  #legend{bottom:12px;left:12px}
  #stat{bottom:12px;right:12px;text-align:right}
  .k{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:6px;
     vertical-align:-1px}
  .row{margin:2px 0}
  .dim{color:#8fa3b8}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="hud" class="panel">
  <h1>__TITLE__</h1>
  <div id="meta" class="dim"></div>
  <div class="warn" id="caveat"></div>
  <div class="dim">拖拽旋转 · 滚轮缩放 · 双击复位</div>
</div>
<div id="legend" class="panel"></div>
<div id="stat" class="panel"></div>
<script>
const BOXES = __BOXES__;
const META  = __META__;

// ---------- 世界坐标范围 → 自动居中缩放 ----------
let minx=1e18,miny=1e18,minz=1e18,maxx=-1e18,maxy=-1e18,maxz=-1e18;
for (const b of BOXES){
  minx=Math.min(minx,b.x-b.dx/2); maxx=Math.max(maxx,b.x+b.dx/2);
  miny=Math.min(miny,b.y-b.dy/2); maxy=Math.max(maxy,b.y+b.dy/2);
  minz=Math.min(minz,b.z);        maxz=Math.max(maxz,b.z+b.dz);
}
const cx=(minx+maxx)/2, cy=(miny+maxy)/2, cz=(minz+maxz)/2;
const span=Math.max(maxx-minx, maxy-miny, maxz-minz) || 1;
const S = 2.6/span;                       // 世界 → 归一化

// ---------- 相机 ----------
const q = new URLSearchParams(location.search);
const VIEWS = {iso:[0.62,-0.52],top:[0.001,-1.5],front:[0,0],side:[1.5,0]};
let yaw, pitch, dist=4.2;
function setView(v){ const a=VIEWS[v]||VIEWS.iso; yaw=a[0]; pitch=a[1]; }
setView(q.get('view')||'iso');

function project(p, w, h){
  const s=Math.sin(yaw), c=Math.cos(yaw);
  let x=p[0]*c - p[1]*s, y=p[0]*s + p[1]*c, z=p[2];
  const sp=Math.sin(pitch), cp=Math.cos(pitch);
  let y2=y*cp - z*sp, z2=y*sp + z*cp;
  const f=1.9/(dist+z2), X=w/2 + x*f*w*0.5, Y=h/2 - y2*f*w*0.5;
  return [X,Y,z2];
}

// ---------- 立方体的 6 个面（顶点索引） ----------
const FACES=[[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[0,3,7,4]];
function cubeVerts(b){
  const hx=b.dx/2*S, hy=b.dy/2*S, hz=b.dz/2*S;
  const X=(b.x-cx)*S, Y=(b.y-cy)*S, Z=(b.z-cz)*S + hz;
  return [[X-hx,Y-hy,Z-hz],[X+hx,Y-hy,Z-hz],[X+hx,Y+hy,Z-hz],[X-hx,Y+hy,Z-hz],
          [X-hx,Y-hy,Z+hz],[X+hx,Y-hy,Z+hz],[X+hx,Y+hy,Z+hz],[X-hx,Y+hy,Z+hz]];
}
function shade(hex,k){
  const n=parseInt(hex.slice(1),16);
  let r=(n>>16)&255,g=(n>>8)&255,b=n&255;
  r=Math.min(255,r*k)|0; g=Math.min(255,g*k)|0; b=Math.min(255,b*k)|0;
  return 'rgb('+r+','+g+','+b+')';
}

function draw(){
  const cv=document.getElementById('c'), w=cv.width, h=cv.height;
  const g=cv.getContext('2d');
  g.fillStyle='#0f1419'; g.fillRect(0,0,w,h);
  const items=[];
  for (const b of BOXES){
    const vs=cubeVerts(b).map(v=>project(v,w,h));
    for (const f of FACES){
      const pts=f.map(i=>vs[i]);
      const zc=pts.reduce((a,p)=>a+p[2],0)/4;
      const nx=(pts[1][1]-pts[0][1])*(pts[2][0]-pts[0][0])-(pts[1][0]-pts[0][0])*(pts[2][1]-pts[0][1]);
      if (nx<=0) continue;                    // 背面剔除
      items.push({z:zc, pts:pts, color:b.color, top:f===FACES[1]||f===FACES[0]});
    }
  }
  items.sort((a,b)=>a.z-b.z);                 // painter's algorithm
  for (const it of items){
    g.beginPath();
    g.moveTo(it.pts[0][0], it.pts[0][1]);
    for (let i=1;i<it.pts.length;i++) g.lineTo(it.pts[i][0], it.pts[i][1]);
    g.closePath();
    g.fillStyle=shade(it.color, it.top?1.0:0.68);
    g.fill();
    g.strokeStyle='rgba(0,0,0,.45)'; g.lineWidth=1; g.stroke();
  }
}

function resize(){
  const cv=document.getElementById('c');
  cv.width=window.innerWidth; cv.height=window.innerHeight; draw();
}

// ---------- 交互 ----------
let drag=null;
const cv=document.getElementById('c');
cv.addEventListener('mousedown',e=>{drag=[e.clientX,e.clientY];cv.classList.add('drag');});
window.addEventListener('mouseup',()=>{drag=null;cv.classList.remove('drag');});
window.addEventListener('mousemove',e=>{
  if(!drag) return;
  yaw += (e.clientX-drag[0])*0.008; pitch += (e.clientY-drag[1])*0.008;
  drag=[e.clientX,e.clientY]; draw();
});
cv.addEventListener('wheel',e=>{e.preventDefault(); dist=Math.max(1.2,Math.min(14,dist+e.deltaY*0.0022)); draw();},{passive:false});
cv.addEventListener('dblclick',()=>{ setView('iso'); dist=4.2; draw(); });
window.addEventListener('resize',resize);

// ---------- HUD / 图例 / 统计 ----------
document.getElementById('meta').textContent =
  META.专业 + ' · ' + META.名称 + ' · 构件 ' + BOXES.length + ' 个';
document.getElementById('caveat').textContent = META.说明;
const counts={};
for (const b of BOXES) counts[b.label]=(counts[b.label]||0)+1;
document.getElementById('legend').innerHTML = Object.keys(counts).map(k=>
  '<div class="row"><span class="k" style="background:'+((BOXES.find(b=>b.label===k)||{}).color||'#888')+'"></span>'
  + k + ' <span class="dim">× ' + counts[k] + '</span></div>').join('');
document.getElementById('stat').innerHTML =
  '<div class="row dim">范围 X ' + (maxx-minx).toFixed(1) + ' · Y ' + (maxy-miny).toFixed(1)
  + ' · Z ' + (maxz-minz).toFixed(1) + '</div><div class="dim">视角 ' + (q.get('view')||'iso') + '</div>';

resize();
</script>
</body>
</html>
'''


def _num(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def _sect(text):
    """'500x500' / [500,500] → (b, h) 单位 mm。"""
    if isinstance(text, (list, tuple)) and len(text) >= 2:
        return _num(text[0], 300.0), _num(text[1], 300.0)
    s = str(text or '').lower().replace('*', 'x').replace('×', 'x')
    for sep in ('x', 'X'):
        if sep in s:
            a = s.split(sep)
            try:
                return float(a[0]), float(a[1])
            except Exception:
                pass
    return 300.0, 300.0


def build_boxes(pid):
    """构件模型 → 立方体列表（x/y/z 单位 m，dx/dy/dz 单位 m）。"""
    boxes, noted = [], []
    cm = pid.get('构件模型') or {}

    # ① 柱：**位置可能是多个坐标点**（同规格多根。实测 KZ-500x550 数量 36 → 位置数组 36 个点）
    for c in (cm.get('柱') or []):
        poss = c.get('位置') or c.get('坐标')
        b, h = _sect(c.get('截面') or c.get('截面尺寸'))
        hh = _num(c.get('高度_m') or c.get('层高_m'), 3.0)
        if isinstance(poss, dict):
            poss = [poss]
        if not isinstance(poss, (list, tuple)) or not poss:
            noted.append('柱(无坐标)')
            continue
        for pt in poss:
            if isinstance(pt, dict):
                x, y = _num(pt.get('x')), _num(pt.get('y'))
            elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                x, y = _num(pt[0]), _num(pt[1])
            else:
                continue
            boxes.append({'x': x / 1000.0, 'y': y / 1000.0, 'z': 0.0,
                          'dx': b / 1000.0, 'dy': h / 1000.0, 'dz': hh,
                          'color': COLORS['柱'], 'label': '柱'})

    # ② 梁：轴线几何段（实测线段）+ 截面
    bg = pid.get('梁几何') or {}
    segs = bg.get('段') or bg.get('segments') or []
    b, h = 300.0, 600.0
    for bm in (cm.get('梁') or []):
        if bm.get('截面'):
            b, h = _sect(bm['截面'])
            break
    for sg in segs:
        try:
            p1, p2 = sg[0], sg[1]
            x1, y1, x2, y2 = _num(p1[0]) / 1000.0, _num(p1[1]) / 1000.0, _num(p2[0]) / 1000.0, _num(p2[1]) / 1000.0
        except Exception:
            continue
        L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if L < 0.3:
            continue
        boxes.append({'x': (x1 + x2) / 2, 'y': (y1 + y2) / 2, 'z': _num(bg.get('标高_m'), 3.0),
                      'dx': abs(x2 - x1) or 0.3, 'dy': abs(y2 - y1) or 0.3,
                      'dz': h / 1000.0, 'color': COLORS['梁'], 'label': '梁'})
    if not segs:
        # v2（P4 修复）：图上无轴线几何时，**按柱网相邻关系推断梁** ——
        # 视觉验证指出"梁完全未建 → 梁板混凝土/钢筋/模板全部漏算"，先用柱网连线让模型可验证。
        cols = [(b['x'], b['y']) for b in boxes if b['label'] == '柱']
        hh = next((b['dz'] for b in boxes if b['label'] == '柱'), 3.0)
        added = 0
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                dx = cols[j][0] - cols[i][0]
                dy = cols[j][1] - cols[i][1]
                d = (dx * dx + dy * dy) ** 0.5
                if 1.0 < d <= 12.0:
                    boxes.append({
                        'x': (cols[i][0] + cols[j][0]) / 2,
                        'y': (cols[i][1] + cols[j][1]) / 2,
                        'z': max(0.0, hh - 0.5),
                        'dx': max(abs(dx), 0.3), 'dy': max(abs(dy), 0.3),
                        'dz': 0.5, 'color': COLORS['梁'],
                        'label': '梁·柱网推断'})
                    added += 1
                    if added >= 300:
                        break
            if added >= 300:
                break
        noted.append(('梁 %d 根（柱网推断，无轴线几何）' % added) if added
                     else '梁(无轴线几何且无柱网可推断, 未绘)')

    # ③ 板/墙/钢构件：无独立坐标 → 示意摆放（界面已标注）
    def _grid(items, label, z0, thickness, size_from, n_max=400):
        n = min(len(items), n_max)
        cols = max(1, int(n ** 0.5) + 1)
        for i, it in enumerate(items[:n]):
            s = size_from(it)
            boxes.append({'x': (i % cols) * (s + 0.4), 'y': (i // cols) * (s + 0.4),
                          'z': z0, 'dx': s, 'dy': s, 'dz': thickness,
                          'color': COLORS[label], 'label': label + '·示意'})
        if len(items) > n:
            noted.append('%s %d 个仅绘前 %d 个' % (label, len(items), n))

    _grid(cm.get('板') or [], '板', 0.05, 0.12,
          lambda it: min(2.5, max(0.8, (_num(it.get('面积_m2'), 9.0)) ** 0.5 / 8.0)))
    _grid(cm.get('墙') or [], '墙', 0.05, 0.24,
          lambda it: min(2.5, max(0.8, (_num(it.get('面积_m2'), 9.0)) ** 0.5 / 8.0)))
    _grid(cm.get('钢构件') or [], '钢构件', 0.05, 0.3,
          lambda it: min(2.5, max(0.4, _num(it.get('长度_m'), 3.0) / 3.0)))

    caveat = ('⚠ 柱/梁按图纸实测坐标摆放（已平移至原点）；板/墙/钢构件图上无独立坐标 → 排在右侧"示意区"。'
              if noted else '柱/梁/板/墙均按图纸坐标摆放。')
    if noted:
        caveat += ' 未绘/降级项：' + '、'.join(sorted(set(noted)))

    # ---- 坐标归一化（关键修正）----
    # 实测坐标是绝对坐标（如 x≈1637000mm = 1637m），若与 0~3m 的示意件混在同一空间，
    # span 会被拉到 1600+ m，导致柱被压成一个点、模型整片黑（实测截图已复现）。
    real = [b for b in boxes if '·示意' not in b['label']]
    fake = [b for b in boxes if '·示意' in b['label']]
    if real:
        ox = min(b['x'] for b in real)
        oy = min(b['y'] for b in real)
        for b in real:
            b['x'] -= ox
            b['y'] -= oy
        fx = max(b['x'] + b['dx'] for b in real) + 5.0
    else:
        fx = 0.0
    for b in fake:
        b['x'] += fx                      # 示意件整体右移，与实测区分开
    return boxes, caveat


def export(pid_path, out_path, title=''):
    pid = json.load(open(pid_path, encoding='utf-8'))
    boxes, caveat = build_boxes(pid)
    name = title or os.path.splitext(os.path.basename(pid_path))[0]
    meta = {
        '名称': name,
        '专业': str(pid.get('专业类型') or '—'),
        '说明': caveat,
    }
    html = (HTML_TMPL.replace('__TITLE__', name)
            .replace('__BOXES__', json.dumps(boxes, ensure_ascii=False))
            .replace('__META__', json.dumps(meta, ensure_ascii=False)))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    by = {}
    for b in boxes:
        by[b['label'].split('·')[0]] = by.get(b['label'].split('·')[0], 0) + 1
    return {'盒体数': len(boxes), '按类型': by, '说明': caveat, '输出': out_path}


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    t = ''
    if '--title' in sys.argv:
        t = sys.argv[sys.argv.index('--title') + 1]
    r = export(args[0], args[1], t)
    print(json.dumps(r, ensure_ascii=False, indent=1))
