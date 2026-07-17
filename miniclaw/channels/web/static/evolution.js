"use strict";

export function createEvolutionPanel({ getProjectId, settingsDialog }) {
  const evolutionStatus = document.getElementById("evolution-status");
  const evolutionCandidateCount = document.getElementById("evolution-candidate-count");
  const evolutionPendingCount = document.getElementById("evolution-pending-count");
  const evolutionCanaryCount = document.getElementById("evolution-canary-count");
  const evolutionVerifiedCount = document.getElementById("evolution-verified-count");
  const evolutionRejectedCount = document.getElementById("evolution-rejected-count");
  const evolutionFilter = document.getElementById("evolution-filter");
  const evolutionExperienceList = document.getElementById("evolution-experience-list");
  const evolutionMessage = document.getElementById("evolution-message");
  const refreshEvolutionButton = document.getElementById("refresh-evolution-experiences");
  const reflectionProposalCount = document.getElementById("reflection-proposal-count");
  const reflectionProposalList = document.getElementById("reflection-proposal-list");

  function renderEvolutionExperiences(data) {
    const counts = data.counts || {};
    evolutionCandidateCount.textContent = Number(counts.candidate || 0).toLocaleString("zh-CN");
    evolutionPendingCount.textContent = Number(counts.pending_evaluation || 0).toLocaleString("zh-CN");
    evolutionCanaryCount.textContent = Number(counts.canary || 0).toLocaleString("zh-CN");
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
    const statusLabels = {
      candidate: "候选",
      pending_evaluation: "待评测",
      canary: "灰度中",
      verified: "已验证",
      rejected: "已拒绝",
    };
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
      const workflow = document.createElement("small");
      const semanticSteps = experience.strategy?.steps || [];
      workflow.className = "evolution-workflow";
      workflow.textContent = semanticSteps.length
        ? `结构化步骤：${semanticSteps.map((step) => `${step.action}:${step.resource_type}`).join(" → ")}`
        : "旧版文本经验（等待新的任务样本升级）";
      const evidence = document.createElement("small");
      evidence.textContent = `来源项目 ${experience.source_project_count || 0} · 使用 ${experience.usage_count || 0} 次 · 正向 ${experience.positive_evidence || 0} · 负向 ${experience.negative_evidence || 0} · 置信度 ${Math.round(Number(experience.confidence || 0) * 100)}%`;

      const actions = document.createElement("div");
      actions.className = "evolution-actions";
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "ghost-btn";
      approve.textContent = "有效";
      approve.addEventListener("click", (event) => {
        submitEvolutionFeedback(experience.experience_id, true, event.currentTarget);
      });
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "ghost-btn danger-btn";
      reject.textContent = "无效 / 回滚";
      reject.addEventListener("click", () => {
        if (experience.status === "verified") submitEvolutionRollback(experience.experience_id);
        else submitEvolutionFeedback(experience.experience_id, false, reject);
      });
      actions.append(approve, reject);
      card.append(heading, lesson, workflow, evidence, actions);
      evolutionExperienceList.appendChild(card);
    });
  }

  async function refreshEvolutionExperiences({ preserveMessage = false } = {}) {
    if (!settingsDialog || settingsDialog.hidden) return;
    evolutionStatus.textContent = "读取中";
    evolutionStatus.dataset.state = "running";
    if (!preserveMessage) evolutionMessage.textContent = "";
    const params = new URLSearchParams();
    if (getProjectId()) params.set("project_id", getProjectId());
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

  function evolutionFeedbackMessage(experience, positive, thresholds = {}) {
    const status = experience.status || "candidate";
    if (!positive) {
      return status === "rejected"
        ? "负向证据已记录，该经验已停止使用。"
        : "负向证据已记录；达到拒绝门槛后会自动停止使用。";
    }
    if (status === "verified") return "有效反馈已记录，该经验已通过评估并启用。";
    if (status === "canary") return "有效反馈已记录，该经验正在灰度验证。";
    if (status === "pending_evaluation") return "有效反馈已记录，该经验正在等待离线评估。";
    if (status === "rejected") return "有效反馈已记录，但该经验仍处于停用状态。";
    const required = Number(thresholds.promotion_evidence || 3);
    const remaining = Math.max(0, required - Number(experience.positive_evidence || 0));
    return remaining > 0
      ? `有效反馈已记录；还需 ${remaining} 条正向证据，之后将自动进行安全评估。`
      : "有效反馈已记录；安全评估尚未通过，请继续积累真实任务样本。";
  }

  async function submitEvolutionFeedback(experienceId, positive, button = null) {
    evolutionMessage.textContent = positive ? "正在记录正向反馈…" : "正在执行降权或回滚…";
    if (button) button.disabled = true;
    try {
      const response = await fetch("/api/evolution/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          experience_id: experienceId,
          positive,
          project_id: getProjectId() || "",
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || "反馈保存失败");
      evolutionMessage.textContent = evolutionFeedbackMessage(
        data.experience || {}, positive, data.thresholds || {}
      );
      await refreshEvolutionExperiences({ preserveMessage: true });
    } catch (error) {
      evolutionMessage.textContent = error.message || "反馈保存失败";
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function submitEvolutionRollback(experienceId) {
    evolutionMessage.textContent = "正在回滚已验证经验…";
    try {
      const response = await fetch("/api/evolution/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          experience_id: experienceId,
          project_id: getProjectId() || "",
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || "经验回滚失败");
      evolutionMessage.textContent = "经验已回滚并停止注入。";
      await refreshEvolutionExperiences({ preserveMessage: true });
    } catch (error) {
      evolutionMessage.textContent = error.message || "经验回滚失败";
    }
  }

  function renderReflectionProposals(reflections) {
    reflectionProposalCount.textContent = `${reflections.length} 条待审`;
    reflectionProposalList.innerHTML = "";
    if (!reflections.length) {
      const empty = document.createElement("p");
      empty.className = "knowledge-message";
      empty.textContent = "暂无待审反思提案。";
      reflectionProposalList.appendChild(empty);
      return;
    }
    reflections.forEach((reflection) => {
      const card = document.createElement("article");
      card.className = "evolution-card reflection-card";
      const title = document.createElement("strong");
      title.textContent = reflection.situation || "反思提案";
      const lesson = document.createElement("p");
      lesson.textContent = reflection.lesson || "";
      const meta = document.createElement("small");
      meta.textContent = `模型 ${reflection.model || "未知"} · 置信度 ${Math.round(Number(reflection.confidence || 0) * 100)}%`;
      const actions = document.createElement("div");
      actions.className = "evolution-actions";
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "ghost-btn";
      approve.textContent = "批准并重新评测";
      approve.addEventListener("click", () => reviewReflectionProposal(reflection.reflection_id, true));
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "ghost-btn danger-btn";
      reject.textContent = "拒绝";
      reject.addEventListener("click", () => reviewReflectionProposal(reflection.reflection_id, false));
      actions.append(approve, reject);
      card.append(title, lesson, meta, actions);
      reflectionProposalList.appendChild(card);
    });
  }

  async function refreshReflectionProposals() {
    const params = new URLSearchParams({ status: "proposed" });
    if (getProjectId()) params.set("project_id", getProjectId());
    try {
      const response = await fetch(`/api/evolution/reflections?${params.toString()}`);
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || "无法读取反思提案");
      renderReflectionProposals(data.reflections || []);
    } catch (error) {
      evolutionMessage.textContent = error.message || "无法读取反思提案";
    }
  }

  async function reviewReflectionProposal(reflectionId, approve) {
    evolutionMessage.textContent = approve ? "正在批准并重新评测…" : "正在拒绝提案…";
    try {
      const response = await fetch(`/api/evolution/reflections/${reflectionId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve, project_id: getProjectId() || "" }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || "反思提案审核失败");
      evolutionMessage.textContent = approve ? "提案已批准并完成重新评测。" : "提案已拒绝。";
      await Promise.all([refreshEvolutionExperiences(), refreshReflectionProposals()]);
    } catch (error) {
      evolutionMessage.textContent = error.message || "反思提案审核失败";
    }
  }


  refreshEvolutionButton?.addEventListener("click", refreshEvolutionExperiences);
  evolutionFilter?.addEventListener("change", refreshEvolutionExperiences);
  return {
    refreshExperiences: refreshEvolutionExperiences,
    refreshReflections: refreshReflectionProposals,
  };
}
