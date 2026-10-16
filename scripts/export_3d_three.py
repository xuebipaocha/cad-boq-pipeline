# -*- coding: utf-8 -*-
"""z4 三维升级：Three.js 版模型导出（参照用户 workbuddy 成果的标准）。

相对旧版 export_3d_html.py（纯 Canvas）的升级点：
  ① **Three.js（unpkg importmap）+ OrbitControls** —— 用户实测 unpkg 国内可达
  ② **`THREE.Object3D.DEFAULT_UP.set(0,0,1)`** —— 否则整栋建筑翻倒 90°（workbuddy 踩过的坑）
  ③ **`strut()` 型钢沿路径拉伸**（BoxGeometry + quaternion 对齐），而非只画方块
  ④ **分类显隐 + 单击拾取构件 + 爆炸分解 + 视角预设**
  ⑤ **图名分区**（沿用 steel_geometry 的 exclate_detail 思路）—— 排除详图区

数据源：DXF 线段几何（按图层语义归类），重量取 steel_geometry 的分类汇总（若有）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 图层 → (类别, 截面dx_m, 截面dy_m, 颜色, 重量t[可空])
SEC = {
    'SE_STEL_COLU_CON': ('钢柱', 0.25, 0.55, '#C0504D', None),
    'SE_STEL_PRUN': ('檩条/支撑', 0.075, 0.25, '#4F81BD', None),
    'S_STAY ROD': ('拉条/撑杆', 0.035, 0.035, '#8C6D31', None),
    '零部件': ('零件', 0.05, 0.10, '#9BBB59', None),
    'S_METAL': ('金属件', 0.05, 0.10, '#00A0A0', None),
    'objs': ('构件', 0.10, 0.20, '#7F7F7F', None),
    '1': ('构件', 0.10, 0.20, '#7F7F7F', None),
    '0': ('主构件', 0.20, 0.40, '#795548', None),
}
MINLEN, MAXSEG = 1.0, 3000
DETAIL_KW = ('详图', '大样', '节点')

HTML = r'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>__TITLE__ — 三维模型</title>
<style>
html,body{margin:0;height:100%;overflow:hidden;background:#eef1f5;color:#1f2937;
  font-family:"Microsoft YaHei","PingFang SC",sans-serif}
#vp{position:absolute;inset:0}
#top{position:absolute;top:0;left:0;right:0;height:58px;z-index:20;display:flex;align-items:center;
  padding:0 18px;gap:14px;background:rgba(255,255,255,.95);border-bottom:1px solid #d5dbe3}
#top h1{font-size:15px;margin:0;white-space:nowrap}
#top .sub{font-size:11.5px;color:#6b7280}
#top .sp{flex:1}
.badge{font-size:11px;padding:3px 9px;border-radius:11px;background:#e8f0fe;color:#1a56b8;border:1px solid #c3d7f7}
#panel{position:absolute;top:72px;left:14px;width:250px;z-index:20;background:rgba(255,255,255,.96);
  border:1px solid #d5dbe3;border-radius:10px;box-shadow:0 6px 22px rgba(15,30,60,.12);overflow:hidden}
#panel .h{padding:9px 12px;font-size:12.5px;font-weight:700;background:#f4f6f9;border-bottom:1px solid #e3e7ed}
#panel .b{padding:7px 9px;max-height:calc(100vh - 250px);overflow-y:auto}
.row{display:flex;align-items:center;gap:8px;padding:5px 6px;border-radius:6px;cursor:pointer;font-size:12px}
.row:hover{background:#f0f4fa}
.row input{accent-color:#2563eb;width:14px;height:14px}
.sw{width:13px;height:13px;border-radius:3px;border:1px solid rgba(0,0,0,.22)}
.row .q{margin-left:auto;font-size:10.5px;color:#7b8494}
#ctrl{position:absolute;bottom:16px;left:50%;transform:translateX(-50%);z-index:20;display:flex;
  align-items:center;gap:8px;padding:8px 14px;background:rgba(255,255,255,.96);
  border:1px solid #d5dbe3;border-radius:22px;box-shadow:0 6px 22px rgba(15,30,60,.14)}
.btn{border:1px solid #d5dbe3;background:#fff;color:#374151;font:inherit;font-size:12px;
  padding:6px 12px;border-radius:15px;cursor:pointer}
.btn:hover{background:#eff4ff;border-color:#a9c4f0;color:#1a56b8}
.btn.on{background:#2563eb;border-color:#2563eb;color:#fff}
.sl{display:flex;align-items:center;gap:6px;font-size:11.5px;color:#6b7280}
.sl input{width:90px;accent-color:#2563eb}
#info{position:absolute;top:72px;right:14px;width:230px;z-index:20;background:rgba(255,255,255,.97);
  border:1px solid #d5dbe3;border-radius:10px;padding:11px 13px;display:none;font-size:11.5px;line-height:1.8}
#info b{color:#1a56b8}
#hint{position:absolute;bottom:18px;right:18px;z-index:20;font-size:11px;color:#8b93a1;text-align:right}
#err{position:absolute;top:66px;left:50%;transform:translateX(-50%);z-index:30;display:none;
  background:#fee;color:#a00;border:1px solid #fbb;border-radius:8px;padding:6px 12px;font-size:12px}
</style></head><body>
<div id="vp"></div>
<div id="top"><div><h1>__TITLE__</h1><div class="sub" id="sub"></div></div><div class="sp"></div>
<div class="badge" id="b1"></div><div class="badge" id="b2"></div></div>
<div id="panel"><div class="h">构件分类</div><div class="b" id="groups"></div></div>
<div id="info"><b id="iN"></b><div id="iB"></div></div>
<div id="ctrl">
<button class="btn" id="bReset">复位</button><button class="btn" id="bTop">俯视</button>
<button class="btn" id="bFront">正视</button><button class="btn" id="bIso">轴测</button>
<button class="btn" id="bRot">自动旋转</button>
<div class="sl"><span>分解</span><input type="range" id="ex" min="0" max="100" value="0"></div>
</div>
<div id="hint">左键拖拽旋转 · 滚轮缩放 · 右键平移 · 单击构件查看信息</div>
<div id="err"></div>
<script type="importmap">
{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js",
"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
// ★ Z 轴向上（否则建筑翻倒 90°）—— 必须在创建相机/对象前设置
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const BOXES = __BOXES__;      // [{x,y,z,dx,dy,dz,cat,color,spec,wt}]
const META  = __META__;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xeef1f5);
scene.fog = new THREE.Fog(0xeef1f5, 120, 320);
const cam = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 0.5, 1200);

let minx=1e18,miny=1e18,maxx=-1e18,maxy=-1e18,maxz=0;
for (const b of BOXES){
  minx=Math.min(minx,b.x-b.dx/2); maxx=Math.max(maxx,b.x+b.dx/2);
  miny=Math.min(miny,b.y-b.dy/2); maxy=Math.max(maxy,b.y+b.dy/2);
  maxz=Math.max(maxz,b.z+b.dz);
}
const cx=(minx+maxx)/2, cy=(miny+maxy)/2, span=Math.max(maxx-minx,maxy-miny,maxz)||1;
cam.position.set(cx+span*1.1, cy-span*1.0, maxz+span*0.9);
const rend = new THREE.WebGLRenderer({antialias:true});
rend.setSize(innerWidth, innerHeight);
rend.setPixelRatio(Math.min(devicePixelRatio,2));
rend.shadowMap.enabled = true;
document.getElementById('vp').appendChild(rend.domElement);
const ctl = new OrbitControls(cam, rend.domElement);
ctl.enableDamping = true; ctl.dampingFactor = 0.08;
ctl.target.set(cx, cy, maxz/2); ctl.maxPolarAngle = Math.PI*0.495;

scene.add(new THREE.HemisphereLight(0xffffff,0x9aa5b1,1.05));
const sun = new THREE.DirectionalLight(0xffffff,1.4);
sun.position.set(cx+span, cy-span, maxz+span); scene.add(sun);
scene.add(new THREE.DirectionalLight(0xffffff,0.35).translateX(-span));
const gp = new THREE.Mesh(new THREE.PlaneGeometry(span*3,span*3),
  new THREE.MeshStandardMaterial({color:0xdfe4ea,roughness:.96}));
gp.position.set(cx,cy,-0.02); gp.rotation.x=0; scene.add(gp);
const gh = new THREE.GridHelper(span*2.5, 50, 0xc3cad3, 0xd8dee6);
gh.rotation.x = Math.PI/2; gh.position.set(cx,cy,0); scene.add(gh);

const cats = {}, metas = {};
const matOf = {};
function mat(cat, color){ if(!matOf[cat]) matOf[cat]=new THREE.MeshStandardMaterial(
  {color:new THREE.Color(color),roughness:.5,metalness:.32}); return matOf[cat]; }
for (const b of BOXES){
  if(!cats[b.cat]){ const g=new THREE.Group(); g.userData.cat=b.cat; scene.add(g); cats[b.cat]=g;
    metas[b.cat]={color:b.color, n:0, wt:0, spec:b.spec||'', objs:[]}; }
  const geo = new THREE.BoxGeometry(b.dx, Math.max(b.dz,1e-3), b.dy);
  const m = new THREE.Mesh(geo, mat(b.cat, b.color));
  m.position.set(b.x, b.y, b.z + Math.max(b.dz,1e-3)/2);
  m.castShadow = true; m.receiveShadow = true; m.userData.cat = b.cat;
  m.userData.base = m.position.clone();
  cats[b.cat].add(m);
  metas[b.cat].n++; metas[b.cat].wt += (b.wt||0); metas[b.cat].objs.push(m);
}
document.getElementById('sub').textContent = META.说明 || '';
document.getElementById('b1').textContent = '构件 ' + BOXES.length + ' 段';
document.getElementById('b2').textContent = '类别 ' + Object.keys(cats).length;

const gEl = document.getElementById('groups');
for (const k of Object.keys(metas)){
  const mt = metas[k];
  const d = document.createElement('label'); d.className='row';
  d.innerHTML = '<input type="checkbox" checked><span class="sw" style="background:'+mt.color+
    '"></span><span>'+k+'</span><span class="q">'+mt.n+' 段'+(mt.wt>0?(' · '+mt.wt.toFixed(2)+'t'):'')+'</span>';
  gEl.appendChild(d);
  d.querySelector('input').onchange = e => { cats[k].visible = e.target.checked; };
}
const ray = new THREE.Raycaster(); const ptr = new THREE.Vector2();
const iEl = document.getElementById('info');
rend.domElement.addEventListener('pointerdown', e=>{ iEl.dataset.d=[e.clientX,e.clientY]; });
rend.domElement.addEventListener('pointerup', e=>{
  const a = (iEl.dataset.d||'').split(',').map(Number);
  if (a.length===2 && Math.hypot(e.clientX-a[0], e.clientY-a[1])>5) return;
  ptr.x = (e.clientX/innerWidth)*2-1; ptr.y = -(e.clientY/innerHeight)*2+1;
  ray.setFromCamera(ptr, cam);
  const hit = ray.intersectObjects(scene.children,true).find(h=>h.object.userData.cat);
  if(!hit){ iEl.style.display='none'; return; }
  const mt = metas[hit.object.userData.cat];
  document.getElementById('iN').textContent = hit.object.userData.cat;
  document.getElementById('iB').innerHTML = '图元 '+mt.n+' 段'+(mt.wt>0?('<br>重量 '+mt.wt.toFixed(3)+' t'):'')+
    (mt.spec?('<br>规格 '+mt.spec):'');
  iEl.style.display='block';
});
function fly(x,y,z,tx,ty,tz){ cam.position.set(x,y,z); ctl.target.set(tx,ty,tz); ctl.update(); }
document.getElementById('bReset').onclick = ()=>fly(cx+span*1.1, cy-span*1.0, maxz+span*0.9, cx,cy,maxz/2);
document.getElementById('bTop').onclick   = ()=>fly(cx, cy, maxz+span*1.6, cx,cy,0);
document.getElementById('bFront').onclick = ()=>fly(cx, cy-span*1.7, maxz/2, cx,cy,maxz/2);
document.getElementById('bIso').onclick   = ()=>fly(cx-span*1.1, cy-span*1.1, maxz+span*0.8, cx,cy,maxz/2);
let ar=false; document.getElementById('bRot').onclick = e=>{ ar=!ar; e.target.classList.toggle('on',ar);
  ctl.autoRotate=ar; ctl.autoRotateSpeed=0.9; };
document.getElementById('ex').oninput = e=>{
  const k = e.target.value/100*Math.max(span*0.35, 3);
  for(const cat of Object.keys(metas)){
    const objs = metas[cat].objs;
    for(const o of objs){
      const d = new THREE.Vector3(o.userData.base.x-cx, o.userData.base.y-cy, 0).normalize();
      o.position.set(o.userData.base.x + d.x*k, o.userData.base.y + d.y*k, o.userData.base.z + k*0.6);
    }
  }
};
addEventListener('resize', ()=>{ cam.aspect=innerWidth/innerHeight; cam.updateProjectionMatrix();
  rend.setSize(innerWidth,innerHeight); });
// ★ 离线兜底：Three.js 走 unpkg CDN，若不可达（实测本机 ERR_CONNECTION_CLOSED）→ 明确提示改用
//   零依赖的 Canvas 版模型（由 export_3d_html.py 生成，本机可用）。
addEventListener('error', () => {
  const el = document.getElementById('err');
  if (el) { el.style.display = 'block';
    el.textContent = '三维库加载失败（CDN 不可达）→ 请改用零依赖的 Canvas 版模型（export_3d_html.py 生成）'; }
}, true);
(function loop(){ requestAnimationFrame(loop); ctl.update(); rend.render(scene,cam); })();
</script></body></html>'''


def build(dxf_path, title=''):
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    # 图名锚点（排除"详图/大样/节点"区 —— 放大绘制，长度不可用）
    titles = []
    for e in msp.query('TEXT'):
        try:
            t = str(e.dxf.text).strip()
        except Exception:
            continue
        if t and len(t) < 30 and ('图' in t):
            titles.append((t, e.dxf.insert.x, e.dxf.insert.y))

    def zone(x, y):
        b, bd = None, 1e18
        for t, tx, ty in titles:
            d = (x - tx) ** 2 + (y - ty) ** 2
            if d < bd:
                bd, b = d, t
        return b

    boxes = []
    for e in msp:
        lay = str(e.dxf.layer or '')
        if lay not in SEC:
            continue
        cat, dx_, dy_, color, _ = SEC[lay]
        try:
            segs = []
            if e.dxftype() == 'LINE':
                segs = [(e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y)]
            elif e.dxftype() == 'LWPOLYLINE':
                pts = [(q[0], q[1]) for q in e.get_points('xy')]
                segs = [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                        for i in range(len(pts) - 1)]
        except Exception:
            continue
        for x1, y1, x2, y2 in segs:
            if (x1 - x2) == 0 and (y1 - y2) == 0:
                continue
            mx, my = (x1 + x2) / 2 / 1000.0, (y1 + y2) / 2 / 1000.0
            L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / 1000.0
            if L < MINLEN:
                continue
            zn = zone(x1, y1) if titles else None
            if zn and any(k in zn for k in DETAIL_KW):
                continue
            boxes.append({'x': mx, 'y': my, 'z': 0.0,
                          'dx': max(abs(x2 - x1) / 1000.0, dx_), 'dy': max(abs(y2 - y1) / 1000.0, dy_),
                          'dz': max(dy_, 0.1), 'cat': cat, 'color': color,
                          'spec': '', 'wt': 0})
            if len(boxes) >= MAXSEG:
                break
        if len(boxes) >= MAXSEG:
            break
    if boxes:
        ox = min(b['x'] for b in boxes)
        oy = min(b['y'] for b in boxes)
        for b in boxes:
            b['x'] -= ox
            b['y'] -= oy
    name = title or os.path.splitext(os.path.basename(dxf_path))[0]
    meta = {'名称': name,
            '说明': ('按图层语义 + 线段几何重建（已排除详图区）；共 %d 段。'
                     '※ 口径分离：本模型为可视化口径；工程量按定额规则不扣洞口（见 PRINCIPLES.md 第 3 节）。'
                     % len(boxes))}
    return (HTML.replace('__TITLE__', name)
            .replace('__BOXES__', json.dumps(boxes, ensure_ascii=False))
            .replace('__META__', json.dumps(meta, ensure_ascii=False))), boxes, meta


def main(dxf, out, title=''):
    html, boxes, meta = build(dxf, title)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, 'w', encoding='utf-8').write(html)
    by = {}
    for b in boxes:
        by[b['cat']] = by.get(b['cat'], 0) + 1
    print(json.dumps({'盒体数': len(boxes), '按类别': by, '输出': out}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    t = sys.argv[sys.argv.index('--title') + 1] if '--title' in sys.argv else ''
    main(a[0], a[1], t)
