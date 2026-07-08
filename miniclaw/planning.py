"""
planning.py — 异构 Planning Agent 模块

提供:
  - 全局 ProjectManager 引用注册 (仿 message_tool.py 模式)
  - plan_add_tasks 工具 — Planning Agent 用来写入任务计划
  - Planning 专用 system prompt
  - Auto-check hook — Agent 执行完已计划任务后自动勾选
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .hooks import HookContext, HookPriority, hook_system
from .logger import get_logger
from .projects import ProjectManager
from .tools.registry import tool_registry

logger = get_logger(__name__)

# ── 全局 ProjectManager 引用 (仿 message_tool.py 模式) ──

_plan_project_manager: Optional[ProjectManager] = None


def register_plan_project_manager(pm: ProjectManager) -> None:
    """由 main.py 在 WebChatAdapter 创建后调用。"""
    global _plan_project_manager
    _plan_project_manager = pm
    logger.debug("Registered project manager for planning")


def unregister_plan_project_manager() -> None:
    global _plan_project_manager
    _plan_project_manager = None


# ── Planning Agent 系统提示 ──

PLANNING_SYSTEM_PROMPT = """\
You are a **Planning Agent**. Your sole responsibility is to analyze a goal and decompose it into concrete, actionable steps. You do NOT execute tasks — you only plan.

## Instructions
1. Carefully understand the user's goal.
2. Break it down into **3–8 sequential, independently-verifiable steps**.
3. Each step should clearly state **WHAT** to achieve (not how to implement it).
4. Call `plan_add_tasks` with the `project_id` provided below and the list of task titles.
5. After adding tasks, write a brief plain-text summary of the plan so the user knows what will happen.

## Task Quality Guidelines
- Each task title should be under 200 characters, clear and specific.
- Tasks should be logically ordered: early tasks produce outputs that later tasks depend on.
- Avoid vague tasks like "analyze data" — prefer "统计 data.csv 中各列的基本描述统计量".
- If the goal is vague, ask ONE clarifying question and propose a plan for the most likely interpretation."""


def build_planning_system_prompt(
    project_id: str,
    project_title: str = "",
    project_objective: str = "",
) -> str:
    """组装 Planning Agent 的完整 system prompt，含项目上下文。"""
    parts: List[str] = [PLANNING_SYSTEM_PROMPT]
    parts.append("\n## Current Project Context")
    parts.append(f"- **Project ID**: `{project_id}`")
    if project_title:
        parts.append(f"- **Title**: {project_title}")
    if project_objective:
        parts.append(f"- **Objective**: {project_objective}")
    parts.append(
        "\n**Important**: Use exactly the project_id shown above when calling "
        "`plan_add_tasks`."
    )
    return "\n".join(parts)


# ── plan_add_tasks 工具 ──

@tool_registry.register(
    name="plan_add_tasks",
    description=(
        "Add a list of tasks to the current project's plan. "
        "Each task needs a clear title describing what to accomplish."
    ),
    schema={
        "type": "function",
        "function": {
            "name": "plan_add_tasks",
            "description": (
                "Add one or more tasks to the project's execution plan. "
                "Call this after decomposing the user's goal into concrete "
                "sequential steps. Each task should describe WHAT to achieve, "
                "not HOW to implement it."
            ),
            "parameters": {
                "type": "object",
                "required": ["tasks", "project_id"],
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": (
                            "Ordered list of task objects. Each must have a "
                            "'title' field with a clear, actionable description "
                            "(max 200 chars)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "A clear, actionable task title (max 200 chars)",
                                }
                            },
                            "required": ["title"],
                        },
                    },
                    "project_id": {
                        "type": "string",
                        "description": (
                            "The project ID provided in the system prompt. "
                            "This must match exactly."
                        ),
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["planning"],
)
async def plan_add_tasks(
    tasks: List[Dict[str, str]],
    project_id: str,
    **kwargs,
) -> str:
    """Planning Agent 调用此工具将任务写入项目计划。"""
    pm = _plan_project_manager
    if pm is None:
        return (
            "Error: Project manager is not available. "
            "The planning system may not be fully initialized."
        )

    ctx = kwargs.get("_context") or {}
    context_project_id = (ctx.get("project_id") or "").strip()
    requested_project_id = (project_id or "").strip()
    effective_project_id = context_project_id or requested_project_id

    project = pm.get(effective_project_id)
    if project is None:
        return (
            f"Error: Project '{effective_project_id or requested_project_id}' not found. "
            "Double-check the project_id from the system prompt and try again."
        )

    if not tasks:
        return "Error: No tasks provided. Please supply at least one task with a non-empty title."

    added: List[str] = []
    for task in tasks:
        title = (task.get("title") or "").strip()
        if not title:
            continue
        result = pm.add_task(project.project_id, title)
        if result:
            added.append(title)

    if not added:
        return (
            "No tasks were added. Please provide at least one valid task "
            "with a non-empty title field."
        )

    # 更新项目摘要
    total = len(project.tasks)
    done = sum(1 for t in project.tasks if t.get("done"))
    pm.update_summary(
        project.project_id,
        f"已创建 {len(added)} 个新任务，共 {total} 个任务待执行。",
    )

    note = ""
    if context_project_id and requested_project_id and context_project_id != requested_project_id:
        note = (
            f"\n\nNote: ignored stale model-supplied project_id '{requested_project_id}' "
            f"and used current request project_id '{context_project_id}'."
        )

    return (
        f"Successfully added {len(added)} task(s):\n"
        + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(added))
        + f"\n\nProject now has {total} task(s), {done} already done."
        + note
    )


# ── Auto-check hook — Agent 执行完已计划任务后自动勾选 ──

@hook_system.on(
    "after_agent",
    priority=HookPriority.LOW,
    description="Auto-mark planned task as done after successful execution",
)
async def _auto_check_completed_task(ctx: HookContext) -> HookContext:
    """
    当 webchat.py 的 _run_next_task 在 AgentContext.metadata 中注入了
    _plan_task_id / _plan_project_id 时，Agent 成功完成后自动 mark done。
    """
    task_id = ctx.data.get("_plan_task_id")
    project_id = ctx.data.get("_plan_project_id")
    if not task_id or not project_id:
        return ctx

    pm = _plan_project_manager
    if pm is None:
        return ctx

    # 只在无错误时自动勾选
    if ctx.data.get("error"):
        return ctx

    updated = pm.update_task(project_id, task_id, done=True)
    if updated:
        # 更新摘要
        project = pm.get(project_id)
        if project:
            done = sum(1 for t in project.tasks if t.get("done"))
            total = len(project.tasks)
            pm.update_summary(
                project_id,
                f"任务进度：{done}/{total} 已完成。",
            )
        logger.info(
            "Auto-checked task %s in project %s (%s/%s done)",
            task_id, project_id,
            done if updated else "?", len(updated.tasks) if updated else "?",
        )

    return ctx
