(function () {
  const CHAT_STORAGE_KEY = "miniclaw_webchat_chat_id";
  const VIZ_STORAGE_PREFIX = "miniclaw_viz_";
  const SIDEBAR_COLLAPSED_KEY = "miniclaw_sidebar_collapsed";

  const msgs = document.getElementById("messages");
  const convList = document.getElementById("conv-list");
  const inp = document.getElementById("input");
  const sendBtn = document.getElementById("btn-send");
  const sidebar = document.getElementById("sidebar");
  const fileSelect = document.getElementById("file-select");
  const mediaGallery = document.getElementById("media-gallery");
  const structureGallery = document.getElementById("structure-gallery");
  const chatProgress = document.getElementById("chat-progress");
  const chatStatusText = document.getElementById("chat-status-text");
  const vizProgress = document.getElementById("viz-progress");
  const vizStatusText = document.getElementById("viz-status-text");
  const mediaCountEl = document.getElementById("media-count");
  const structureCountEl = document.getElementById("structure-count");
  const browsePathEl = document.getElementById("browse-path");
  const browseUpBtn = document.getElementById("browse-up");
  const atomRadiusSlider = document.getElementById("atom-radius-slider");
  const atomRadiusValue = document.getElementById("atom-radius-value");

  let currentChatId = localStorage.getItem(CHAT_STORAGE_KEY) || "webchat:default";
  let chatRunning = false;
  let mediaCount = 0;
  let structureCount = 0;
  let workspaceRoot = "";
  let browseDir = "";
  let atomRadiusTouched = false;
  let atomPointSize = 0.3;
  const structureScenes = [];
  const savedMedia = [];
  const savedStructures = [];

  let SKIP_BROWSE_DIRS = new Set([".idea", "__pycache__", ".git", "web", "node_modules", ".venv", "sessions"]);
  const STRUCTURE_EXTS = new Set(["xyz", "dump", "lammpstrj", "lmp", "data", "xsf"]);
  let VISUAL_FILE_EXTS = new Set([
    "gif", "png", "jpg", "jpeg", "xyz", "dump", "lammpstrj", "lmp", "data", "csv",
  ]);

  function vizStorageKey() {
    return VIZ_STORAGE_PREFIX + currentChatId;
  }

  function saveVizState() {
    localStorage.setItem(
      vizStorageKey(),
      JSON.stringify({ media: savedMedia, structures: savedStructures })
    );
  }

  function assetUrl(path) {
    return `/api/asset?path=${encodeURIComponent(path)}`;
  }

  function addMsg(role, text) {
    const d = document.createElement("div");
    d.className = role;
    d.textContent = text;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function setChatProgress(active, text) {
    chatRunning = active;
    chatProgress.classList.toggle("active", active);
    chatStatusText.textContent = text || (active ? "Agent 运行中…" : "就绪");
    sendBtn.disabled = active;
    sendBtn.textContent = active ? "运行中…" : "发送";
  }

  function setVizProgress(active, text) {
    vizProgress.classList.toggle("active", active);
    if (text) vizStatusText.textContent = text;
    else vizStatusText.textContent = active ? "可视化处理中…" : "无渲染任务";
  }

  function updateBadge(el, count) {
    el.textContent = String(count);
  }

  function removePlaceholder(container) {
    const ph = container.querySelector(".placeholder");
    if (ph) ph.remove();
  }

  function clearGalleries() {
    mediaGallery.innerHTML = '<p class="placeholder">Agent 生成的 GIF / 曲线图将显示在这里</p>';
    structureGallery.innerHTML = '<p class="placeholder">手动加载或 Agent 输出的结构将保留在此</p>';
    structureScenes.forEach((s) => s.dispose?.());
    structureScenes.length = 0;
    savedMedia.length = 0;
    savedStructures.length = 0;
    mediaCount = 0;
    structureCount = 0;
    updateBadge(mediaCountEl, 0);
    updateBadge(structureCountEl, 0);
  }

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    document.querySelectorAll(".tab-pane").forEach((p) => {
      p.classList.toggle("active", p.id === `tab-${name}`);
    });
    if (name === "structure") structureScenes.forEach((s) => s.onResize?.());
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  function isVisualFileEntry(entry) {
    if (entry.is_dir) return false;
    if (entry.visual) return true;
    const name = entry.name.toLowerCase();
    if (name.endsWith(".csv")) return true;
    const ext = (entry.ext || name.split(".").pop() || "").replace(/^\./, "");
    return VISUAL_FILE_EXTS.has(ext);
  }

  async function loadConfig() {
    try {
      const res = await fetch("/api/config");
      if (!res.ok) return;
      const cfg = await res.json();
      workspaceRoot = cfg.workspace_root || "";
      browseDir = workspaceRoot;
      if (cfg.skip_dir_names) SKIP_BROWSE_DIRS = new Set(cfg.skip_dir_names);
      if (cfg.visual_ext) {
        VISUAL_FILE_EXTS = new Set(cfg.visual_ext.map((e) => e.replace(/^\./, "")));
      }
      await refreshFileList();
    } catch (e) {
      console.error(e);
    }
  }

  function updateBrowseUI(data) {
    browseDir = data.work_dir;
    const label = data.rel_path === "." ? "工作区根目录" : data.rel_path;
    browsePathEl.textContent = label;
    browsePathEl.title = data.work_dir;
    browseUpBtn.disabled = !data.parent_dir;
  }

  function populateFileSelect(data) {
    fileSelect.innerHTML = '<option value="">— 进入子文件夹或选择文件 —</option>';
    if (data.parent_dir) {
      const up = document.createElement("option");
      up.value = "__up__";
      up.textContent = "📁 .. 上级目录";
      fileSelect.appendChild(up);
    }
    const dirs = data.entries.filter(
      (e) => e.is_dir && !SKIP_BROWSE_DIRS.has(e.name) && !e.name.startsWith(".")
    );
    const files = data.entries.filter(isVisualFileEntry);
    for (const dir of dirs) {
      const opt = document.createElement("option");
      opt.value = `__dir__:${dir.path}`;
      opt.textContent = `📁 ${dir.name}/`;
      fileSelect.appendChild(opt);
    }
    if (dirs.length && files.length) {
      const sep = document.createElement("option");
      sep.disabled = true;
      sep.textContent = "──────── 文件 ────────";
      fileSelect.appendChild(sep);
    }
    for (const file of files) {
      const opt = document.createElement("option");
      opt.value = file.path;
      opt.textContent = file.name.toLowerCase().endsWith(".csv") ? `📊 ${file.name}` : file.name;
      fileSelect.appendChild(opt);
    }
  }

  async function refreshFileList() {
    const target = browseDir || workspaceRoot;
    if (!target) return;
    const res = await fetch(`/api/files?work_dir=${encodeURIComponent(target)}`);
    if (!res.ok) return;
    const data = await res.json();
    updateBrowseUI(data);
    populateFileSelect(data);
  }

  function onFileSelectChange() {
    const value = fileSelect.value;
    if (value === "__up__") {
      fileSelect.value = "";
      navigateBrowseUp();
      return;
    }
    if (value.startsWith("__dir__:")) {
      browseDir = value.slice("__dir__:".length);
      fileSelect.value = "";
      refreshFileList();
    }
  }

  async function navigateBrowseUp() {
    const res = await fetch(`/api/files?work_dir=${encodeURIComponent(browseDir || workspaceRoot)}`);
    const data = await res.json();
    if (data.parent_dir) {
      browseDir = data.parent_dir;
      await refreshFileList();
    }
  }

  function appendMedia(mediaType, filePath, persist = true) {
    removePlaceholder(mediaGallery);
    setVizProgress(true, "加载媒体…");
    const card = document.createElement("article");
    card.className = "media-card";
    const label = mediaType === "gif" ? "OVITO 动画" : mediaType === "csv" ? "CSV 曲线" : "图片";
    const time = new Date().toLocaleTimeString();
    card.innerHTML = `
      <div class="media-card-head"><span>${label}</span><time>${time}</time></div>
      <p class="media-caption">${filePath}</p>`;
    const img = document.createElement("img");
    img.alt = label;
    img.src = assetUrl(filePath);
    img.onload = () => setVizProgress(false);
    img.onerror = () => setVizProgress(false, "媒体加载失败");
    card.appendChild(img);
    mediaGallery.appendChild(card);
    mediaGallery.scrollTop = mediaGallery.scrollHeight;
    mediaCount += 1;
    updateBadge(mediaCountEl, mediaCount);
    switchTab("media");
    if (persist) {
      savedMedia.push({ mediaType, path: filePath });
      saveVizState();
    }
  }

  function parseCsvPlotData(text) {
    const xs = [];
    const ys = [];
    for (const line of text.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const parts = t.split(",");
      if (parts.length < 2) continue;
      const x = parseFloat(parts[0]);
      const y = parseFloat(parts[1]);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        xs.push(x);
        ys.push(y);
      }
    }
    return { xs, ys };
  }

  function drawStressStrainOnCanvas(canvas, xs, ys) {
    const ctx = canvas.getContext("2d");
    const w = 640;
    const h = 360;
    canvas.width = w;
    canvas.height = h;
    const pad = { l: 52, r: 18, t: 28, b: 42 };
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    let xMin = Math.min(...xs);
    let xMax = Math.max(...xs);
    let yMin = Math.min(...ys);
    let yMax = Math.max(...ys);
    if (xMin === xMax) { xMin -= 0.01; xMax += 0.01; }
    if (yMin === yMax) { yMin -= 0.01; yMax += 0.01; }
    const toX = (v) => pad.l + ((v - xMin) / (xMax - xMin)) * plotW;
    const toY = (v) => pad.t + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
    ctx.fillStyle = "#141414";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "#3d3d3d";
    ctx.beginPath();
    ctx.moveTo(pad.l, pad.t);
    ctx.lineTo(pad.l, pad.t + plotH);
    ctx.lineTo(pad.l + plotW, pad.t + plotH);
    ctx.stroke();
    ctx.strokeStyle = "#00d4ff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    xs.forEach((x, i) => {
      const px = toX(x);
      const py = toY(ys[i]);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }

  async function appendCsvChart(filePath, persist = true) {
    removePlaceholder(mediaGallery);
    setVizProgress(true, "解析 CSV…");
    try {
      const text = await (await fetch(assetUrl(filePath))).text();
      const { xs, ys } = parseCsvPlotData(text);
      if (xs.length < 2) {
        setVizProgress(false, "CSV 无有效数据");
        return;
      }
      const card = document.createElement("article");
      card.className = "media-card";
      const time = new Date().toLocaleTimeString();
      card.innerHTML = `
        <div class="media-card-head"><span>应力应变曲线 (CSV)</span><time>${time}</time></div>
        <p class="media-caption">${filePath}</p>`;
      const canvas = document.createElement("canvas");
      canvas.className = "csv-chart-canvas";
      drawStressStrainOnCanvas(canvas, xs, ys);
      card.appendChild(canvas);
      mediaGallery.appendChild(card);
      mediaGallery.scrollTop = mediaGallery.scrollHeight;
      mediaCount += 1;
      updateBadge(mediaCountEl, mediaCount);
      switchTab("media");
      setVizProgress(false);
      if (persist) {
        savedMedia.push({ mediaType: "csv", path: filePath });
        saveVizState();
      }
    } catch {
      setVizProgress(false, "CSV 加载失败");
    }
  }

  function parseStructureFile(text, ext) {
    const head = text.trimStart();
    // LAMMPS dump（含误命名为 .xyz/.lmp/.data 的轨迹）
    if (head.startsWith("ITEM:")) {
      return parseDumpFirstFrame(text);
    }
    // XSF（XCrySDen）格式
    if (ext === "xsf" || text.includes("PRIMCOORD")) {
      return parseXsf(text);
    }
    // LAMMPS data（.lmp / .data / 含 Atoms 段的 .xyz）
    if (text.includes("Atoms")) {
      return parseLammpsData(text);
    }
    // 标准 XYZ / OVITO 导出
    if (STRUCTURE_EXTS.has(ext)) {
      const std = parseStandardXyz(text);
      if (std.length) return std;
      return parseLammpsData(text);
    }
    return parseDumpFirstFrame(text);
  }

  function parseStandardXyz(text) {
    const lines = text.split(/\r?\n/);
    if (lines.length < 3) return [];
    const n = parseInt(lines[0].trim(), 10);
    if (!Number.isFinite(n) || n <= 0) return [];

    const atoms = [];
    const elemTypes = new Map();
    let nextType = 1;

    for (let i = 2; i < lines.length && atoms.length < n; i++) {
      const t = lines[i].trim();
      if (!t || t.startsWith("#")) continue;
      const parts = t.split(/\s+/);
      if (parts.length < 4) continue;
      const x = parseFloat(parts[1]);
      const y = parseFloat(parts[2]);
      const z = parseFloat(parts[3]);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      const elem = parts[0];
      if (!elemTypes.has(elem)) elemTypes.set(elem, nextType++);
      atoms.push({ x, y, z, type: elemTypes.get(elem) });
    }
    return atoms;
  }

  function parseLammpsData(text) {
    const lines = text.split(/\r?\n/);
    const atoms = [];
    let inAtoms = false;
    // column indices: will be set from header or inferred from column count
    let xIdx = 2, yIdx = 3, zIdx = 4, typeIdx = 1;  // default: atomic style (ID type x y z)
    for (const line of lines) {
      const t = line.trim();
      if (t.startsWith("Atoms")) {
        inAtoms = true;
        // Try to parse column layout from header: "Atoms # full style (ID mol type q x y z)"
        const parenMatch = t.match(/\(([^)]+)\)/);
        if (parenMatch) {
          const colNames = parenMatch[1].trim().split(/\s+/);
          const map = {};
          for (let i = 0; i < colNames.length; i++) {
            map[colNames[i].toLowerCase()] = i;
          }
          if (map.x !== undefined) xIdx = map.x;
          else if (map.xs !== undefined) xIdx = map.xs;  // scaled coordinates
          if (map.y !== undefined) yIdx = map.y;
          else if (map.ys !== undefined) yIdx = map.ys;
          if (map.z !== undefined) zIdx = map.z;
          else if (map.zs !== undefined) zIdx = map.zs;
          if (map.type !== undefined) typeIdx = map.type;
        }
        continue;
      }
      if (!inAtoms || !t || t.startsWith("#")) continue;
      const parts = t.split(/\s+/);
      // Infer style from column count if no header info was parsed
      if (xIdx === 2 && yIdx === 3 && zIdx === 4 && typeIdx === 1 && parts.length >= 7) {
        // looks like full style without header: ID mol type q x y z
        xIdx = 4; yIdx = 5; zIdx = 6; typeIdx = 2;
      }
      if (parts.length <= Math.max(xIdx, yIdx, zIdx, typeIdx)) continue;
      const x = parseFloat(parts[xIdx]);
      const y = parseFloat(parts[yIdx]);
      const z = parseFloat(parts[zIdx]);
      const type = parseInt(parts[typeIdx], 10);
      if (Number.isFinite(x)) atoms.push({ x, y, z, type: type || 1 });
    }
    return atoms;
  }

  function parseDumpFirstFrame(text) {
    const lines = text.split(/\r?\n/);
    let numAtoms = 0;
    let box = null;
    let columns = [];
    let atomStart = -1;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (line.startsWith("ITEM: NUMBER OF ATOMS")) numAtoms = parseInt(lines[i + 1]?.trim(), 10);
      if (line.startsWith("ITEM: BOX BOUNDS")) {
        let j = i + 1;
        while (j < lines.length && !lines[j].trim()) j++;
        const readBounds = () => {
          const parts = lines[j].trim().split(/\s+/).map(Number);
          j++;
          return parts;
        };
        const xb = readBounds();
        const yb = readBounds();
        const zb = readBounds();
        box = { xlo: xb[0], xhi: xb[1], ylo: yb[0], yhi: yb[1], zlo: zb[0], zhi: zb[1] };
        i = j - 1;
      }
      if (line.startsWith("ITEM: ATOMS")) {
        columns = line.replace(/^ITEM:\s*ATOMS\s*/i, "").trim().split(/\s+/);
        atomStart = i + 1;
        break;
      }
    }
    if (!columns.length || atomStart < 0) return [];
    const col = {};
    columns.forEach((name, idx) => { col[name] = idx; });
    const xKey = col.xs !== undefined ? "xs" : "x";
    const yKey = col.ys !== undefined ? "ys" : "y";
    const zKey = col.zs !== undefined ? "zs" : "z";
    const scaled = xKey === "xs";
    function toCartesian(parts, key, lo, hi) {
      const v = parseFloat(parts[col[key]]);
      if (!Number.isFinite(v)) return NaN;
      return scaled ? lo + v * (hi - lo) : v;
    }
    const xlo = box?.xlo ?? 0, xhi = box?.xhi ?? 1;
    const ylo = box?.ylo ?? 0, yhi = box?.yhi ?? 1;
    const zlo = box?.zlo ?? 0, zhi = box?.zhi ?? 1;
    const atoms = [];
    for (let i = atomStart; i < lines.length && atoms.length < numAtoms; i++) {
      const t = lines[i].trim();
      if (!t || t.startsWith("ITEM:")) break;
      const parts = t.split(/\s+/);
      const x = toCartesian(parts, xKey, xlo, xhi);
      const y = toCartesian(parts, yKey, ylo, yhi);
      const z = toCartesian(parts, zKey, zlo, zhi);
      if (!Number.isFinite(x)) continue;
      const type = col.type !== undefined ? parseInt(parts[col.type], 10) || 1 : 1;
      atoms.push({ x, y, z, type });
    }
    return atoms;
  }

  function parseXsf(text) {
    // XCrySDen Structure File (.xsf)
    // Supports CRYSTAL + PRIMCOORD blocks; for animations (ANIMSTEPS) only the first frame is read.
    const lines = text.split(/\r?\n/);
    const atoms = [];
    const elemTypes = new Map();
    let nextType = 1;
    let inPrimCoord = false;
    let atomsToRead = 0;
    let atomsRead = 0;

    for (const line of lines) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;

      // Match PRIMCOORD or PRIMCOORD <n> (animation frame)
      if (/^PRIMCOORD/i.test(t)) {
        inPrimCoord = true;
        atomsToRead = 0;
        atomsRead = 0;
        continue;
      }

      if (!inPrimCoord) continue;

      // First non-empty line after PRIMCOORD: "natoms flag"
      if (atomsToRead === 0) {
        const parts = t.split(/\s+/);
        atomsToRead = parseInt(parts[0], 10);
        if (!Number.isFinite(atomsToRead) || atomsToRead <= 0) break;
        continue;
      }

      // Atom line: "elem x y z [fx fy fz]"
      const parts = t.split(/\s+/);
      if (parts.length < 4) continue;
      // Skip if first token is a number (could be next PRIMCOORD header line)
      if (/^\d+$/.test(parts[0]) && parts.length <= 2) {
        // This is a new PRIMCOORD header for animation - stop
        break;
      }
      const x = parseFloat(parts[1]);
      const y = parseFloat(parts[2]);
      const z = parseFloat(parts[3]);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      const elem = parts[0];
      if (!elemTypes.has(elem)) elemTypes.set(elem, nextType++);
      atoms.push({ x, y, z, type: elemTypes.get(elem) });
      atomsRead++;
      if (atomsRead >= atomsToRead) break;
    }

    return atoms;
  }

  const TYPE_COLORS = [0x00d4ff, 0xe74c3c, 0x2ecc71, 0xf39c12, 0x9b59b6, 0x1abc9c, 0xe67e22, 0x3498db];

  function computeStructureMetrics(atoms) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const a of atoms) {
      minX = Math.min(minX, a.x); maxX = Math.max(maxX, a.x);
      minY = Math.min(minY, a.y); maxY = Math.max(maxY, a.y);
      minZ = Math.min(minZ, a.z); maxZ = Math.max(maxZ, a.z);
    }
    const extent = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 1e-6);
    return {
      extent,
      baseSize: Math.max(0.08, extent / 100),
      cx: (minX + maxX) / 2,
      cy: (minY + maxY) / 2,
      cz: (minZ + maxZ) / 2,
    };
  }

  function createStructureScene(container, atoms) {
    const width = container.clientWidth || 400;
    const height = container.clientHeight || 280;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x141414);
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(2, 3, 4);
    scene.add(dirLight);
    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 10000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    const { extent, baseSize, cx, cy, cz } = computeStructureMetrics(atoms);
    const atomOffsets = atoms.map((a) => ({ x: a.x - cx, y: a.y - cy, z: a.z - cz, type: a.type }));
    const maxR = extent / 2;
    const radius = atomRadiusTouched ? atomPointSize : baseSize;
    const sphereGeo = new THREE.SphereGeometry(1, 14, 14);
    const material = new THREE.MeshPhongMaterial({ color: 0xffffff });
    const mesh = new THREE.InstancedMesh(sphereGeo, material, atomOffsets.length);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    function updateInstanceMatrices(r) {
      atomOffsets.forEach((a, i) => {
        dummy.position.set(a.x, a.y, a.z);
        dummy.scale.set(r, r, r);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
        color.setHex(TYPE_COLORS[(a.type - 1) % TYPE_COLORS.length]);
        mesh.setColorAt(i, color);
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    updateInstanceMatrices(radius);
    scene.add(mesh);
    const dist = maxR * 2.5 || 50;
    camera.position.set(dist, dist, dist);
    camera.lookAt(0, 0, 0);
    let rot = 0;
    let frameId = null;
    function animate() {
      frameId = requestAnimationFrame(animate);
      rot += 0.003;
      camera.position.x = dist * Math.cos(rot);
      camera.position.z = dist * Math.sin(rot);
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
    }
    animate();
    return {
      baseSize,
      setRadius(r) { updateInstanceMatrices(r); },
      onResize() {
        const w = container.clientWidth || 400;
        const h = container.clientHeight || 280;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      },
      dispose() {
        if (frameId) cancelAnimationFrame(frameId);
        renderer.dispose();
        sphereGeo.dispose();
        material.dispose();
        mesh.dispose();
      },
    };
  }

  function removeStructureCard(card) {
    const scene = card._scene;
    if (scene) {
      scene.dispose();
      const idx = structureScenes.indexOf(scene);
      if (idx >= 0) structureScenes.splice(idx, 1);
    }
    // 用 path 匹配删除 savedStructures 中的条目（兼容 restoreVizState 恢复的卡片无 _savedEntry 引用的情况）
    const label = card._label;
    if (label) {
      for (let i = savedStructures.length - 1; i >= 0; i--) {
        if (savedStructures[i].path === label) {
          savedStructures.splice(i, 1);
          break;
        }
      }
    }
    card.remove();
    structureCount = Math.max(0, structureCount - 1);
    updateBadge(structureCountEl, structureCount);
    saveVizState();
    if (!structureGallery.querySelector(".structure-card")) {
      structureGallery.innerHTML = '<p class="placeholder">手动加载或 Agent 输出的结构将保留在此</p>';
    }
  }

  function appendStructure(atoms, label, persist = true) {
    removePlaceholder(structureGallery);
    const card = document.createElement("article");
    card.className = "structure-card";
    const time = new Date().toLocaleTimeString();
    const closeBtn = document.createElement("button");
    closeBtn.className = "structure-card-close";
    closeBtn.textContent = "✕";
    closeBtn.title = "移除此结构";
    closeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      removeStructureCard(card);
    });
    const head = document.createElement("div");
    head.className = "structure-card-head";
    const labelSpan = document.createElement("span");
    labelSpan.textContent = "结构预览";
    const timeEl = document.createElement("time");
    timeEl.textContent = time;
    head.appendChild(labelSpan);
    head.appendChild(timeEl);
    head.appendChild(closeBtn);
    card.appendChild(head);
    const caption = document.createElement("p");
    caption.className = "structure-caption";
    caption.textContent = `${label} · ${atoms.length ? atoms.length + " 原子" : "未能解析坐标"}`;
    card.appendChild(caption);
    const canvasWrap = document.createElement("div");
    canvasWrap.className = "structure-canvas-wrap";
    card.appendChild(canvasWrap);
    structureGallery.appendChild(card);
    structureGallery.scrollTop = structureGallery.scrollHeight;
    structureCount += 1;
    updateBadge(structureCountEl, structureCount);
    let scene = null;
    if (atoms.length) {
      scene = createStructureScene(canvasWrap, atoms);
      structureScenes.push(scene);
    }
    card._label = label;
    card._scene = scene;
    if (persist) {
      const ext = label.split(".").pop().toLowerCase();
      const entry = { path: label, ext };
      savedStructures.push(entry);
      card._savedEntry = entry;
      saveVizState();
    }
  }

  async function loadStructureFromPath(path, ext, persist = true) {
    setVizProgress(true, "解析结构文件…");
    try {
      const text = await (await fetch(assetUrl(path))).text();
      const atoms = parseStructureFile(text, ext);
      appendStructure(atoms, path, persist);
      switchTab("structure");
      setVizProgress(false);
    } catch {
      setVizProgress(false, "结构加载失败");
    }
  }

  async function loadSelectedVisualization() {
    const path = fileSelect.value;
    if (!path || path === "__up__" || path.startsWith("__dir__:")) return;
    const lower = path.toLowerCase();
    if (lower.endsWith(".csv")) {
      await appendCsvChart(path, false);
      return;
    }
    const ext = path.split(".").pop().toLowerCase();
    if (["gif", "png", "jpg", "jpeg"].includes(ext)) {
      appendMedia(ext === "gif" ? "gif" : "image", path, false);
      return;
    }
    if (STRUCTURE_EXTS.has(ext)) {
      await loadStructureFromPath(path, ext, false);
    }
  }

  async function handleVizEvent(data) {
    const ev = data.event;
    if (ev === "status") {
      setVizProgress(true, data.text || "渲染中…");
    } else if (ev === "error") {
      setVizProgress(false, data.text || "渲染失败");
    } else if (ev === "media") {
      const mt = data.media_type;
      if (mt === "csv") await appendCsvChart(data.path);
      else appendMedia(mt, data.path);
    }
  }

  async function restoreVizState() {
    clearGalleries();
    const raw = localStorage.getItem(vizStorageKey());
    if (!raw) return;
    try {
      const state = JSON.parse(raw);
      for (const m of state.media || []) {
        if (m.mediaType === "csv") await appendCsvChart(m.path, false);
        else appendMedia(m.mediaType, m.path, false);
      }
      savedMedia.push(...(state.media || []));
      for (const s of state.structures || []) {
        await loadStructureFromPath(s.path, s.ext, false);
      }
      savedStructures.push(...(state.structures || []));
    } catch (e) {
      console.error("restore viz", e);
    }
  }

  function setActiveInSidebar() {
    convList.querySelectorAll(".conv-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.chatId === currentChatId);
    });
  }

  async function loadSidebar() {
    const r = await fetch("/api/conversations");
    const data = await r.json();
    const list = data.conversations || [];
    convList.innerHTML = "";
    list.forEach((row) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "conv-item";
      btn.dataset.chatId = row.chat_id;
      const head = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = row.chat_id.replace(/^webchat:/, "");
      head.appendChild(strong);
      const pv = document.createElement("div");
      pv.className = "preview";
      pv.textContent = row.preview || "";
      btn.appendChild(head);
      btn.appendChild(pv);
      btn.addEventListener("click", () => selectChat(row.chat_id));
      convList.appendChild(btn);
    });
    setActiveInSidebar();
    return list;
  }

  async function loadHistory() {
    msgs.innerHTML = "";
    const r = await fetch(`/api/history?chat_id=${encodeURIComponent(currentChatId)}`);
    const data = await r.json();
    (data.messages || []).forEach((m) => {
      if (m.role === "system") return;
      if (m.role === "tool") { addMsg("system", m.content || ""); return; }
      if (m.role === "user") addMsg("user", m.content || "");
      else if (m.role === "assistant") addMsg("agent", m.content || "");
    });
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function selectChat(chatId) {
    currentChatId = chatId;
    localStorage.setItem(CHAT_STORAGE_KEY, currentChatId);
    setActiveInSidebar();
    await loadHistory();
    await restoreVizState();
    inp.focus();
  }

  async function createNewChat() {
    const r = await fetch("/api/conversations", { method: "POST" });
    const data = await r.json();
    if (data.chat_id) await selectChat(data.chat_id);
    await loadSidebar();
  }

  function parseSseBuffer(buffer, onPayload) {
    const parts = buffer.split("\n");
    const rest = parts.pop() || "";
    for (const line of parts) {
      if (!line.startsWith("data:")) continue;
      try {
        onPayload(JSON.parse(line.slice(5).trim()));
      } catch (_) {}
    }
    return rest;
  }

  async function send() {
    const text = inp.value.trim();
    if (!text || chatRunning) return;
    addMsg("user", text);
    inp.value = "";
    const agDiv = document.createElement("div");
    agDiv.className = "agent";
    msgs.appendChild(agDiv);
    setChatProgress(true, "等待响应…");
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, chat_id: currentChatId }),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const x = await reader.read();
        if (x.done) break;
        buffer += decoder.decode(x.value, { stream: true });
        buffer = parseSseBuffer(buffer, (payload) => {
          if (payload.viz) handleVizEvent(payload);
          else if (payload.delta) agDiv.textContent += payload.delta;
          else if (payload.done) {
            setChatProgress(false);
            msgs.scrollTop = msgs.scrollHeight;
            refreshFileList();
          }
        });
      }
      if (buffer.trim()) {
        parseSseBuffer(buffer + "\n", (payload) => {
          if (payload.viz) handleVizEvent(payload);
          else if (payload.delta) agDiv.textContent += payload.delta;
        });
      }
    } catch (e) {
      agDiv.textContent = "Error: " + e.message;
      setChatProgress(false);
    }
    await loadSidebar();
    setActiveInSidebar();
  }

  document.getElementById("new-chat").addEventListener("click", createNewChat);
  sendBtn.addEventListener("click", send);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  document.getElementById("refresh-files").addEventListener("click", refreshFileList);
  document.getElementById("browse-up").addEventListener("click", navigateBrowseUp);
  document.getElementById("load-viz").addEventListener("click", loadSelectedVisualization);
  fileSelect.addEventListener("change", onFileSelectChange);

  document.getElementById("sidebar-toggle").addEventListener("click", () => {
    sidebar.classList.toggle("collapsed");
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebar.classList.contains("collapsed") ? "1" : "0");
    structureScenes.forEach((s) => s.onResize?.());
  });

  if (localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1") sidebar.classList.add("collapsed");

  atomRadiusSlider.addEventListener("input", () => {
    atomRadiusTouched = true;
    atomPointSize = parseFloat(atomRadiusSlider.value);
    atomRadiusValue.textContent = atomPointSize.toFixed(2);
    structureScenes.forEach((s) => s.setRadius?.(atomPointSize));
  });
  document.getElementById("atom-radius-dec").addEventListener("click", () => {
    atomRadiusSlider.value = String(Math.max(0.05, atomPointSize - 0.05));
    atomRadiusSlider.dispatchEvent(new Event("input"));
  });
  document.getElementById("atom-radius-inc").addEventListener("click", () => {
    atomRadiusSlider.value = String(Math.min(6, atomPointSize + 0.05));
    atomRadiusSlider.dispatchEvent(new Event("input"));
  });
  document.getElementById("atom-radius-reset").addEventListener("click", () => {
    atomRadiusTouched = false;
    const last = structureScenes[structureScenes.length - 1];
    const size = last?.baseSize ?? 0.3;
    structureScenes.forEach((s) => s.setRadius?.(s.baseSize));
    atomRadiusSlider.value = String(size);
    atomRadiusSlider.dispatchEvent(new Event("input"));
  });

  window.addEventListener("resize", () => structureScenes.forEach((s) => s.onResize?.()));

  async function init() {
    await loadConfig();
    const list = await loadSidebar();
    const ids = list.map((x) => x.chat_id);
    if (!ids.length) {
      currentChatId = "webchat:default";
      localStorage.setItem(CHAT_STORAGE_KEY, currentChatId);
    } else if (ids.indexOf(currentChatId) < 0) {
      currentChatId = list[0].chat_id;
      localStorage.setItem(CHAT_STORAGE_KEY, currentChatId);
    }
    setActiveInSidebar();
    await loadHistory();
    await restoreVizState();
  }

  init();
})();
