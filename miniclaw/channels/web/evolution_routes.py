"""Evolution HTTP routes kept separate from the WebChat transport."""
from pathlib import Path

from fastapi import HTTPException, Request

from ...config import WORKSPACE_DIR, config


def register_evolution_routes(app, adapter) -> None:
    def configured_source_repo() -> Path:
        if not config.evolution.source_evolution_enabled:
            raise HTTPException(status_code=403, detail="source evolution is disabled")
        value = config.evolution.source_repo_path
        if not value:
            raise HTTPException(status_code=400, detail="source_repo_path is not configured")
        repo = Path(value).expanduser().resolve()
        if not repo.is_dir() or not (repo / ".git").exists():
            raise HTTPException(status_code=400, detail="source_repo_path is not a Git repository")
        return repo

    @app.get("/api/evolution/source/proposals")
    async def list_source_proposals(status: str = ""):
        from ...learning.service import EvolutionService

        configured_source_repo()
        return {
            "ok": True,
            "proposals": EvolutionService(WORKSPACE_DIR).list_source_proposals(status or None),
        }

    @app.post("/api/evolution/source/proposals/detect")
    async def detect_source_proposals():
        from ...learning.service import EvolutionService

        repo = configured_source_repo()
        proposals = EvolutionService(WORKSPACE_DIR).detect_source_proposals(
            repo,
            min_occurrences=config.evolution.source_failure_min_occurrences,
            min_projects=config.evolution.source_failure_min_projects,
        )
        return {"ok": True, "proposals": proposals}

    @app.post("/api/evolution/source/proposals/{proposal_id}/review")
    async def review_source_proposal(proposal_id: str, request: Request):
        from ...learning.service import EvolutionService

        configured_source_repo()
        body = await request.json()
        approve = body.get("approve")
        if not isinstance(approve, bool):
            raise HTTPException(status_code=400, detail="boolean approve is required")
        try:
            proposal = EvolutionService(WORKSPACE_DIR).review_source_proposal(
                proposal_id, approve=approve
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "proposal": proposal}

    @app.post("/api/evolution/source/proposals/{proposal_id}/run")
    async def run_source_proposal(proposal_id: str, request: Request):
        from ...learning.service import EvolutionService

        repo = configured_source_repo()
        if not config.evolution.source_auto_patch_enabled:
            raise HTTPException(status_code=403, detail="automatic source patching is disabled")
        if adapter.source_patch_llm is None:
            raise HTTPException(status_code=503, detail="source patch LLM is unavailable")
        confirmation = str((await request.json()).get("confirmation", ""))
        if confirmation != f"RUN {proposal_id}":
            raise HTTPException(status_code=400, detail="run confirmation phrase does not match")
        try:
            experiment = await EvolutionService(
                WORKSPACE_DIR
            ).run_automated_source_experiment(
                proposal_id, repo, adapter.source_patch_llm
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "experiment": experiment}

    @app.get("/api/evolution/source/experiments")
    async def list_source_experiments(status: str = ""):
        from ...learning.service import EvolutionService

        configured_source_repo()
        return {
            "ok": True,
            "experiments": EvolutionService(WORKSPACE_DIR).list_source_experiments(status or None),
        }

    @app.post("/api/evolution/source/experiments/{experiment_id}/evaluate")
    async def evaluate_source_experiment(experiment_id: str):
        from ...learning.service import EvolutionService

        repo = configured_source_repo()
        try:
            experiment = EvolutionService(WORKSPACE_DIR).evaluate_source_experiment(
                experiment_id, repo
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "experiment": experiment}

    @app.post("/api/evolution/source/experiments/{experiment_id}/promote")
    async def promote_source_experiment(experiment_id: str, request: Request):
        from ...learning.service import EvolutionService

        repo = configured_source_repo()
        confirmation = str((await request.json()).get("confirmation", ""))
        try:
            experiment = EvolutionService(WORKSPACE_DIR).promote_source_experiment(
                experiment_id, repo, confirmation
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "experiment": experiment}

    @app.post("/api/evolution/source/experiments/{experiment_id}/rollback")
    async def rollback_source_experiment(experiment_id: str, request: Request):
        from ...learning.service import EvolutionService

        repo = configured_source_repo()
        confirmation = str((await request.json()).get("confirmation", ""))
        try:
            experiment = EvolutionService(WORKSPACE_DIR).rollback_source_experiment(
                experiment_id, repo, confirmation
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "experiment": experiment}

    @app.get("/api/evolution/experiences")
    async def list_evolution_experiences(status: str = "", project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        service = EvolutionService(workspace)
        experiences = service.list_experiences(status or None)
        all_experiences = service.list_experiences()
        counts = {
            "candidate": 0,
            "pending_evaluation": 0,
            "canary": 0,
            "verified": 0,
            "rejected": 0,
        }
        for item in all_experiences:
            item_status = item.get("status", "candidate")
            counts[item_status] = counts.get(item_status, 0) + 1
        return {
            "ok": True,
            "experiences": experiences,
            "counts": counts,
        }

    @app.post("/api/evolution/feedback")
    async def evolution_feedback(request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        experience_id = str(body.get("experience_id", "")).strip()
        project_id = str(body.get("project_id", "")).strip()
        positive = body.get("positive")
        if not experience_id or not isinstance(positive, bool):
            raise HTTPException(
                status_code=400,
                detail="experience_id and boolean positive are required",
            )
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        service = EvolutionService(workspace)
        experience = service.feedback(
            experience_id,
            positive,
            project_id=project_id,
            chat_id=project.chat_id if project else "",
        )
        if experience is None:
            raise HTTPException(status_code=404, detail="experience not found")
        return {
            "ok": True,
            "experience": experience.__dict__,
            "thresholds": {
                "promotion_evidence": service.experience_store.PROMOTION_EVIDENCE,
                "rejection_evidence": service.experience_store.REJECTION_EVIDENCE,
            },
        }

    @app.post("/api/evolution/rollback")
    async def evolution_rollback(request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        experience_id = str(body.get("experience_id", "")).strip()
        project_id = str(body.get("project_id", "")).strip()
        if not experience_id:
            raise HTTPException(status_code=400, detail="experience_id is required")
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        experience = EvolutionService(workspace).rollback(experience_id)
        if experience is None:
            raise HTTPException(status_code=404, detail="experience not found")
        return {"ok": True, "experience": experience.__dict__}

    @app.get("/api/evolution/evaluations")
    async def evolution_evaluations(project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        service = EvolutionService(workspace)
        return {
            "ok": True,
            "evaluations": service.evaluations(),
            "versions": service.policy_versions(),
            "canary": service.canary_stats(),
            "replays": service.replay_reports(),
            "maintenance": service.maintenance_reports(),
        }

    @app.post("/api/evolution/maintenance")
    async def run_evolution_maintenance(project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        report = EvolutionService(workspace).maintain(
            candidate_ttl_days=config.evolution.candidate_ttl_days,
            verified_review_days=config.evolution.verified_review_days,
            trace_retention_days=config.evolution.trace_retention_days,
        )
        return {"ok": True, "report": report}

    @app.get("/api/evolution/governance")
    async def evolution_governance(project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        service = EvolutionService(workspace)
        return {
            "ok": True,
            "backups": service.list_backups(),
            "audit": service.audit_events(),
        }

    @app.get("/api/evolution/export")
    async def export_evolution_data(project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        return EvolutionService(workspace).export_data(redact=True)

    @app.post("/api/evolution/backup")
    async def backup_evolution_data(project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        return {"ok": True, "backup": EvolutionService(workspace).backup_data()}

    @app.post("/api/evolution/restore")
    async def restore_evolution_data(request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        backup_name = str(body.get("backup_name", "")).strip()
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            result = EvolutionService(workspace).restore_data(backup_name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "restore": result}

    @app.post("/api/evolution/purge")
    async def purge_evolution_data(request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        confirmation = str(body.get("confirmation", ""))
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            result = EvolutionService(workspace).purge_data(confirmation)
        except PermissionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "purge": result}

    @app.get("/api/evolution/skill-drafts")
    async def evolution_skill_drafts(project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        return {"ok": True, "drafts": EvolutionService(workspace).list_skill_drafts()}

    @app.post("/api/evolution/skill-drafts")
    async def synthesize_evolution_skill(request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        task_pattern = str(body.get("task_pattern", "")).strip()
        if not task_pattern:
            raise HTTPException(status_code=400, detail="task_pattern is required")
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            draft = EvolutionService(workspace).synthesize_skill(
                task_pattern, config.evolution.skill_min_experiences
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "draft": draft}

    @app.post("/api/evolution/skill-drafts/{draft_id}/test")
    async def test_evolution_skill_draft(draft_id: str, project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            result = EvolutionService(workspace).test_skill_draft(draft_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "test": result}

    @app.post("/api/evolution/skill-drafts/{draft_id}/review")
    async def review_evolution_skill_draft(draft_id: str, request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        approve = body.get("approve")
        confirmation = str(body.get("confirmation", ""))
        if not isinstance(approve, bool):
            raise HTTPException(status_code=400, detail="boolean approve is required")
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            result = EvolutionService(workspace).review_skill_draft(
                draft_id, approve=approve, confirmation=confirmation
            )
        except (FileNotFoundError, FileExistsError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @app.get("/api/evolution/executable-policies")
    async def evolution_executable_policies(
        approved: bool = False, project_id: str = ""
    ):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        return {
            "ok": True,
            "policies": EvolutionService(workspace).list_executable_policies(
                approved=approved
            ),
        }

    @app.post("/api/evolution/executable-policies")
    async def compile_evolution_executable_policy(request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        task_pattern = str(body.get("task_pattern", "")).strip()
        if not task_pattern:
            raise HTTPException(status_code=400, detail="task_pattern is required")
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            policy = EvolutionService(workspace).compile_executable_policy(task_pattern)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "policy": policy}

    @app.post("/api/evolution/executable-policies/{policy_id}/test")
    async def test_evolution_executable_policy(policy_id: str, project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            result = await EvolutionService(workspace).test_executable_policy(policy_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "test": result}

    @app.post("/api/evolution/executable-policies/{policy_id}/review")
    async def review_evolution_executable_policy(policy_id: str, request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        approve = body.get("approve")
        confirmation = str(body.get("confirmation", ""))
        if not isinstance(approve, bool):
            raise HTTPException(status_code=400, detail="boolean approve is required")
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        try:
            result = await EvolutionService(workspace).review_executable_policy(
                policy_id, approve=approve, confirmation=confirmation
            )
        except (FileNotFoundError, FileExistsError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @app.get("/api/evolution/reflections")
    async def evolution_reflections(status: str = "proposed", project_id: str = ""):
        from ...learning.service import EvolutionService

        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        return {
            "ok": True,
            "reflections": EvolutionService(workspace).reflections(status or None),
        }

    @app.post("/api/evolution/reflections/{reflection_id}/review")
    async def review_evolution_reflection(reflection_id: str, request: Request):
        from ...learning.service import EvolutionService

        body = await request.json()
        project_id = str(body.get("project_id", "")).strip()
        approve = body.get("approve")
        if not isinstance(approve, bool):
            raise HTTPException(status_code=400, detail="boolean approve is required")
        project = adapter.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project) if project else WORKSPACE_DIR
        experience = EvolutionService(workspace).review_reflection(
            reflection_id, approve=approve
        )
        if experience is None:
            raise HTTPException(status_code=404, detail="reflection not found or already reviewed")
        return {"ok": True, "experience": experience.__dict__}

