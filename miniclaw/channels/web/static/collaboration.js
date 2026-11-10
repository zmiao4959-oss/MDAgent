// One conversation chooses a mode; the shared registry is never switched globally.
export function createCollaborationPanel({ getChatId, getProjectId, isRunning, onApplied, onSaved, onPause }) {
  const el = id => document.getElementById(`collaboration-${id}`);
  const launcher = document.getElementById('btn-collaboration');
  const dialog = el('dialog');
  const labels = { default: '默认委派', graph: '智能体画布', room: '公共频道' };
  const statuses = { running: '讨论进行中', idle: '暂时空闲', limit_reached: '已到本轮上限', cancelled: '已停止', interrupted: '运行已中断', timed_out: '已超时', error: '运行失败' };
  let saved = null, loadedChat = null, sequence = 0, saving = false, profiles = [];

  async function json(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '请求失败');
    return data;
  }
  function message(text, error = false) {
    el('message').textContent = text;
    el('message').dataset.error = String(error);
  }
  function updateRunning() {
    const locked = isRunning() || saving || !getProjectId() || loadedChat !== getChatId();
    for (const control of dialog.querySelectorAll('[data-room-config]')) control.disabled = locked;
    let position = 0;
    const rows = [...el('members').children];
    rows.forEach((row, index) => {
      row.querySelector('.room-member-order').textContent = row.querySelector('input').checked ? String(++position) : '—';
      row.querySelector('[data-move="up"]').disabled = locked || index === 0;
      row.querySelector('[data-move="down"]').disabled = locked || index === rows.length - 1;
    });
    el('save').disabled = locked;
    el('pause').disabled = !isRunning();
    el('room-fields').hidden = el('mode').value !== 'room';
    el('graph-note').hidden = el('mode').value !== 'graph';
  }
  function renderLog() {
    const log = el('log');
    log.replaceChildren();
    for (const item of saved?.channel?.messages || []) {
      const entry = document.createElement('article');
      const heading = document.createElement('strong');
      heading.textContent = `${item.author === 'user' ? '你' : item.author} · #${item.sequence}`;
      const content = document.createElement('p'); content.textContent = item.content;
      entry.append(heading, content); log.appendChild(entry);
    }
    if (!log.childElementCount) log.textContent = '还没有公开消息。启用公共频道后，在项目对话中发送任务。';
    const run = saved?.last_room_run;
    el('status').textContent = run ? `${statuses[run.status] || run.status} · ${run.published || 0} 条公开发言 · ${run.activations || 0} 轮成员工作` : '尚未开始';
  }
  function fillForm() {
    el('mode').value = saved?.mode || 'default';
    const room = saved?.room;
    for (const [id, fallback] of [['concurrency', 4], ['messages', 24], ['activations', 100], ['timeout', 600]]) {
      const key = { concurrency: 'max_concurrency', messages: 'max_messages', activations: 'max_activations', timeout: 'timeout_sec' }[id];
      el(id).value = room?.[key] ?? fallback;
    }
    const names = room?.members || profiles.map(profile => profile.name);
    const chosen = new Set(names);
    const byName = new Map(profiles.map(profile => [profile.name, profile]));
    const ordered = [...names.map(name => byName.get(name)).filter(Boolean), ...profiles.filter(profile => !chosen.has(profile.name))];
    el('members').replaceChildren();
    for (const profile of ordered) {
      const row = document.createElement('div'); row.className = 'room-member';
      const label = document.createElement('label'); label.className = 'room-member-select';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = profile.name; checkbox.checked = chosen.has(profile.name); checkbox.dataset.roomConfig = '';
      checkbox.addEventListener('change', updateRunning);
      const order = document.createElement('span'); order.className = 'room-member-order'; order.setAttribute('aria-hidden', 'true');
      const copy = document.createElement('span');
      const title = document.createElement('strong'); title.textContent = profile.name;
      const description = document.createElement('small'); description.textContent = profile.description;
      copy.append(title, description); label.append(checkbox, order, copy);
      const actions = document.createElement('div'); actions.className = 'room-member-actions';
      for (const [direction, text, symbol] of [['up', '上移', '↑'], ['down', '下移', '↓']]) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'ghost-btn';
        button.textContent = symbol; button.dataset.roomConfig = ''; button.dataset.move = direction;
        button.setAttribute('aria-label', `${text} ${profile.name}`); button.title = text;
        button.addEventListener('click', () => {
          if (button.disabled) return;
          const sibling = direction === 'up' ? row.previousElementSibling : row.nextElementSibling;
          if (!sibling) return;
          if (direction === 'up') row.parentElement.insertBefore(row, sibling);
          else row.parentElement.insertBefore(sibling, row);
          updateRunning();
          (button.disabled ? checkbox : button).focus();
        });
        actions.appendChild(button);
      }
      row.append(label, actions); el('members').appendChild(row);
    }
    updateRunning();
  }
  async function refresh(fill = false) {
    const requestSequence = ++sequence;
    const chatId = getChatId();
    if (!chatId || !getProjectId()) { launcher.textContent = '协作方式'; return; }
    try {
      const value = await json(`/api/conversations/${encodeURIComponent(chatId)}/collaboration`);
      if (requestSequence !== sequence || chatId !== getChatId()) return;
      const changedChat = loadedChat !== chatId;
      saved = value; loadedChat = chatId;
      launcher.textContent = `协作 · ${labels[value.mode] || value.mode}`;
      onApplied(value.mode);
      if (fill || changedChat) fillForm();
      renderLog(); updateRunning();
    } catch (error) { if (chatId === getChatId()) message(error.message, true); }
  }
  async function open() {
    dialog.hidden = false;
    if (!getProjectId()) { message('请先选择或创建一个项目。', true); updateRunning(); return; }
    const chatId = getChatId();
    message('正在读取协作设置…');
    try {
      const result = await json('/api/agent-profiles');
      if (chatId !== getChatId()) return;
      profiles = result.profiles;
      await refresh(true);
      if (loadedChat === chatId) message(isRunning() ? '正在运行，可以查看记录；暂停后可修改设置。' : '选择协作方式并保存，下一条项目消息使用新设置。');
    } catch (error) { message(error.message, true); }
    el('close').focus();
  }
  function close() { dialog.hidden = true; launcher.focus(); }
  launcher.addEventListener('click', open);
  el('close').addEventListener('click', close);
  dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape') close();
    if (event.key === 'Tab') {
      const controls = [...dialog.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled)')].filter(control => control.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  el('mode').addEventListener('change', updateRunning);
  el('edit-graph').addEventListener('click', () => { close(); document.getElementById('btn-agent-graph').click(); });
  el('pause').addEventListener('click', async () => {
    try { await onPause(); await refresh(); message('已暂停，运行清理结束后可切换模式。'); }
    catch (error) { message(error.message, true); }
  });
  el('save').addEventListener('click', async () => {
    if (isRunning() || saving || loadedChat !== getChatId()) return;
    const chatId = loadedChat;
    const mode = el('mode').value;
    const body = { mode };
    if (mode === 'room') body.room = {
      version: 1, room_id: saved?.room?.room_id || 'public',
      members: [...el('members').querySelectorAll('input:checked')].map(input => input.value),
      max_concurrency: Number(el('concurrency').value), max_messages: Number(el('messages').value),
      max_activations: Number(el('activations').value), timeout_sec: Number(el('timeout').value),
    };
    saving = true; updateRunning();
    try {
      const value = await json(`/api/conversations/${encodeURIComponent(chatId)}/collaboration`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (chatId !== getChatId()) return;
      saved = value;
      launcher.textContent = `协作 · ${labels[value.mode]}`;
      onApplied(value.mode); onSaved(); renderLog();
      message(`已启用${labels[value.mode]}，下一条消息生效。`);
    } catch (error) { message(error.message, true); }
    finally { saving = false; updateRunning(); }
  });
  window.setInterval(() => { if (!dialog.hidden) refresh(); }, 3000);
  return {
    refresh, updateRunning,
    get mode() { return saved?.mode || 'default'; },
    handleEvent(event, chatId) {
      if (chatId !== getChatId()) return;
      saved ||= { mode: 'room', channel: { messages: [] } };
      if (!saved.last_room_run || saved.last_room_run.run_id !== event.run_id) saved.last_room_run = { run_id: event.run_id, status: 'running', published: 0, activations: 0 };
      if (event.kind === 'message') {
        saved.channel ||= { messages: [] };
        if (!saved.channel.messages.some(item => item.message_id === event.message.message_id)) {
          saved.channel.messages.push(event.message);
          if (event.message.author !== 'user') saved.last_room_run.published += 1;
        }
      } else if (event.kind === 'member_started') saved.last_room_run.activations += 1;
      else if (statuses[event.kind]) saved.last_room_run.status = event.kind;
      renderLog(); updateRunning();
    },
  };
}
