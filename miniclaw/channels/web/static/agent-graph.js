// X6 provides editing only. The server owns message permissions and execution.
export function createAgentGraphPanel({ getChatId, getProjectId, isRunning, onApplied, onPause }) {
  const el = (id) => document.getElementById(`agent-graph-${id}`);
  const dialog = el('dialog');
  const launcher = document.getElementById('btn-agent-graph');
  const canvas = el('canvas');
  let graph = null;
  let profiles = [];
  let selected = null;
  let loadedChat = null;
  let loadSequence = 0;
  let savedGraph = null;
  let lastRun = null;
  let projectSkills = { bindings: {}, requests: [] };
  let scriptPromise;
  let saving = false;
  let loading = false;

  function message(text, error = false) {
    el('message').textContent = text;
    el('message').dataset.error = String(error);
  }
  async function json(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || '请求失败');
    return data;
  }
  function loadX6() {
    if (window.X6) return Promise.resolve();
    if (!scriptPromise) scriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/vendor/x6/x6.min.js';
      script.onload = resolve;
      script.onerror = () => { script.remove(); scriptPromise = null; reject(new Error('画布组件加载失败，请重试')); };
      document.head.appendChild(script);
    });
    return scriptPromise;
  }
  function editable() { return loading || (Boolean(getProjectId()) && !isRunning() && !saving); }
  function ensureEditable() {
    if (editable()) return true;
    message('请先暂停当前任务，再编辑或保存画布。', true);
    return false;
  }
  function select(cell) {
    if (selected && graph?.getCellById(selected.id)) {
      selected.attr(selected.isNode() ? 'body/strokeWidth' : 'line/strokeWidth', 2);
    }
    selected = cell;
    if (cell) cell.attr(cell.isNode() ? 'body/strokeWidth' : 'line/strokeWidth', 4);
    el('start').disabled = !cell?.isNode() || !editable();
    el('reverse').disabled = !cell?.isEdge() || !editable();
    el('remove').disabled = !cell || !editable();
    if (!cell) { el('selection').textContent = '点击画布中的节点或连线。'; return; }
    if (cell.isNode()) {
      const profile = profiles.find((item) => item.name === cell.id);
      const mounted = Object.values(projectSkills.bindings[cell.id] || {}).filter((item) => item.available !== false).map((item) => item.name);
      el('selection').textContent = `${cell.id}\n${profile?.description || ''}\n\nSkills: ${[...(profile?.skills || []), ...mounted].join(', ') || '无'}\n工具: ${(profile?.tools || []).join(', ') || '无'}`;
    } else {
      el('selection').textContent = `${cell.getSourceCellId()} → ${cell.getTargetCellId()}\n仅允许沿此方向投递消息；不会自动运行接收方。`;
    }
  }
  function updateNodes() {
    if (!graph) return;
    const old = el('entry').value;
    el('entry').replaceChildren();
    for (const node of graph.getNodes()) {
      const option = document.createElement('option');
      option.value = node.id; option.textContent = node.id;
      el('entry').appendChild(option);
    }
    if (graph.getCellById(old)) el('entry').value = old;
    paintEntry();
    for (const button of el('profiles').querySelectorAll('button')) button.disabled = Boolean(graph.getCellById(button.dataset.profile)) || !editable();
  }
  function paintEntry() {
    for (const node of graph?.getNodes() || []) {
      const start = node.id === el('entry').value;
      node.attr('body/stroke', start ? '#f8c66d' : '#4b8297');
      node.attr('label/text', `${start ? '▶ ' : ''}${node.id}`);
    }
  }
  function addNode(name, point) {
    if (!graph || graph.getCellById(name) || !ensureEditable()) return;
    const index = graph.getNodes().length;
    graph.addNode({
      id: name, shape: 'rect', x: point?.x ?? 70 + index % 3 * 280, y: point?.y ?? 70 + Math.floor(index / 3) * 150,
      width: 210, height: 76,
      attrs: { body: { fill: '#1c2d42', stroke: '#4b8297', strokeWidth: 2, rx: 12, ry: 12 }, label: { text: name, fill: '#edf7ff', fontSize: 13, fontWeight: 600 } },
      ports: { groups: { in: { position: 'left', attrs: { circle: { r: 6, magnet: 'passive', stroke: '#68c7d5', fill: '#142236', strokeWidth: 2 } } }, out: { position: 'right', attrs: { circle: { r: 6, magnet: true, stroke: '#68c7d5', fill: '#142236', strokeWidth: 2 } } } }, items: [{ id: 'in', group: 'in' }, { id: 'out', group: 'out' }] },
    });
    if (!loading) el('mode').value = 'graph';
    updateNodes();
  }
  function hasEdge(source, target) {
    return graph.getEdges().some((edge) => edge.getSourceCellId() === source && edge.getTargetCellId() === target);
  }
  function edgeStyle() { return { line: { stroke: '#68c7d5', strokeWidth: 2, targetMarker: { name: 'classic', size: 8 } } }; }
  function edgeRoute() { return { router: { name: 'manhattan', args: { startDirections: ['right'], endDirections: ['left'], padding: 25 } }, connector: { name: 'rounded', args: { radius: 12 } } }; }
  function addEdge(source, target) {
    if (!hasEdge(source, target)) graph.addEdge({ source: { cell: source, port: 'out' }, target: { cell: target, port: 'in' }, attrs: edgeStyle(), ...edgeRoute() });
    if (!loading) el('mode').value = 'graph';
  }
  function initGraph() {
    graph = new window.X6.Graph({
      container: canvas, width: canvas.clientWidth, height: canvas.clientHeight,
      // This small role graph reuses cell IDs when reopening. Finish removing
      // old views before creating their replacements; queued async jobs can
      // otherwise leave duplicate SVG nodes even though the model is unique.
      async: false,
      background: { color: '#101929' }, grid: { size: 16, visible: true },
      panning: true, mousewheel: { enabled: true, modifiers: ['ctrl', 'meta'], minScale: .3, maxScale: 2 },
      interacting: () => editable(),
      connecting: {
        allowBlank: false, allowLoop: false, allowNode: false, allowEdge: false, snap: true,
        validateConnection({ sourceCell, targetCell, sourcePort, targetPort }) {
          return editable() && sourceCell !== targetCell && sourcePort === 'out' && targetPort === 'in' && !hasEdge(sourceCell?.id, targetCell?.id);
        },
        createEdge() { return this.createEdge({ attrs: edgeStyle(), ...edgeRoute() }); },
      },
    });
    graph.on('node:click', ({ node }) => select(node));
    graph.on('edge:click', ({ edge }) => select(edge));
    graph.on('blank:click', () => select(null));
    graph.on('node:removed', updateNodes);
    graph.on('edge:connected', () => { if (!loading) el('mode').value = 'graph'; });
    new ResizeObserver(() => { if (!dialog.hidden) graph.resize(canvas.clientWidth, canvas.clientHeight); }).observe(canvas);
  }
  function renderProfiles() {
    el('profiles').replaceChildren();
    for (const profile of profiles) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'graph-profile'; button.dataset.profile = profile.name; button.draggable = true;
      button.title = profile.description;
      const title = document.createElement('strong'); title.textContent = profile.name;
      const description = document.createElement('span'); description.textContent = profile.description;
      button.append(title, description);
      button.addEventListener('click', () => addNode(profile.name));
      button.addEventListener('dragstart', (event) => event.dataTransfer.setData('application/x-miniclaw-agent', profile.name));
      el('profiles').appendChild(button);
    }
  }
  function renderEvents() {
    const box = el('events'); box.replaceChildren();
    const labels = { started: '运行开始', node_started: '正在处理', node_done: '处理完成', message: '消息已入队', blocked: '通信被阻止', limit: '消息达到上限', done: '本轮完成', limit_reached: '本轮达到消息上限', cancelled: '任务已停止', interrupted: '上次运行被中断', error: '运行失败', skill_requested: '申请 Skill', skill_mounted: 'Skill 已挂载', skill_failed: 'Skill 处理失败', skill_revoked: 'Skill 已撤下' };
    for (const event of (lastRun?.events || []).slice(-60)) {
      const row = document.createElement('div'); row.className = 'graph-event'; row.dataset.kind = event.kind;
      const route = event.target ? `${event.source} → ${event.target}` : event.node || event.entry || '';
      row.textContent = `${labels[event.kind] || event.kind}${route ? ` · ${route}` : ''}${event.name ? ` · ${event.name}` : ''}${event.reason ? `\n${event.reason}` : ''}`;
      row.title = event.preview || ''; box.appendChild(row);
    }
    if (!box.childElementCount) box.textContent = '保存画布后，在项目对话中发送任务。';
    box.scrollTop = box.scrollHeight;
  }
  function renderProjectSkills() {
    const box = el('skills'); box.replaceChildren();
    for (const [agent, skills] of Object.entries(projectSkills.bindings)) {
      for (const skill of Object.values(skills)) {
        const card = document.createElement('div'); card.className = 'graph-skill';
        const title = document.createElement('strong'); title.textContent = `${skill.name} · ${skill.version}`;
        const scope = document.createElement('span'); scope.textContent = `${agent}${skill.available === false ? ' · 当前不可用' : ''}`;
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'ghost-btn'; remove.textContent = '撤下'; remove.disabled = !editable();
        remove.addEventListener('click', async () => {
          if (!ensureEditable()) return;
          try {
            await json(`/api/conversations/${encodeURIComponent(getChatId())}/project-skills/${encodeURIComponent(agent)}/${encodeURIComponent(skill.name)}`, { method: 'DELETE' });
            await refresh(); select(selected); message(`已撤下 ${agent} 的 ${skill.name}。`);
          } catch (error) { message(error.message, true); }
        });
        card.append(title, scope, remove); box.appendChild(card);
      }
    }
    const labels = { requested: '待构建', validating: '校验中', validation_failed: '验证失败' };
    for (const request of projectSkills.requests.filter((item) => labels[item.status])) {
      const row = document.createElement('div'); row.className = 'graph-skill';
      row.textContent = `${request.requester} · ${labels[request.status]}\n${request.objective}`;
      row.title = request.error || ''; box.appendChild(row);
    }
    if (!box.childElementCount) box.textContent = '与 skill-builder 双向连接后，可在任务中申请临时 Skill。';
  }
  async function refresh() {
    const sequence = ++loadSequence;
    const chatId = getChatId();
    if (!chatId || !getProjectId()) return;
    try {
      const data = await json(`/api/conversations/${encodeURIComponent(chatId)}/agent-graph`);
      if (sequence !== loadSequence || chatId !== getChatId()) return;
      savedGraph = data.graph; lastRun = data.last_run; loadedChat = chatId;
      projectSkills = data.project_skills || { bindings: {}, requests: [] };
      launcher.textContent = savedGraph ? `◇ 团队 · ${savedGraph.nodes.length} 个智能体` : '◇ 智能体画布';
      renderEvents();
      renderProjectSkills();
    } catch (error) { message(error.message, true); }
  }
  async function open() {
    dialog.hidden = false;
    if (!getProjectId()) {
      loadedChat = null;
      updateLock();
      message('请先从左侧选择或创建一个项目，再配置智能体画布。', true);
      el('close').focus();
      return;
    }
    const chatId = getChatId();
    message('正在加载智能体画布…');
    try {
      const [data] = await Promise.all([json('/api/agent-profiles'), loadX6()]);
      if (chatId !== getChatId()) return;
      profiles = data.profiles;
      if (!graph) initGraph();
      await refresh();
      if (loadedChat !== chatId) throw new Error('画布配置读取失败，请重试');
      select(null); graph.clearCells(); renderProfiles();
      // Render stored nodes while the run is active; interaction stays locked.
      const config = savedGraph || { nodes: [{ id: data.default_profile, x: 70, y: 70 }], edges: [], entry: data.default_profile, max_messages: 24 };
      loading = true;
      try {
        for (const item of config.nodes) addNode(item.id, item);
        for (const edge of config.edges) addEdge(edge.source, edge.target);
      } finally { loading = false; }
      el('entry').value = config.entry; el('limit').value = config.max_messages;
      el('mode').value = savedGraph ? 'graph' : 'default';
      paintEntry(); updateLock();
      graph.resize(canvas.clientWidth, canvas.clientHeight);
      graph.zoomToFit({ padding: 55, maxScale: 1 });
      renderEvents();
      message(isRunning() ? '任务运行中，可查看通信记录；暂停后可编辑画布。' : '单向箭头仅允许沿箭头发送；双向通信需要两个方向的连线。');
      el('close').focus();
    } catch (error) { message(error.message, true); }
  }
  function updateLock() {
    const locked = !editable();
    for (const id of ['mode', 'entry', 'limit', 'save']) el(id).disabled = locked;
    el('pause').disabled = !isRunning();
    updateNodes(); select(selected);
    for (const button of el('skills').querySelectorAll('button')) button.disabled = locked;
  }
  launcher.addEventListener('click', open);
  function close() { dialog.hidden = true; launcher.focus(); }
  window.setInterval(async () => {
    if (!dialog.hidden) { await refresh(); updateLock(); }
  }, 3000);
  el('close').addEventListener('click', close);
  dialog.addEventListener('click', (event) => { if (event.target === dialog) close(); });
  document.addEventListener('keydown', (event) => {
    if (dialog.hidden) return;
    if (event.key === 'Escape') close();
    if (event.key === 'Tab') {
      const focusable = [...dialog.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled)')];
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  canvas.addEventListener('dragover', (event) => event.preventDefault());
  canvas.addEventListener('drop', (event) => {
    event.preventDefault();
    const name = event.dataTransfer.getData('application/x-miniclaw-agent');
    if (profiles.some((p) => p.name === name)) addNode(name, graph.clientToLocal(event.clientX, event.clientY));
  });
  el('entry').addEventListener('change', () => { paintEntry(); el('mode').value = 'graph'; });
  el('start').addEventListener('click', () => { if (ensureEditable() && selected?.isNode()) { el('entry').value = selected.id; paintEntry(); } });
  el('reverse').addEventListener('click', () => { if (ensureEditable() && selected?.isEdge()) addEdge(selected.getTargetCellId(), selected.getSourceCellId()); });
  el('remove').addEventListener('click', () => { if (ensureEditable() && selected) { const cell = selected; select(null); graph.removeCell(cell); updateNodes(); } });
  el('fit').addEventListener('click', () => graph?.zoomToFit({ padding: 55, maxScale: 1 }));
  el('pause').addEventListener('click', async () => {
    try { await onPause(); updateLock(); message('任务已暂停。修改并保存后，恢复项目即可继续。'); }
    catch (error) { message(error.message, true); }
  });
  el('save').addEventListener('click', async () => {
    if (!ensureEditable() || loadedChat !== getChatId()) return;
    const chatId = loadedChat;
    const config = el('mode').value === 'default' ? null : {
      version: 1,
      nodes: graph.getNodes().map((node) => ({ id: node.id, ...node.position() })),
      edges: graph.getEdges().map((edge) => ({ source: edge.getSourceCellId(), target: edge.getTargetCellId() })),
      entry: el('entry').value, max_messages: Number(el('limit').value),
    };
    saving = true; updateLock();
    try {
      const data = await json(`/api/conversations/${encodeURIComponent(chatId)}/agent-graph`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: config }) });
      if (chatId !== getChatId()) return;
      savedGraph = data.graph;
      launcher.textContent = savedGraph ? `◇ 团队 · ${savedGraph.nodes.length} 个智能体` : '◇ 智能体画布';
      onApplied(Boolean(savedGraph));
      message(savedGraph ? '已保存。下一条消息会从指定起点开始运行。' : '已恢复默认智能体模式。');
    } catch (error) { message(error.message, true); }
    finally { saving = false; updateLock(); }
  });
  return {
    refresh,
    updateRunning: updateLock,
    handleEvent(event, chatId) {
      if (chatId !== getChatId()) return;
      if (!lastRun || lastRun.run_id !== event.run_id) lastRun = { run_id: event.run_id, events: [] };
      if (event.sequence != null && lastRun.events.some((item) => item.sequence === event.sequence)) return;
      lastRun.events.push(event);
      lastRun.events = lastRun.events.slice(-400);
      if (graph && !dialog.hidden) {
        const node = graph.getCellById(event.node);
        if (node?.isNode()) node.attr('body/fill', event.kind === 'node_started' ? '#244d51' : '#1c2d42');
      }
      renderEvents(); updateLock();
    },
    get enabled() { return Boolean(savedGraph); },
  };
}
