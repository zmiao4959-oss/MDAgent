# 可视化系统

## 组件

```
viz/
├── auto.py           # 自动可视化调度（工具执行后检测新文件）
├── ovito_render.py   # OVITO 子进程渲染（multiprocessing）
├── snapshot.py       # 工作区文件快照 + diff
└── constants.py      # 扩展名常量、OVITO 参数
```

## 自动可视化流程

```
Agent 工具轮次结束
  ├─ snap_before = snapshot_workspace()     # 工作区文件快照（mtime + size）
  ├─ 执行工具（如 LAMMPS 模拟）
  ├─ snap_after = snapshot_workspace()      # 再次快照
  └─ auto_viz.after_tool_round(snap_before, snap_after)
       ├─ diff_snapshots(before, after)     # 找出新增/变更文件
       ├─ _classify_new_file(path)           # 分类：structure / csv / media
       └─ 按类型处理:
            ├─ structure (.xyz .dump .lammpstrj .lmp .data)
            │    └─ asyncio.create_task(_render_structure(path))
            │         ├─ _emit({event: "status"})              → SSE: "OVITO 渲染中…"
            │         ├─ asyncio.to_thread(render_ovito_subprocess)  # 子进程渲染
            │         ├─ _mark_viz_done(session, gif_path)     # 记入 session metadata
            │         └─ _emit_media("gif", gif_path)          → SSE: GIF 路径
            ├─ csv
            │    └─ _emit_media("csv", path)                   → 应力应变曲线
            └─ media (.gif .png .jpg .jpeg)
                 └─ _emit_media(media_type, path)              → 图片展示
```

## OVITO 渲染 (`ovito_render.py`)

### 调用链

```
_render_structure(path)
  └─ asyncio.to_thread(render_ovito_subprocess, struct_path)
       └─ mp.Process(target=_run_ovito_worker)      # 子进程（规避 GIL + OVITO 内存泄漏）
            └─ _run_ovito_in_process(work_dir, name)
                 ├─ from ovito.io import import_file
                 ├─ 如果是 LAMMPS dump:
                 │    ├─ 复制到 temp_first_frame_overwritten.lammpstrj
                 │    ├─ 重排帧：最后一帧 → 第一帧（展示最终构型）
                 │    └─ 可选 CNA modifier（晶粒着色）
                 ├─ Viewport + TachyonRenderer
                 └─ viewport.render_anim() → .gif
```

### 关键参数 (`constants.py`)

```python
OVITO_GIF_SIZE = (500, 500)
OVITO_GIF_FPS = 30
OVITO_BACKGROUND = (45/255, 45/255, 45/255)   # 深灰背景
OVITO_CAMERA_DIR = (1, -2.5, -1)
OVITO_CAMERA_POS = (1, 1, 1)
OVITO_DUMP_TEMP_NAME = "temp_first_frame_overwritten.lammpstrj"
OVITO_APPLY_CNA_ON_DUMP = True                 # 对 dump 文件做 CNA 分析
```

### 依赖

- `ovito` Python 包（`pip install ovito`）
- OVITO 需要有效的 license（学术免费）
- 如果未安装，`render_ovito_subprocess` 返回 `None`，`_render_structure` 发 error 事件

## 3D 结构渲染（前端）

**文件**: `app.js` 函数 `createStructureScene(container, atoms)` (~941 行)

使用 Three.js InstancedMesh 渲染原子结构：

```js
const sphereGeo = new THREE.SphereGeometry(1, 14, 14);
const mesh = new THREE.InstancedMesh(sphereGeo, material, atomOffsets.length);
// 每种原子类型用不同颜色（TYPE_COLORS 数组）
// 旋转动画 + IntersectionObserver 暂停
```

支持的结构文件格式：XYZ、LAMMPS dump (ITEM:)、LAMMPS data (Atoms)、XSF (XCrySDen)

解析函数：`parseStructureFile` / `parseStandardXyz` / `parseDumpFirstFrame` / `parseLammpsData` / `parseXsf`

## viz_done 机制

服务端 `session.metadata["viz_done"]` 存储已处理的可视化文件路径列表（绝对路径）。用途：
- 避免同一文件重复渲染
- SSE 断开后客户端可通过 `/api/history` 的 `viz_done` 字段恢复遗漏的 GIF

相关函数：
- `_viz_done_set(session)` — 读取
- `_mark_viz_done(session, path)` — 追加
- `after_tool_round` 末尾 `session.metadata["viz_done"] = sorted(done)` — 持久化
