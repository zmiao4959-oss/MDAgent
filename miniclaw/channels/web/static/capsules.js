export function createResearchCapsulePanel({ getProjectId, formatFileSize, updateBadge, refreshTimeline }) {
  const list = document.getElementById("capsule-list");
  const status = document.getElementById("capsule-status");
  const createButton = document.getElementById("create-capsule");
  const count = document.getElementById("capsule-count");

  function verificationLabel(verification) {
    const labels = { verified: "已核验", changed: "有变化", missing: "有缺失", unreadable: "无法读取" };
    return verification ? (labels[verification.status] || verification.status) : "待核验";
  }

  function errorMessage(data, fallback) {
    const detail = data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    return data?.error || fallback;
  }

  async function verifyCapsule(capsuleId) {
    const projectId = getProjectId();
    status.textContent = "正在逐文件核验…";
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/capsules/${encodeURIComponent(capsuleId)}/verify`,
      { method: "POST" },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(errorMessage(data, "核验失败"));
    const report = data.verification;
    status.textContent = `核验完成：${verificationLabel(report)} · ${report.checked_file_count}/${report.expected_file_count} 个文件`;
    await refresh();
  }

  async function exportCapsule(capsuleId, includeFiles = true) {
    const projectId = getProjectId();
    const confirmation = includeFiles
      ? window.prompt(`导出包含实际文件的脱敏 ZIP，请准确输入：\nEXPORT ${capsuleId}`)
      : "";
    if (includeFiles && confirmation === null) return;
    status.textContent = "正在复核并生成脱敏 ZIP…";
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/capsules/${encodeURIComponent(capsuleId)}/export`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation, include_files: includeFiles }),
      },
    );
    if (!response.ok) {
      let data = {};
      try { data = await response.json(); } catch (_) {}
      throw new Error(errorMessage(data, "导出失败"));
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
    const filename = filenameMatch?.[1] || `research-${capsuleId.replace(":", "-")}.zip`;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    status.textContent = includeFiles ? "脱敏 ZIP 已下载。" : "清单 ZIP 已下载。";
    await refreshTimeline();
  }

  function actionButton(label, className, action, errorPrefix) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", () => action().catch((error) => {
      status.textContent = `${errorPrefix}：${error.message}`;
    }));
    return button;
  }

  function renderCard(capsule) {
    const card = document.createElement("article");
    card.className = "capsule-card";
    const head = document.createElement("div");
    head.className = "capsule-card-head";
    const identity = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = capsule.title || capsule.capsule_id;
    const meta = document.createElement("span");
    meta.textContent = `${capsule.file_count} 个文件 · ${formatFileSize(capsule.total_bytes)} · ${new Date((capsule.created_at || 0) * 1000).toLocaleString()}`;
    identity.append(title, meta);
    const badge = document.createElement("span");
    const state = capsule.verification?.status || "pending";
    badge.className = `capsule-badge capsule-${state}`;
    badge.textContent = verificationLabel(capsule.verification);
    head.append(identity, badge);

    const digest = document.createElement("code");
    digest.textContent = `${capsule.capsule_id} · SHA-256 ${capsule.manifest_sha256}`;
    const actions = document.createElement("div");
    actions.className = "capsule-actions";
    actions.append(
      actionButton("核验", "ghost-btn", () => verifyCapsule(capsule.capsule_id), "核验失败"),
      actionButton("仅清单", "ghost-btn", () => exportCapsule(capsule.capsule_id, false), "导出失败"),
      actionButton("脱敏 ZIP", "primary-btn", () => exportCapsule(capsule.capsule_id), "导出失败"),
    );
    card.append(head, digest, actions);
    return card;
  }

  async function refresh() {
    const projectId = getProjectId();
    if (!projectId || !list) return;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/capsules`);
      const data = await response.json();
      if (!response.ok) throw new Error(errorMessage(data, "读取失败"));
      const capsules = data.capsules || [];
      updateBadge(count, capsules.length);
      list.innerHTML = "";
      if (!capsules.length) {
        list.innerHTML = '<p class="placeholder">还没有科研胶囊。</p>';
        return;
      }
      capsules.forEach((capsule) => list.appendChild(renderCard(capsule)));
    } catch (error) {
      status.textContent = `暂时无法读取科研胶囊：${error.message}`;
    }
  }

  async function create() {
    const projectId = getProjectId();
    if (!projectId || !createButton) return;
    createButton.disabled = true;
    status.textContent = "正在冻结文件清单并计算哈希…";
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/capsules`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(errorMessage(data, "生成失败"));
      status.textContent = `胶囊 ${data.capsule.capsule_id} 已生成。`;
      await refresh();
      await refreshTimeline();
    } catch (error) {
      status.textContent = `生成失败：${error.message}`;
    } finally {
      createButton.disabled = false;
    }
  }

  createButton?.addEventListener("click", create);
  return { refresh };
}
