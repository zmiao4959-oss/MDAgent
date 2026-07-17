import * as THREE from '/static/three.module.js';

  const CHAT_STORAGE_KEY = "miniclaw_webchat_chat_id";
  const PROJECT_STORAGE_KEY = "miniclaw_webchat_project_id";
  const VIZ_STORAGE_PREFIX = "miniclaw_viz_";
  const VIZ_REMOVED_KEY = "miniclaw_viz_removed";
  const SIDEBAR_COLLAPSED_KEY = "miniclaw_sidebar_collapsed";
  const VIZ_COLLAPSED_KEY = "miniclaw_viz_collapsed";
  const SETTINGS_STORAGE_KEY = "miniclaw_webchat_settings_v1";

  const msgs = document.getElementById("messages");
  const convList = document.getElementById("conv-list");
  const inp = document.getElementById("input");
  const sendBtn = document.getElementById("btn-send");
  const sidebar = document.getElementById("sidebar");
  const fileSelect = document.getElementById("file-select");
  const mediaGallery = document.getElementById("media-gallery");
  const structureGallery = document.getElementById("structure-gallery");
  const artifactList = document.getElementById("artifact-list");
  const artifactSummary = document.getElementById("artifact-summary");
  const artifactPreview = document.getElementById("artifact-preview");
  const runTimeline = document.getElementById("run-timeline");
  const taskList = document.getElementById("task-list");
  const taskForm = document.getElementById("task-form");
  const taskInput = document.getElementById("task-input");
  const projectSummary = document.getElementById("project-summary");
  const runNextTaskButton = document.getElementById("run-next-task");
  const chatProgress = document.getElementById("chat-progress");
  const chatStatusText = document.getElementById("chat-status-text");
  const vizProgress = document.getElementById("viz-progress");
  const vizStatusText = document.getElementById("viz-status-text");
  const mediaCountEl = document.getElementById("media-count");
  const structureCountEl = document.getElementById("structure-count");
  const artifactCountEl = document.getElementById("artifact-count");
  const browsePathEl = document.getElementById("browse-path");
  const browseUpBtn = document.getElementById("browse-up");
  const atomRadiusSlider = document.getElementById("atom-radius-slider");
  const atomRadiusValue = document.getElementById("atom-radius-value");
  const currentChatLabel = document.getElementById("current-chat-label");
  const runtimeDot = document.getElementById("runtime-dot");
  const runtimeSummary = document.getElementById("runtime-summary");
  const planModeCheckbox = document.getElementById("plan-mode-checkbox");
  const planModeToggle = document.getElementById("plan-mode-toggle");
  const projectDialog = document.getElementById("project-dialog");
  const projectForm = document.getElementById("project-form");
  const projectTitleInput = document.getElementById("project-title");
  const projectObjectiveInput = document.getElementById("project-objective-input");
  const quickActions = document.getElementById("quick-actions");
  const settingsDialog = document.getElementById("settings-dialog");
  const settingsForm = document.getElementById("settings-form");
  const settingTheme = document.getElementById("setting-theme");
  const settingMessageSize = document.getElementById("setting-message-size");
  const settingCodeWrap = document.getElementById("setting-code-wrap");
  const settingAutoScroll = document.getElementById("setting-auto-scroll");
  const settingEnterToSend = document.getElementById("setting-enter-to-send");
  const settingShowQuickActions = document.getElementById("setting-show-quick-actions");
  const gpumdKnowledgeStatus = document.getElementById("gpumd-knowledge-status");
  const gpumdCorpusCount = document.getElementById("gpumd-corpus-count");
  const gpumdIndexCount = document.getElementById("gpumd-index-count");
  const gpumdModel = document.getElementById("gpumd-model");
  const gpumdKnowledgeMessage = document.getElementById("gpumd-knowledge-message");
  const gpumdSyncDocs = document.getElementById("gpumd-sync-docs");
  const gpumdUpdateIndex = document.getElementById("gpumd-update-index");
  const runtimeDiagnosticStatus = document.getElementById("runtime-diagnostic-status");
  const runtimeDiagnosticGrid = document.getElementById("runtime-diagnostic-grid");
  const refreshRuntimeDiagnosticsButton = document.getElementById("refresh-runtime-diagnostics");
  const exportRuntimeDiagnosticsButton = document.getElementById("export-runtime-diagnostics");
  const settingTaskConcurrency = document.getElementById("setting-task-concurrency");
  const settingTaskRetries = document.getElementById("setting-task-retries");
  const settingTaskNotifications = document.getElementById("setting-task-notifications");
  const taskBehaviorMessage = document.getElementById("task-behavior-message");
  const evolutionStatus = document.getElementById("evolution-status");
  const evolutionCandidateCount = document.getElementById("evolution-candidate-count");
  const evolutionVerifiedCount = document.getElementById("evolution-verified-count");
  const evolutionRejectedCount = document.getElementById("evolution-rejected-count");
  const evolutionFilter = document.getElementById("evolution-filter");
  const evolutionExperienceList = document.getElementById("evolution-experience-list");
  const evolutionMessage = document.getElementById("evolution-message");
  const refreshEvolutionButton = document.getElementById("refresh-evolution-experiences");

  let currentChatId = localStorage.getItem(CHAT_STORAGE_KEY) || "webchat:default";
  let currentProjectId = localStorage.getItem(PROJECT_STORAGE_KEY) || "";
  let chatRunning = false;
  let planMode = false;
  const projectsById = new Map();
  const runningProjectIds = new Set();
  let mediaCount = 0;
  let structureCount = 0;
  let workspaceRoot = "";
  let browseDir = "";
  let atomRadiusTouched = false;
  let atomPointSize = 0.3;
  const structureScenes = [];
  const savedMedia = [];
  const savedStructures = [];
  let gpumdStatusTimer = null;
  let latestRuntimeDiagnostics = null;
  const DEFAULT_TASK_BEHAVIOR = Object.freeze({
    max_concurrency: 2,
    retry_count: 0,
    notify_on_completion: false,
  });
  let taskBehaviorSettings = { ...DEFAULT_TASK_BEHAVIOR };

  const DEFAULT_SETTINGS = Object.freeze({
    theme: "dark",
    messageSize: "medium",
    codeWrap: false,
    autoScroll: true,
    enterToSend: true,
    showQuickActions: true,
  });

  function loadSettings() {
    try {
      const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || "{}");
      return {
        theme: ["dark", "light", "system"].includes(stored.theme) ? stored.theme : DEFAULT_SETTINGS.theme,
        messageSize: ["small", "medium", "large"].includes(stored.messageSize) ? stored.messageSize : DEFAULT_SETTINGS.messageSize,
        codeWrap: typeof stored.codeWrap === "boolean" ? stored.codeWrap : DEFAULT_SETTINGS.codeWrap,
        autoScroll: typeof stored.autoScroll === "boolean" ? stored.autoScroll : DEFAULT_SETTINGS.autoScroll,
        enterToSend: typeof stored.enterToSend === "boolean" ? stored.enterToSend : DEFAULT_SETTINGS.enterToSend,
        showQuickActions: typeof stored.showQuickActions === "boolean" ? stored.showQuickActions : DEFAULT_SETTINGS.showQuickActions,
      };
    } catch (_) {
      return { ...DEFAULT_SETTINGS };
    }
  }

  let webchatSettings = loadSettings();
  const systemThemeQuery = window.matchMedia("(prefers-color-scheme: light)");

  function updateInputPlaceholder() {
    if (planMode) {
      inp.placeholder = "描述你的目标，Agent 会先制定执行计划...";
    } else if (webchatSettings.enterToSend) {
      inp.placeholder = "输入消息... (Enter 发送, Shift+Enter 换行)";
    } else {
      inp.placeholder = "输入消息... (Ctrl/Cmd+Enter 发送)";
    }
  }

  function applySettings() {
    const resolvedTheme = webchatSettings.theme === "system"
      ? (systemThemeQuery.matches ? "light" : "dark")
      : webchatSettings.theme;
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.dataset.messageSize = webchatSettings.messageSize;
    document.documentElement.dataset.codeWrap = webchatSettings.codeWrap ? "true" : "false";
    if (quickActions) quickActions.hidden = !webchatSettings.showQuickActions;
    updateInputPlaceholder();
  }

  function saveSettings() {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(webchatSettings));
    applySettings();
  }

  function fillSettingsForm() {
    settingTheme.value = webchatSettings.theme;
    settingMessageSize.value = webchatSettings.messageSize;
    settingCodeWrap.checked = webchatSettings.codeWrap;
    settingAutoScroll.checked = webchatSettings.autoScroll;
    settingEnterToSend.checked = webchatSettings.enterToSend;
    settingShowQuickActions.checked = webchatSettings.showQuickActions;
    settingTaskConcurrency.value = String(taskBehaviorSettings.max_concurrency);
    settingTaskRetries.value = String(taskBehaviorSettings.retry_count);
    settingTaskNotifications.checked = taskBehaviorSettings.notify_on_completion;
  }

  function openSettingsDialog() {
    fillSettingsForm();
    settingsDialog.hidden = false;
    settingTheme.focus();
    refreshGpumdKnowledgeStatus();
    refreshRuntimeDiagnostics();
    loadTaskBehaviorSettings();
    refreshEvolutionExperiences();
  }

  function closeSettingsDialog() {
    settingsDialog.hidden = true;
    if (gpumdStatusTimer) clearTimeout(gpumdStatusTimer);
    gpumdStatusTimer = null;
  }

  function renderGpumdKnowledgeStatus(data) {
    const jobRunning = Boolean(data.job?.running);
    const labels = {
      ready: ["已就绪", "ready"],
      missing_corpus: ["缺少文档", "warning"],
      missing_index: ["待建索引", "warning"],
      model_mismatch: ["模型已变化", "warning"],
      outdated: ["索引待更新", "warning"],
      error: ["状态异常", "error"],
    };
    const [label, visualState] = jobRunning
      ? ["更新中", "running"]
      : (labels[data.state] || ["未知", "warning"]);
    gpumdKnowledgeStatus.textContent = label;
    gpumdKnowledgeStatus.dataset.state = visualState;
    gpumdCorpusCount.textContent = Number(data.corpus?.count || 0).toLocaleString("zh-CN");
    gpumdIndexCount.textContent = Number(data.index?.count || 0).toLocaleString("zh-CN");
    gpumdModel.textContent = data.model || "未配置";
    gpumdModel.title = data.model || "";

    let message = data.job?.message || "";
    if (!message && !data.api_key_configured) {
      message = "向量服务密钥未配置；可先同步文档，配置后再更新向量。";
    } else if (!message && data.state === "model_mismatch") {
      message = "当前模型与旧索引不同，下一次更新会使用新模型重新向量化。";
    } else if (!message && data.state === "outdated") {
      message = "文档块数量已变化，建议执行增量更新。";
    } else if (!message && data.state === "ready") {
      message = "文档与向量索引数量一致。";
    }
    gpumdKnowledgeMessage.textContent = message || "尚未建立 GPUMD 知识库。";
    gpumdSyncDocs.disabled = jobRunning;
    gpumdUpdateIndex.disabled = jobRunning || !data.enabled || !data.api_key_configured || !data.corpus?.exists;
  }

  async function refreshGpumdKnowledgeStatus() {
    if (!settingsDialog || settingsDialog.hidden) return;
    try {
      const response = await fetch("/api/knowledge/gpumd");
      if (!response.ok) throw new Error("无法读取知识库状态");
      const data = await response.json();
      renderGpumdKnowledgeStatus(data);
      if (data.job?.running) {
        gpumdStatusTimer = setTimeout(refreshGpumdKnowledgeStatus, 1200);
      }
    } catch (error) {
      gpumdKnowledgeStatus.textContent = "读取失败";
      gpumdKnowledgeStatus.dataset.state = "error";
      gpumdKnowledgeMessage.textContent = error.message || "无法读取知识库状态";
    }
  }

  async function runGpumdKnowledgeAction(action) {
    gpumdSyncDocs.disabled = true;
    gpumdUpdateIndex.disabled = true;
    gpumdKnowledgeStatus.textContent = "正在启动";
    gpumdKnowledgeStatus.dataset.state = "running";
    gpumdKnowledgeMessage.textContent = action === "sync" ? "正在启动文档同步…" : "正在启动增量向量更新…";
    try {
      const response = await fetch(`/api/knowledge/gpumd/${action}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "任务启动失败");
      await refreshGpumdKnowledgeStatus();
    } catch (error) {
      gpumdKnowledgeStatus.textContent = "启动失败";
      gpumdKnowledgeStatus.dataset.state = "error";
      gpumdKnowledgeMessage.textContent = error.message || "任务启动失败";
      gpumdSyncDocs.disabled = false;
    }
  }

  async function loadTaskBehaviorSettings() {
    try {
      const response = await fetch("/api/settings/task-behavior");
      if (!response.ok) throw new Error("无法读取任务设置");
      const data = await response.json();
      taskBehaviorSettings = {
        max_concurrency: Number(data.max_concurrency || 2),
        retry_count: Number(data.retry_count || 0),
        notify_on_completion: Boolean(data.notify_on_completion),
      };
      fillSettingsForm();
      taskBehaviorMessage.textContent = "";
    } catch (error) {
      taskBehaviorMessage.textContent = error.message || "无法读取任务设置";
    }
  }

  function renderEvolutionExperiences(data) {
    const counts = data.counts || {};
    evolutionCandidateCount.textContent = Number(counts.candidate || 0).toLocaleString("zh-CN");
    evolutionVerifiedCount.textContent = Number(counts.verified || 0).toLocaleString("zh-CN");
    evolutionRejectedCount.textContent = Number(counts.rejected || 0).toLocaleString("zh-CN");
    const experiences = data.experiences || [];
    evolutionStatus.textContent = `${experiences.length} 条`;
    evolutionStatus.dataset.state = "ready";
    evolutionExperienceList.innerHTML = "";
    if (!experiences.length) {
      const empty = document.createElement("p");
      empty.className = "knowledge-message";
      empty.textContent = "当前筛选条件下还没有经验。";
      evolutionExperienceList.appendChild(empty);
      return;
    }
    const statusLabels = { candidate: "候选", verified: "已验证", rejected: "已拒绝" };
    experiences.forEach((experience) => {
      const card = document.createElement("article");
      card.className = "evolution-card";
      card.dataset.status = experience.status || "candidate";

      const heading = document.createElement("div");
      heading.className = "evolution-card-heading";
      const situation = document.createElement("strong");
      situation.textContent = experience.situation || "未命名经验";
      const badge = document.createElement("span");
      badge.className = "evolution-badge";
      badge.dataset.status = experience.status || "candidate";
      badge.textContent = statusLabels[experience.status] || experience.status;
      heading.append(situation, badge);

      const lesson = document.createElement("p");
      lesson.textContent = experience.lesson || "";
      if (experience.task_pattern) lesson.title = `任务模式：${experience.task_pattern}`;
      const evidence = document.createElement("small");
      evidence.textContent = `使用 ${experience.usage_count || 0} 次 · 正向 ${experience.positive_evidence || 0} · 负向 ${experience.negative_evidence || 0} · 置信度 ${Math.round(Number(experience.confidence || 0) * 100)}%`;

      const actions = document.createElement("div");
      actions.className = "evolution-actions";
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "ghost-btn";
      approve.textContent = "有效";
      approve.addEventListener("click", () => submitEvolutionFeedback(experience.experience_id, true));
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "ghost-btn danger-btn";
      reject.textContent = "无效 / 回滚";
      reject.addEventListener("click", () => submitEvolutionFeedback(experience.experience_id, false));
      actions.append(approve, reject);
      card.append(heading, lesson, evidence, actions);
      evolutionExperienceList.appendChild(card);
    });
  }

  async function refreshEvolutionExperiences() {
    if (!settingsDialog || settingsDialog.hidden) return;
    evolutionStatus.textContent = "读取中";
    evolutionStatus.dataset.state = "running";
    evolutionMessage.textContent = "";
    const params = new URLSearchParams();
    if (currentProjectId) params.set("project_id", currentProjectId);
    if (evolutionFilter.value) params.set("status", evolutionFilter.value);
    try {
      const response = await fetch(`/api/evolution/experiences?${params.toString()}`);
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || "无法读取进化经验");
      renderEvolutionExperiences(data);
    } catch (error) {
      evolutionStatus.textContent = "读取失败";
      evolutionStatus.dataset.state = "error";
      evolutionMessage.textContent = error.message || "无法读取进化经验";
    }
  }

  async function submitEvolutionFeedback(experienceId, positive) {
    evolutionMessage.textContent = positive ? "正在记录正向反馈…" : "正在执行降权或回滚…";
    try {
      const response = await fetch("/api/evolution/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          experience_id: experienceId,
          positive,
          project_id: currentProjectId || "",
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || "反馈保存失败");
      evolutionMessage.textContent = positive ? "正向证据已记录。" : "负向证据已记录，必要时经验会自动回滚。";
      await refreshEvolutionExperiences();
    } catch (error) {
      evolutionMessage.textContent = error.message || "反馈保存失败";
    }
  }

  async function saveTaskBehaviorSettings() {
    let notifyOnCompletion = settingTaskNotifications.checked;
    if (notifyOnCompletion) {
      if (!("Notification" in window)) {
        notifyOnCompletion = false;
        settingTaskNotifications.checked = false;
        taskBehaviorMessage.textContent = "当前浏览器不支持系统通知。";
      } else if (Notification.permission !== "granted") {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          notifyOnCompletion = false;
          settingTaskNotifications.checked = false;
          taskBehaviorMessage.textContent = "浏览器未允许通知，已保持关闭。";
        }
      }
    }
    const payload = {
      max_concurrency: Number(settingTaskConcurrency.value),
      retry_count: Number(settingTaskRetries.value),
      notify_on_completion: notifyOnCompletion,
    };
    const response = await fetch("/api/settings/task-behavior", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.detail || "任务设置保存失败");
    taskBehaviorSettings = { ...data.settings };
    return taskBehaviorSettings;
  }

  function renderRuntimeDiagnostics(data) {
    latestRuntimeDiagnostics = data;
    runtimeDiagnosticGrid.innerHTML = "";
    const items = data.items || [];
    items.forEach((item) => {
      const card = document.createElement("div");
      card.className = "diagnostic-item";
      const dot = document.createElement("span");
      dot.className = "diagnostic-dot";
      dot.dataset.state = item.state || "unavailable";
      const title = document.createElement("strong");
      title.textContent = item.label || item.id;
      const detail = document.createElement("small");
      detail.textContent = item.detail || "无状态信息";
      detail.title = detail.textContent;
      card.append(dot, title, detail);
      runtimeDiagnosticGrid.appendChild(card);
    });
    const unavailable = items.filter((item) => item.state === "unavailable").length;
    const degraded = items.filter((item) => item.state === "degraded").length;
    runtimeDiagnosticStatus.textContent = unavailable ? `${unavailable} 项不可用` : (degraded ? `${degraded} 项需注意` : "全部可用");
    runtimeDiagnosticStatus.dataset.state = unavailable ? "error" : (degraded ? "warning" : "ready");
    exportRuntimeDiagnosticsButton.disabled = false;
  }

  async function refreshRuntimeDiagnostics() {
    runtimeDiagnosticStatus.textContent = "检查中";
    runtimeDiagnosticStatus.dataset.state = "running";
    refreshRuntimeDiagnosticsButton.disabled = true;
    try {
      const response = await fetch("/api/diagnostics");
      if (!response.ok) throw new Error("运行诊断暂不可用");
      renderRuntimeDiagnostics(await response.json());
    } catch (error) {
      runtimeDiagnosticStatus.textContent = "检查失败";
      runtimeDiagnosticStatus.dataset.state = "error";
      runtimeDiagnosticGrid.innerHTML = "";
      const message = document.createElement("p");
      message.className = "knowledge-message";
      message.textContent = error.message || "运行诊断暂不可用";
      runtimeDiagnosticGrid.appendChild(message);
    } finally {
      refreshRuntimeDiagnosticsButton.disabled = false;
    }
  }

  function exportRuntimeDiagnostics() {
    if (!latestRuntimeDiagnostics) return;
    const blob = new Blob([JSON.stringify(latestRuntimeDiagnostics, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `miniclaw-diagnostics-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function scrollMessagesToBottom(force = false) {
    if (force || webchatSettings.autoScroll) msgs.scrollTop = msgs.scrollHeight;
  }

  applySettings();
  systemThemeQuery.addEventListener?.("change", () => {
    if (webchatSettings.theme === "system") applySettings();
  });

  let SKIP_BROWSE_DIRS = new Set([".idea", "__pycache__", ".git", "web", "node_modules", ".venv", "sessions"]);
  const STRUCTURE_EXTS = new Set(["xyz", "dump", "lammpstrj", "lmp", "data", "xsf"]);
  let VISUAL_FILE_EXTS = new Set([
    "gif", "png", "jpg", "jpeg", "xyz", "dump", "lammpstrj", "lmp", "data", "csv",
  ]);

  // ── Configure marked.js ──
  if (typeof marked !== "undefined") {
    marked.setOptions({
      breaks: true,
      gfm: true,
      highlight: function (code, lang) {
        if (lang && typeof hljs !== "undefined" && hljs.getLanguage(lang)) {
          try {
            return hljs.highlight(code, { language: lang }).value;
          } catch (_) {}
        }
        if (typeof hljs !== "undefined") {
          try {
            return hljs.highlightAuto(code).value;
          } catch (_) {}
        }
        return code;
      },
    });
  }

  function vizStorageKey() {
    return VIZ_STORAGE_PREFIX + currentChatId;
  }

  function saveVizState() {
    localStorage.setItem(
      vizStorageKey(),
      JSON.stringify({ media: savedMedia, structures: savedStructures })
    );
  }

  function assetUrl(path, bustCache = false) {
    let url = `/api/asset?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(currentProjectId || "")}`;
    if (bustCache) url += `&_=${Date.now()}`;
    return url;
  }

  // 跟踪已展示的媒体/结构卡片，同路径覆盖时移除旧卡片
  const _mediaCardsByPath = new Map();
  const _structureCardsByPath = new Map();

  // ── 安全 HTML 清洗 ──
  function sanitizeHTML(html) {
    const div = document.createElement("div");
    div.innerHTML = html;
    // 移除危险标签和属性
    const dangerous = div.querySelectorAll("script, iframe, object, embed, link[rel=stylesheet]");
    dangerous.forEach((el) => el.remove());
    // 移除所有 on* 事件属性
    div.querySelectorAll("*").forEach((el) => {
      Array.from(el.attributes).forEach((attr) => {
        if (attr.name.startsWith("on")) el.removeAttribute(attr.name);
      });
      // 安全处理 style 属性（移除 expression/javascript:）
      if (el.hasAttribute("style")) {
        const style = el.getAttribute("style");
        if (/expression|javascript:|behavior/i.test(style)) {
          el.removeAttribute("style");
        }
      }
    });
    return div.innerHTML;
  }

  // 增强版 Markdown 渲染（使用 marked.js + highlight.js + 安全清洗）
  function renderMarkdown(text) {
    if (!text) return "";
    if (typeof marked !== "undefined") {
      try {
        let html = marked.parse(text);
        // 为代码块添加复制按钮 — 使用安全的 DOM 方式而非 HTML 字符串拼接
        const tmp = document.createElement("div");
        tmp.innerHTML = html;
        tmp.querySelectorAll("pre > code").forEach((codeEl) => {
          const rawCode = codeEl.textContent || "";
          const pre = codeEl.parentElement;
          const wrapper = document.createElement("div");
          wrapper.className = "code-block-wrapper";
          const btn = document.createElement("button");
          btn.className = "copy-btn";
          btn.title = "复制代码";
          btn.textContent = "📋";
          btn.addEventListener("click", () => {
            navigator.clipboard.writeText(rawCode).then(() => {
              btn.textContent = "✓";
              setTimeout(() => { btn.textContent = "📋"; }, 1500);
            }).catch(() => {});
          });
          wrapper.appendChild(btn);
          pre.parentNode.insertBefore(wrapper, pre);
          wrapper.appendChild(pre);
        });
        html = sanitizeHTML(tmp.innerHTML);
        return html;
      } catch (e) {
        console.error("marked parse error:", e);
      }
    }
    // Fallback: 简易渲染（支持标题、表格、代码块等常用 Markdown）
    let html = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    // 代码块
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // 标题
    html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    // 表格（简易）
    html = html.replace(/^\|(.+)\|$/gm, function(match) {
      if (/^\|[-:\s|]+\|$/.test(match)) return ''; // 分隔行跳过
      const cells = match.slice(1, -1).split('|').map(function(c) { return '<td>' + c.trim() + '</td>'; });
      return '<tr>' + cells.join('') + '</tr>';
    });
    html = html.replace(/(<tr>.*<\/tr>\n?)+/g, '<table>$&</table>');
    // 水平线
    html = html.replace(/^---+$/gm, '<hr>');
    // 粗体/斜体
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // 无序列表
    html = html.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // 换行
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  // ── 工具调用卡片 ──
  function createToolCallCard(toolName, toolArgs, resultPreview) {
    const card = document.createElement("details");
    card.className = "tool-call-card";
    const summary = document.createElement("summary");
    summary.className = "tool-call-summary";
    summary.innerHTML = `<span class="tool-icon">🔧</span> <strong>${toolName}</strong>`;
    card.appendChild(summary);

    const body = document.createElement("div");
    body.className = "tool-call-body";

    // 参数区
    if (toolArgs && Object.keys(toolArgs).length > 0) {
      const argsDiv = document.createElement("div");
      argsDiv.className = "tool-call-section";
      argsDiv.innerHTML = '<div class="tool-call-label">📥 参数</div>';
      const argsPre = document.createElement("pre");
      argsPre.className = "tool-call-json";
      try {
        argsPre.textContent = JSON.stringify(toolArgs, null, 2);
      } catch (_) {
        argsPre.textContent = String(toolArgs);
      }
      argsDiv.appendChild(argsPre);
      body.appendChild(argsDiv);
    }

    // 结果区
    if (resultPreview !== undefined && resultPreview !== null) {
      const resultDiv = document.createElement("div");
      resultDiv.className = "tool-call-section";
      resultDiv.innerHTML = '<div class="tool-call-label">📤 结果</div>';
      const resultPre = document.createElement("pre");
      resultPre.className = "tool-call-result";
      const resultText = String(resultPreview);
      resultPre.textContent = resultText.length > 2000
        ? resultText.slice(0, 2000) + `\n... [truncated ${resultText.length - 2000} chars]`
        : resultText;
      resultDiv.appendChild(resultPre);
      body.appendChild(resultDiv);
    }

    card.appendChild(body);
    return card;
  }

  // ── 解析消息中的工具调用标记 ──
  function parseToolCallsFromMessage(text) {
    const calls = [];
    // 匹配 [tool:name] 格式
    const toolRegex = /\[(?:tool|🔧):([^\]]+)\]\s*([\s\S]*?)(?=\[(?:tool|🔧):|$)/g;
    let match;
    while ((match = toolRegex.exec(text)) !== null) {
      const name = match[1].trim();
      const body = match[2].trim();
      if (body && body.length < 5000) {
        calls.push({ name, args: null, result: body });
      }
    }
    return calls;
  }

  function addToolCallCardsAfterMsg(container, text) {
    const calls = parseToolCallsFromMessage(text);
    calls.forEach((c) => {
      container.appendChild(createToolCallCard(c.name, null, c.result));
    });
  }

  function addMsg(role, text) {
    const d = document.createElement("div");
    d.className = role;
    if (role === "agent" || role === "system") {
      d.innerHTML = renderMarkdown(text);
      if (role === "agent") {
        addToolCallCardsAfterMsg(d, text);
      }
    } else {
      d.textContent = text;
    }
    msgs.appendChild(d);
    scrollMessagesToBottom();
  }

  function setChatProgress(active, text, percent, projectId = currentProjectId) {
    if (projectId) {
      if (active) runningProjectIds.add(projectId);
      else runningProjectIds.delete(projectId);
    }
    // A background project may still stream after the user switches tabs.
    // Keep its state, but never overwrite the newly selected project's UI.
    if (projectId && projectId !== currentProjectId) return;
    chatRunning = active;
    if (active && typeof percent === "number" && percent >= 0) {
      // 真实进度：停掉脉冲动画，显示百分比宽度
      chatProgress.classList.remove("active");
      chatProgress.classList.add("determinate");
      chatProgress.style.width = Math.min(100, Math.round(percent)) + "%";
    } else if (active) {
      // 不确定进度：脉冲动画
      chatProgress.classList.add("active");
      chatProgress.classList.remove("determinate");
      chatProgress.style.width = "";
    } else {
      chatProgress.classList.remove("active", "determinate");
      chatProgress.style.width = "";
    }
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
    _mediaCardsByPath.clear();
    _structureCardsByPath.clear();
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
    if (name === "structure") {
      // 延迟到下一帧等 layout 完成，确保 clientWidth/Height 正确
      requestAnimationFrame(() => {
        requestAnimationFrame(() => structureScenes.forEach((s) => s.onResize?.()));
      });
    }
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
    const res = await fetch(`/api/files?work_dir=${encodeURIComponent(target)}&project_id=${encodeURIComponent(currentProjectId || "")}`);
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
    const res = await fetch(`/api/files?work_dir=${encodeURIComponent(browseDir || workspaceRoot)}&project_id=${encodeURIComponent(currentProjectId || "")}`);
    const data = await res.json();
    if (data.parent_dir) {
      browseDir = data.parent_dir;
      await refreshFileList();
    }
  }

  function formatFileSize(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function previewArtifact(artifact) {
    const textExtensions = new Set([".txt", ".md", ".log", ".json", ".yaml", ".yml", ".csv", ".py", ".js", ".sh", ".ps1", ".lmp", ".in"]);
    artifactPreview.hidden = false;
    if (!textExtensions.has((artifact.ext || "").toLowerCase())) {
      artifactPreview.textContent = `${artifact.name}\n\n该文件可在“媒体 / 结构 3D”标签中加载，或从项目工作区直接打开。`;
      return;
    }
    artifactPreview.textContent = "正在加载预览…";
    try {
      const response = await fetch(assetUrl(artifact.path));
      if (!response.ok) throw new Error("无法读取文件");
      const text = await response.text();
      artifactPreview.textContent = text.length > 12000 ? `${text.slice(0, 12000)}\n\n… 已截断` : text;
    } catch (error) {
      artifactPreview.textContent = `预览失败：${error.message}`;
    }
  }

  async function refreshArtifacts() {
    if (!currentProjectId || !artifactList) return;
    try {
      const [artifactResponse, summaryResponse] = await Promise.all([
        fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/artifacts`),
        fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/summary`),
      ]);
      const data = await artifactResponse.json();
      const summary = await summaryResponse.json();
      if (!data.ok) return;
      const artifacts = data.artifacts || [];
      updateBadge(artifactCountEl, artifacts.length);
      artifactList.innerHTML = "";
      artifactPreview.hidden = true;
      const project = summary.project || {};
      const latest = summary.latest_artifact?.rel_path || "尚无产物";
      artifactSummary.textContent = `${project.message_count || 0} 条消息 · ${summary.artifact_count || 0} 个文件 · 最新：${latest}`;
      if (!artifacts.length) {
        artifactList.innerHTML = '<p class="placeholder">项目生成的文件会显示在这里。</p>';
        return;
      }
      artifacts.forEach((artifact) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "artifact-item";
        const name = document.createElement("strong");
        name.textContent = artifact.name;
        const detail = document.createElement("span");
        detail.textContent = `${artifact.rel_path} · ${formatFileSize(artifact.size)}`;
        item.append(name, detail);
        item.addEventListener("click", () => previewArtifact(artifact));
        artifactList.appendChild(item);
      });
    } catch (_) {
      artifactSummary.textContent = "暂时无法读取项目产物。";
    }
  }

  async function refreshTimeline() {
    if (!currentProjectId || !runTimeline) return;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/timeline`);
      const data = await response.json();
      if (!data.ok) return;
      runTimeline.innerHTML = "";
      const events = data.events || [];
      if (!events.length) {
        runTimeline.innerHTML = '<p class="placeholder">项目运行记录会显示在这里。</p>';
        return;
      }
      events.forEach((event) => {
        const row = document.createElement("div");
        row.className = `timeline-event event-${event.kind}`;
        const when = document.createElement("time");
        when.textContent = new Date((event.at || 0) * 1000).toLocaleTimeString();
        const text = document.createElement("span");
        text.textContent = event.text;
        row.append(when, text);
        runTimeline.appendChild(row);
      });
    } catch (_) {}
  }

  async function refreshPlan() {
    if (!currentProjectId || !taskList) return;
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/plan`);
    const data = await response.json();
    if (!data.ok) return;
    projectSummary.textContent = data.summary || "项目总结会在每轮任务完成后更新。";
    taskList.innerHTML = "";
    if (!(data.tasks || []).length) {
      taskList.innerHTML = '<p class="placeholder">还没有任务计划。</p>';
      return;
    }
    data.tasks.forEach((task) => {
      const row = document.createElement("label");
      row.className = "task-item";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = Boolean(task.done);
      const title = document.createElement("span");
      title.textContent = task.title;
      row.append(check, title);
      check.addEventListener("change", async () => {
        await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/tasks/${encodeURIComponent(task.task_id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: check.checked }) });
        await refreshPlan();
      });
      taskList.appendChild(row);
    });
  }

  function appendMedia(mediaType, filePath, persist = true, bustCache = false) {
    // 同路径已有卡片 → 移除旧卡片
    const old = _mediaCardsByPath.get(filePath);
    if (old) {
      old.remove();
      mediaCount = Math.max(0, mediaCount - 1);
      const si = savedMedia.findIndex((m) => m.path === filePath);
      if (si >= 0) savedMedia.splice(si, 1);
    }

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
    img.src = assetUrl(filePath, bustCache);
    img.onload = () => setVizProgress(false);
    img.onerror = () => setVizProgress(false, "媒体加载失败");
    card.appendChild(img);
    mediaGallery.appendChild(card);
    mediaGallery.scrollTop = mediaGallery.scrollHeight;
    _mediaCardsByPath.set(filePath, card);
    mediaCount += 1;
    updateBadge(mediaCountEl, mediaCount);
    switchTab("media");
    if (persist) {
      savedMedia.push({ mediaType, path: filePath });
      saveVizState();
    }
  }

  function splitCsvLine(line) {
    return line.split(",").map((v) => v.trim());
  }

  function normalizeHeaderName(value) {
    return String(value || "").trim().toLowerCase().replace(/[^a-z0-9_./-]+/g, "_");
  }

  function isNumericCell(value) {
    return Number.isFinite(parseFloat(value));
  }

  function inferCsvChartMeta(headers, filePath) {
    const lowerPath = String(filePath || "").toLowerCase();
    const names = (headers || []).map(normalizeHeaderName);
    const joined = `${lowerPath} ${names.join(" ")}`;
    const has = (...words) => words.some((word) => joined.includes(word));
    const xName = headers?.[0] || "X";
    const yName = headers?.[1] || "Y";

    if (has("strain", "defo", "epsilon") && has("stress", "sigma", "pxx", "pyy", "pzz")) {
      return { title: "应力-应变曲线 (CSV)", xLabel: "应变", yLabel: "应力" };
    }
    if (has("energy", "etotal", "pe", "poteng", "ke", "kineng", "enthalpy", "ecoh", "cohesive")) {
      return { title: "系统能量曲线 (CSV)", xLabel: xName, yLabel: yName };
    }
    if (has("temperature", "temp")) {
      return { title: "温度演化曲线 (CSV)", xLabel: xName, yLabel: yName };
    }
    if (has("pressure", "press")) {
      return { title: "压力演化曲线 (CSV)", xLabel: xName, yLabel: yName };
    }
    return { title: "CSV 曲线", xLabel: xName, yLabel: yName };
  }

  function parseCsvPlotData(text, filePath = "") {
    const xs = [];
    const ys = [];
    let headers = null;
    for (const line of text.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const parts = splitCsvLine(t);
      if (parts.length < 2) continue;
      if (!headers && (!isNumericCell(parts[0]) || !isNumericCell(parts[1]))) {
        headers = parts;
        continue;
      }
      const x = parseFloat(parts[0]);
      const y = parseFloat(parts[1]);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        xs.push(x);
        ys.push(y);
      }
    }
    return { xs, ys, meta: inferCsvChartMeta(headers, filePath) };
  }

  function drawCsvCurveOnCanvas(canvas, xs, ys, meta = {}) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = 640;
    const h = 360;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.scale(dpr, dpr);
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
    ctx.fillStyle = "#d8d8d8";
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillText(meta.yLabel || "Y", 10, 18);
    ctx.fillText(meta.xLabel || "X", pad.l + plotW - 42, h - 12);
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

  async function appendCsvChart(filePath, persist = true, bustCache = false) {
    // 同路径已有卡片 → 移除旧卡片
    const old = _mediaCardsByPath.get(filePath);
    if (old) {
      old.remove();
      mediaCount = Math.max(0, mediaCount - 1);
      const si = savedMedia.findIndex((m) => m.path === filePath);
      if (si >= 0) savedMedia.splice(si, 1);
    }

    removePlaceholder(mediaGallery);
    setVizProgress(true, "解析 CSV…");
    try {
      const text = await (await fetch(assetUrl(filePath, bustCache))).text();
      const { xs, ys, meta } = parseCsvPlotData(text, filePath);
      if (xs.length < 2) {
        setVizProgress(false, "CSV 无有效数据");
        return;
      }
      const card = document.createElement("article");
      card.className = "media-card";
      const time = new Date().toLocaleTimeString();
      card.innerHTML = `
        <div class="media-card-head"><span>${meta.title}</span><time>${time}</time></div>
        <p class="media-caption">${filePath}</p>`;
      const canvas = document.createElement("canvas");
      canvas.className = "csv-chart-canvas";
      drawCsvCurveOnCanvas(canvas, xs, ys, meta);
      card.appendChild(canvas);
      mediaGallery.appendChild(card);
      mediaGallery.scrollTop = mediaGallery.scrollHeight;
      _mediaCardsByPath.set(filePath, card);
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
    let animPaused = false;

    // 立即渲染第一帧，避免黑屏
    renderer.render(scene, camera);

    function animate() {
      frameId = requestAnimationFrame(animate);
      if (animPaused) {
        // 保持最后一帧可见（不擦除）
        return;
      }
      rot += 0.003;
      camera.position.x = dist * Math.cos(rot);
      camera.position.z = dist * Math.sin(rot);
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
    }
    animate();
    // 只在容器有可见尺寸时才启用动画，否则暂停以节省 GPU/CPU
    const visObserver = new IntersectionObserver((entries) => {
      const wasPaused = animPaused;
      animPaused = !entries[0].isIntersecting;
      // 从暂停恢复时立刻重绘一帧
      if (wasPaused && !animPaused) {
        renderer.render(scene, camera);
      }
    }, { threshold: 0.01 });
    visObserver.observe(container);
    return {
      baseSize,
      setRadius(r) { updateInstanceMatrices(r); },
      onResize() {
        const w = container.clientWidth || 400;
        const h = container.clientHeight || 280;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
        renderer.render(scene, camera);
      },
      dispose() {
        if (frameId) cancelAnimationFrame(frameId);
        visObserver.disconnect();
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
      // 记录到"已移除"集合，防止服务端 viz_done 在刷新时重新加载
      try {
        const raw = localStorage.getItem(VIZ_REMOVED_KEY);
        const removed = raw ? JSON.parse(raw) : [];
        if (!removed.includes(label)) {
          removed.push(label);
          localStorage.setItem(VIZ_REMOVED_KEY, JSON.stringify(removed.slice(-200)));
        }
      } catch (_) {}
    }
    _structureCardsByPath.delete(card._label);
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
    _structureCardsByPath.set(label, card);
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
      const resp = await fetch(assetUrl(path));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const text = await resp.text();
      const atoms = parseStructureFile(text, ext);
      appendStructure(atoms, path, persist);
      switchTab("structure");
      setVizProgress(false);
    } catch (e) {
      console.error("结构加载失败:", path, e);
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
      if (mt === "csv") await appendCsvChart(data.path, true, true);
      else appendMedia(mt, data.path, true, true);
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
      el.classList.toggle("active", el.dataset.projectId === currentProjectId);
    });
  }

  function updateCurrentChatLabel() {
    if (!currentChatLabel) return;
    const project = projectsById.get(currentProjectId);
    currentChatLabel.textContent = project ? project.title : "Select a project";
    const objective = document.getElementById("project-objective");
    if (objective) objective.textContent = project?.objective || "Add a goal to give this project a clear direction.";
    const status = document.getElementById("project-status");
    if (status) {
      const current = project?.is_running ? "running" : (project?.status || "active");
      const labels = { active: "进行中", queued: "排队中", running: "运行中", paused: "已暂停", completed: "已完成", failed: "需处理", archived: "已归档" };
      status.className = `project-status status-${current}`;
      status.textContent = labels[current] || current;
    }
    const pauseButton = document.getElementById("btn-fork");
    if (pauseButton) {
      const paused = project?.status === "paused";
      pauseButton.dataset.state = paused ? "paused" : "active";
      pauseButton.textContent = paused ? "▶ 恢复" : "Ⅱ 暂停";
      pauseButton.disabled = !project || ["completed", "archived"].includes(project.status);
    }
    const locked = !project || ["paused", "completed", "archived"].includes(project.status);
    if (locked) {
      inp.disabled = true;
      sendBtn.disabled = true;
      sendBtn.textContent = project?.status === "paused" ? "已暂停" : "不可发送";
    } else if (!chatRunning) {
      inp.disabled = false;
      sendBtn.disabled = false;
      sendBtn.textContent = "发送";
    }
  }

  function compactNumber(value) {
    const n = Number(value || 0);
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  }

  async function refreshRuntimeStats() {
    if (!runtimeSummary || !runtimeDot) return;
    try {
      const response = await fetch("/api/stats");
      if (!response.ok) throw new Error("stats unavailable");
      const data = await response.json();
      const stats = data.agent || {};
      const runs = Number(stats.total_runs || 0);
      const tokens = Number(stats.total_tokens || 0);
      const errorRate = Number(stats.error_rate || 0);
      runtimeDot.classList.toggle("degraded", errorRate > 0.1);
      runtimeSummary.textContent = runs
        ? `${compactNumber(runs)} 次运行 · ${compactNumber(tokens)} tokens`
        : "系统就绪，等待任务";
    } catch (_) {
      runtimeDot.classList.add("offline");
      runtimeSummary.textContent = "运行状态暂不可用";
    }
  }

  function notifyCompletedProjects(previousProjects, projects) {
    projects.forEach((project) => {
      const previous = previousProjects.get(project.project_id);
      if (!previous) return;
      const wasRunning = previous.is_running || ["queued", "running"].includes(previous.status);
      const isRunning = project.is_running || ["queued", "running"].includes(project.status);
      if (!wasRunning || isRunning) return;
      showTaskCompletionNotification(project, project.status === "failed");
    });
  }

  function showTaskCompletionNotification(project, failed = false) {
    if (!taskBehaviorSettings.notify_on_completion || !("Notification" in window)) return;
    if (Notification.permission !== "granted" || document.visibilityState === "visible") return;
    new Notification(failed ? "MiniClaw 任务需要处理" : "MiniClaw 任务已完成", {
      body: project?.title || "后台项目任务",
      tag: `miniclaw-project-${project?.project_id || "current"}`,
    });
  }

  async function loadSidebar() {
    const r = await fetch("/api/projects");
    const data = await r.json();
    const list = data.projects || [];
    const previousProjects = new Map(projectsById);
    projectsById.clear();
    list.forEach((project) => projectsById.set(project.project_id, project));
    notifyCompletedProjects(previousProjects, list);
    convList.innerHTML = "";
    list.forEach((project) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "conv-item";
      btn.dataset.projectId = project.project_id;
      const head = document.createElement("div");
      head.className = "project-item-head";
      const strong = document.createElement("strong");
      strong.textContent = project.title;
      head.appendChild(strong);
      const state = document.createElement("span");
      const visibleStatus = project.is_running ? "running" : project.status;
      const labels = { active: "进行中", queued: "排队中", running: "运行中", paused: "暂停", completed: "完成", failed: "需处理", archived: "归档" };
      state.className = `project-status status-${visibleStatus}`;
      state.textContent = labels[visibleStatus] || visibleStatus;
      head.appendChild(state);
      const pv = document.createElement("div");
      pv.className = "preview";
      pv.textContent = project.preview || project.objective || "尚未开始";
      btn.appendChild(head);
      btn.appendChild(pv);
      const meta = document.createElement("div");
      meta.className = "project-meta";
      meta.innerHTML = `<span>${project.message_count || 0} 条消息</span><span>${project.is_running ? "后台运行" : "随时可继续"}</span>`;
      btn.appendChild(meta);
      btn.addEventListener("click", () => selectChat(project.project_id));
      convList.appendChild(btn);
    });
    setActiveInSidebar();
    return list;
  }

  // ── 从服务端 viz_done 列表恢复漏掉的媒体/结构卡片 ──
  async function restoreVizFromServer(vizDonePaths) {
    if (!vizDonePaths || !vizDonePaths.length) return;
    const known = new Set([
      ...savedMedia.map((m) => m.path),
      ...savedStructures.map((s) => s.path),
    ]);
    // 读取用户手动移除的路径，避免刷新后复活
    let removedSet = new Set();
    try {
      const raw = localStorage.getItem(VIZ_REMOVED_KEY);
      if (raw) { JSON.parse(raw).forEach(function (p) { removedSet.add(p); }); }
    } catch (_) {}
    for (const rawPath of vizDonePaths) {
      const path = String(rawPath);
      if (known.has(path) || removedSet.has(path)) continue;
      known.add(path);
      const lower = path.toLowerCase();
      try {
        if (lower.endsWith(".gif")) {
          appendMedia("gif", path, true, true);
        } else if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
          appendMedia("image", path, true, true);
        } else if (lower.endsWith(".csv")) {
          await appendCsvChart(path, true, true);
        }
        // 注意：原始结构文件（.dump/.xyz 等）不在此自动恢复 —
        // 文件可能很大，用户可手动从文件浏览器加载。
      } catch (e) {
        console.error("恢复 viz 产物失败:", path, e);
      }
    }
    setVizProgress(false);
  }

  async function loadHistory() {
    msgs.innerHTML = "";
    const r = await fetch(`/api/history?chat_id=${encodeURIComponent(currentChatId)}`);
    const data = await r.json();
    (data.messages || []).forEach((m) => {
      if (m.role === "system") return;
      if (m.role === "tool") { addMsg("system", m.content || ""); return; }
      if (m.role === "user") addMsg("user", m.content || "");
      else if (m.role === "assistant") {
        const d = document.createElement("div");
        d.className = "agent";
        const content = m.content || "";
        d.innerHTML = renderMarkdown(content);
        addToolCallCardsAfterMsg(d, content);
        msgs.appendChild(d);
      }
    });
    scrollMessagesToBottom(true);
    // 恢复 SSE 断连期间服务端已完成的渲染产物（GIF / 结构 3D）
    if (data.viz_done) {
      await restoreVizFromServer(data.viz_done);
    }
  }

  async function selectChat(projectId) {
    const project = projectsById.get(projectId);
    if (!project) return;
    currentProjectId = projectId;
    currentChatId = project.chat_id;
    workspaceRoot = project.workspace_dir || workspaceRoot;
    browseDir = workspaceRoot;
    localStorage.setItem(PROJECT_STORAGE_KEY, currentProjectId);
    localStorage.setItem(CHAT_STORAGE_KEY, currentChatId);
    updateCurrentChatLabel();
    setActiveInSidebar();
    const running = Boolean(project.is_running || runningProjectIds.has(projectId));
    setChatProgress(running, running ? "Agent 正在后台运行…" : "就绪", undefined, projectId);
    updateCurrentChatLabel();
    await restoreVizState();
    await loadHistory();
    await refreshFileList();
    await refreshArtifacts();
    await refreshTimeline();
    await refreshPlan();
    inp.focus();
  }

  async function createNewChat() {
    if (!projectDialog) return;
    projectDialog.hidden = false;
    projectTitleInput?.focus();
  }

  async function createProject(title, objective) {
    const r = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, objective }),
    });
    const data = await r.json();
    if (!data.ok || !data.project) {
      alert("创建项目失败: " + (data.error || "未知错误"));
      return;
    }
    await loadSidebar();
    await selectChat(data.project.project_id);
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
    const projectId = currentProjectId;
    const chatId = currentChatId;
    if (!text || !projectId || runningProjectIds.has(projectId)) return;
    addMsg("user", text);
    inp.value = "";
    inp.style.height = "auto";
    const agDiv = document.createElement("div");
    agDiv.className = "agent";
    // 思考动画
    const thinkingEl = document.createElement("div");
    thinkingEl.className = "agent-thinking";
    thinkingEl.innerHTML = '<span>思考中</span><span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>';
    agDiv.appendChild(thinkingEl);
    msgs.appendChild(agDiv);
    scrollMessagesToBottom(true);
    setChatProgress(true, "等待响应…", undefined, projectId);

    let rawContent = "";
    let firstContent = false;
    let receivedDone = false;
    let taskFailed = false;

    // ── 统一渲染 agent 消息卡片 ──
    function finalizeAgentDiv(finalText) {
      if (thinkingEl && thinkingEl.parentNode) thinkingEl.remove();
      setChatProgress(false, undefined, undefined, projectId);
      if (finalText.trim()) {
        if (typeof marked !== "undefined") {
          agDiv.innerHTML = renderMarkdown(finalText);
          addToolCallCardsAfterMsg(agDiv, finalText);
        } else {
          agDiv.textContent = finalText;
        }
      } else if (!agDiv.textContent && !agDiv.innerHTML.trim()) {
        agDiv.textContent = "（模型未返回文本）";
      }
      scrollMessagesToBottom();
    }

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, chat_id: chatId, project_id: projectId, plan_mode: planMode }),
      });
      if (!resp.ok) throw new Error(await resp.text());
      if ((resp.headers.get("content-type") || "").includes("application/json")) {
        const error = await resp.json();
        throw new Error(error.error || "项目暂时无法运行");
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const x = await reader.read();
        if (x.done) break;
        buffer += decoder.decode(x.value, { stream: true });
        buffer = parseSseBuffer(buffer, (payload) => {
          if (payload.progress) {
            const p = payload.progress;
            if (p.total) {
              const pct = Math.round((p.current / p.total) * 100);
              setChatProgress(true, `Step ${p.current} / ${p.total} (${pct}%)`, pct, projectId);
            } else {
              setChatProgress(true, `Step ${p.current}`, undefined, projectId);
            }
          } else if (payload.viz) {
            handleVizEvent(payload);
          } else if (payload.delta) {
            if (!firstContent) {
              if (thinkingEl && thinkingEl.parentNode) thinkingEl.remove();
              firstContent = true;
            }
            rawContent += payload.delta;
            // 流式渲染：使用 marked 增量渲染
            if (typeof marked !== "undefined") {
              agDiv.innerHTML = renderMarkdown(rawContent) +
                '<span class="streaming-cursor">▊</span>';
            } else {
              agDiv.textContent = rawContent;
            }
            scrollMessagesToBottom();
          } else if (payload.done) {
            receivedDone = true;
            // 服务端返回的 full 是权威完整结果，始终用它
            finalizeAgentDiv(payload.full || rawContent);
            refreshFileList();
            if (planMode) {
              planMode = false;
              planModeCheckbox.checked = false;
              updateInputPlaceholder();
            }
          }
        });
      }
      // 兜底：处理 buffer 剩余数据（复用同一解析函数，避免遗漏 done 事件）
      if (buffer.trim()) {
        parseSseBuffer(buffer + "\n", (payload) => {
          if (payload.progress) {
            const p = payload.progress;
            if (p.total) setChatProgress(true, `Step ${p.current} / ${p.total}`, Math.round((p.current / p.total) * 100), projectId);
            else setChatProgress(true, `Step ${p.current}`, undefined, projectId);
          } else if (payload.viz) {
            handleVizEvent(payload);
          } else if (payload.delta) {
            if (!firstContent) { if (thinkingEl && thinkingEl.parentNode) thinkingEl.remove(); firstContent = true; }
            rawContent += payload.delta;
          } else if (payload.done) {
            receivedDone = true;
            finalizeAgentDiv(payload.full || rawContent);
            refreshFileList();
          }
        });
      }
      // 流正常结束但没收到 done → 用流式内容兜底渲染
      if (!receivedDone) {
        if (!firstContent && thinkingEl && thinkingEl.parentNode) thinkingEl.remove();
        if (rawContent.trim()) {
          if (typeof marked !== "undefined") {
            agDiv.innerHTML = renderMarkdown(rawContent);
            addToolCallCardsAfterMsg(agDiv, rawContent);
          } else {
            agDiv.textContent = rawContent;
          }
        }
      }
    } catch (e) {
      taskFailed = true;
      if (thinkingEl && thinkingEl.parentNode) thinkingEl.remove();
      if (!rawContent.trim()) {
        agDiv.textContent = "Error: " + e.message;
      }
      setChatProgress(false, undefined, undefined, projectId);
    }

    // ── 保险：没收到 done 事件时，从服务端拉历史确保最终回复不丢失 ──
    if (!receivedDone) {
      try {
        const hr = await fetch(`/api/history?chat_id=${encodeURIComponent(chatId)}`);
        const hd = await hr.json();
        const msgs = hd.messages || [];
        let lastAssistant = "";
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === "assistant") {
            lastAssistant = msgs[i].content || "";
            break;
          }
        }
        if (lastAssistant && lastAssistant !== rawContent) {
          if (!firstContent && thinkingEl && thinkingEl.parentNode) thinkingEl.remove();
          finalizeAgentDiv(lastAssistant);
        }
        // SSE 断开期间 OVITO 可能已完成 GIF 渲染 → 恢复服务端记录的 viz 产物
        if (hd.viz_done) {
          await restoreVizFromServer(hd.viz_done);
        }
      } catch (_) {}
    }

    await loadSidebar();
    const completedProject = projectsById.get(projectId);
    showTaskCompletionNotification(completedProject, taskFailed || completedProject?.status === "failed");
    updateCurrentChatLabel();
    setActiveInSidebar();
    await refreshArtifacts();
    await refreshTimeline();
    await refreshPlan();
  }

  document.getElementById("new-chat").addEventListener("click", createNewChat);

  taskForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = taskInput?.value.trim();
    if (!title || !currentProjectId) return;
    await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/tasks`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    taskInput.value = "";
    await refreshPlan();
  });
  runNextTaskButton?.addEventListener("click", async () => {
    if (!currentProjectId) return;
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/run-next`, { method: "POST" });
    const data = await response.json();
    if (!data.ok) { alert(data.error || "无法执行任务"); return; }
    await loadSidebar();
    updateCurrentChatLabel();
    await refreshTimeline();
    // 后台任务完成后刷新聊天历史 + 计划面板（每 2s 轮询，最长 5 分钟）
    const projectId = currentProjectId;
    let elapsed = 0;
    const pollInterval = 2000;
    const maxPoll = 300000; // 5 minutes
    const pollTimer = setInterval(async () => {
      elapsed += pollInterval;
      await loadSidebar();
      const project = projectsById.get(projectId);
      const stillRunning = project?.is_running || project?.status === "running" || project?.status === "queued";
      if (!stillRunning || elapsed >= maxPoll) {
        clearInterval(pollTimer);
        // 只在当前仍选中该项目时刷新
        if (currentProjectId === projectId) {
          await loadHistory();
          await refreshPlan();
          await refreshArtifacts();
          updateCurrentChatLabel();
        }
      }
    }, pollInterval);
  });

  function closeProjectDialog() {
    if (!projectDialog) return;
    projectDialog.hidden = true;
    projectForm?.reset();
  }

  document.getElementById("project-dialog-close").addEventListener("click", closeProjectDialog);
  document.getElementById("project-dialog-cancel").addEventListener("click", closeProjectDialog);
  projectDialog?.addEventListener("click", (event) => {
    if (event.target === projectDialog) closeProjectDialog();
  });
  projectForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = projectTitleInput?.value.trim() || "";
    if (!title) return;
    try {
      await createProject(title, projectObjectiveInput?.value.trim() || "");
      closeProjectDialog();
    } catch (error) {
      alert("创建项目失败: " + error.message);
    }
  });

  document.querySelectorAll(".quick-action").forEach((button) => {
    button.addEventListener("click", () => {
      inp.value = button.dataset.prompt || "";
      inp.dispatchEvent(new Event("input"));
      inp.focus();
    });
  });

  async function projectAction(action) {
    if (!currentProjectId) return;
    const resp = await fetch(`/api/projects/${encodeURIComponent(currentProjectId)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "未知错误");
    await loadSidebar();
    updateCurrentChatLabel();
    setActiveInSidebar();
    if (action === "resume") inp.focus();
  }

  document.getElementById("btn-fork").addEventListener("click", async () => {
    const project = projectsById.get(currentProjectId);
    if (!project) return;
    try {
      await projectAction(project.status === "paused" ? "resume" : "pause");
    } catch (e) {
      alert("更新项目失败: " + e.message);
    }
  });

  document.getElementById("btn-complete").addEventListener("click", async () => {
    if (!confirm("标记这个项目为已完成？")) return;
    try {
      await projectAction("complete");
    } catch (e) {
      alert("更新项目失败: " + e.message);
    }
  });

  // 导出对话
  document.getElementById("btn-export").addEventListener("click", () => {
    const url = `/api/export?chat_id=${encodeURIComponent(currentChatId)}&fmt=markdown`;
    window.open(url, "_blank");
  });

  document.getElementById("btn-delete-chat").addEventListener("click", async () => {
    if (!confirm("归档当前项目？历史记录会被保留。")) return;
    try {
      await projectAction("archive");
      await loadSidebar();
      const next = [...projectsById.values()].find((project) => project.status !== "archived");
      if (next) await selectChat(next.project_id);
      else await createNewChat();
    } catch (e) {
      alert("归档失败: " + e.message);
    }
  });

  // 对话搜索/过滤
  const convSearch = document.getElementById("conv-search");
  if (convSearch) {
    convSearch.addEventListener("input", () => {
      const filter = convSearch.value.toLowerCase();
      convList.querySelectorAll(".conv-item").forEach((el) => {
        const text = (el.textContent || "").toLowerCase();
        el.style.display = text.includes(filter) ? "" : "none";
      });
    });
  }

  // Project rename (double-click a project card)
  convList.addEventListener("dblclick", async (e) => {
    const item = e.target.closest(".conv-item");
    if (!item) return;
    const projectId = item.dataset.projectId;
    const nameSpan = item.querySelector("strong");
    if (!nameSpan) return;
    const oldName = nameSpan.textContent;
    const newName = prompt("重命名项目:", oldName);
    if (!newName || newName === oldName) return;
    try {
      const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newName }),
      });
      const data = await resp.json();
      if (data.ok) {
        await loadSidebar();
        updateCurrentChatLabel();
        return;
      }
    } catch (_) {}
  });

  planModeCheckbox?.addEventListener("change", () => {
    planMode = planModeCheckbox.checked;
    updateInputPlaceholder();
  });

  document.getElementById("btn-settings").addEventListener("click", openSettingsDialog);
  document.getElementById("settings-dialog-close").addEventListener("click", closeSettingsDialog);
  document.getElementById("settings-dialog-cancel").addEventListener("click", closeSettingsDialog);
  settingsDialog?.addEventListener("click", (event) => {
    if (event.target === settingsDialog) closeSettingsDialog();
  });
  settingsForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    webchatSettings = {
      theme: settingTheme.value,
      messageSize: settingMessageSize.value,
      codeWrap: settingCodeWrap.checked,
      autoScroll: settingAutoScroll.checked,
      enterToSend: settingEnterToSend.checked,
      showQuickActions: settingShowQuickActions.checked,
    };
    try {
      await saveTaskBehaviorSettings();
      saveSettings();
      closeSettingsDialog();
    } catch (error) {
      taskBehaviorMessage.textContent = error.message || "设置保存失败";
    }
  });
  document.getElementById("settings-reset").addEventListener("click", async () => {
    webchatSettings = { ...DEFAULT_SETTINGS };
    taskBehaviorSettings = { ...DEFAULT_TASK_BEHAVIOR };
    saveSettings();
    fillSettingsForm();
    try {
      await saveTaskBehaviorSettings();
      taskBehaviorMessage.textContent = "任务行为已恢复默认。";
    } catch (error) {
      taskBehaviorMessage.textContent = error.message || "默认设置保存失败";
    }
  });
  gpumdSyncDocs?.addEventListener("click", () => runGpumdKnowledgeAction("sync"));
  gpumdUpdateIndex?.addEventListener("click", () => runGpumdKnowledgeAction("index"));
  refreshRuntimeDiagnosticsButton?.addEventListener("click", refreshRuntimeDiagnostics);
  exportRuntimeDiagnosticsButton?.addEventListener("click", exportRuntimeDiagnostics);
  refreshEvolutionButton?.addEventListener("click", refreshEvolutionExperiences);
  evolutionFilter?.addEventListener("change", refreshEvolutionExperiences);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && settingsDialog && !settingsDialog.hidden) {
      closeSettingsDialog();
    }
  });

  sendBtn.addEventListener("click", send);
  inp.addEventListener("keydown", (e) => {
    const enterShortcut = webchatSettings.enterToSend
      ? e.key === "Enter" && !e.shiftKey
      : e.key === "Enter" && (e.ctrlKey || e.metaKey);
    if (enterShortcut) { e.preventDefault(); send(); }
  });
  // 自动调整 textarea 高度
  inp.addEventListener("input", () => {
    inp.style.height = "auto";
    inp.style.height = Math.min(inp.scrollHeight, 150) + "px";
  });
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

  document.getElementById("viz-toggle").addEventListener("click", () => {
    document.getElementById("viz-panel").classList.toggle("collapsed");
    localStorage.setItem(VIZ_COLLAPSED_KEY, document.getElementById("viz-panel").classList.contains("collapsed") ? "1" : "0");
    structureScenes.forEach((s) => s.onResize?.());
  });

  if (localStorage.getItem(VIZ_COLLAPSED_KEY) === "1") {
    document.getElementById("viz-panel").classList.add("collapsed");
  }

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

  let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => structureScenes.forEach((s) => s.onResize?.()), 150);
});

  async function init() {
    await loadConfig();
    const list = await loadSidebar();
    const ids = list.map((project) => project.project_id);
    if (!ids.length) {
      await createNewChat();
      return;
    }
    if (!ids.includes(currentProjectId)) currentProjectId = list[0].project_id;
    await selectChat(currentProjectId);
    await refreshRuntimeStats();
  }

  init();
  refreshRuntimeStats();
  window.setInterval(refreshRuntimeStats, 30000);
