from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from dataops_control_plane.api.dependencies import LogStoreDep, SessionDep
from dataops_control_plane.domain.models import (
    Incident,
    PipelineRun,
    PlatformState,
    Project,
    RCAReport,
    RecoveryAttempt,
    RecoveryAuditEvent,
    RecoveryPlan,
)
from dataops_control_plane.services.pipeline_logs import LogStoreUnavailable
from dataops_control_plane.services.provider_onboarding import build_github_onboarding
from dataops_control_plane.services.web_identity import (
    get_user_for_session,
    list_user_workspaces,
)
from dataops_control_plane.services.web_projects import (
    ProjectNotFound,
    list_integration_tokens,
    list_projects,
    require_project_access,
    require_workspace_membership,
)

router = APIRouter(tags=["web-ui"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "web" / "templates")


def _current_user(request: Request, session):
    settings = request.app.state.settings
    token = request.cookies.get(settings.web_session_cookie_name, "")
    return get_user_for_session(session, token) if token else None


def _project_for_run(session, run: PipelineRun, user_id: UUID) -> Project | None:
    candidates = session.exec(
        select(Project).where(
            Project.provider == run.provider,
            Project.project_ref == run.project_ref,
        )
    ).all()
    for candidate in candidates:
        try:
            require_project_access(session, project_id=candidate.id, user_id=user_id)
        except ProjectNotFound:
            continue
        return candidate
    return None


@router.get("/", response_class=HTMLResponse)
def root(request: Request, session: SessionDep):
    if session.get(PlatformState, 1) is None:
        return RedirectResponse("/setup", status_code=303)
    if _current_user(request, session) is None:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/app", status_code=303)


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, session: SessionDep):
    if session.get(PlatformState, 1) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"page_title": "Create owner"},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: SessionDep):
    if session.get(PlatformState, 1) is None:
        return RedirectResponse("/setup", status_code=303)
    if _current_user(request, session) is not None:
        return RedirectResponse("/app", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"page_title": "Sign in"},
    )


@router.get("/app", response_class=HTMLResponse)
def dashboard_page(request: Request, session: SessionDep):
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    workspace_rows = []
    for workspace, membership in list_user_workspaces(session, user.id):
        workspace_rows.append(
            {
                "workspace": workspace,
                "membership": membership,
                "projects": list_projects(
                    session,
                    workspace_id=workspace.id,
                    user_id=user.id,
                ),
            }
        )
    incident_count = session.exec(select(PlatformState.id)).first()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "Control center",
            "user": user,
            "workspace_rows": workspace_rows,
            "instance_ready": incident_count is not None,
        },
    )


@router.get("/app/projects/{project_id}", response_class=HTMLResponse)
def project_page(project_id: UUID, request: Request, session: SessionDep):
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        project = require_project_access(
            session,
            project_id=project_id,
            user_id=user.id,
        )
        tokens = list_integration_tokens(
            session,
            project_id=project_id,
            user_id=user.id,
        )
        membership = require_workspace_membership(
            session,
            workspace_id=project.workspace_id,
            user_id=user.id,
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    runs = list(
        session.exec(
            select(PipelineRun)
            .where(
                PipelineRun.provider == project.provider,
                PipelineRun.project_ref == project.project_ref,
            )
            .order_by(PipelineRun.last_event_at.desc())
        ).all()
    )
    run_ids = [run.id for run in runs]
    incidents = (
        list(session.exec(select(Incident).where(Incident.pipeline_run_id.in_(run_ids))).all())
        if run_ids
        else []
    )
    incident_by_run = {incident.pipeline_run_id: incident for incident in incidents}
    return templates.TemplateResponse(
        request=request,
        name="project.html",
        context={
            "page_title": project.name,
            "user": user,
            "project": project,
            "can_delete_project": membership.role == "OWNER",
            "tokens": tokens,
            "runs": runs,
            "incident_by_run": incident_by_run,
            "onboarding": (
                build_github_onboarding(
                    project,
                    public_url=(
                        request.app.state.settings.public_url or str(request.base_url).rstrip("/")
                    ),
                )
                if project.provider == "github"
                else None
            ),
        },
    )


@router.get("/app/runs/{run_id}", response_class=HTMLResponse)
def run_page(run_id: UUID, request: Request, session: SessionDep, log_store: LogStoreDep):
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    project = _project_for_run(session, run, user.id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    log_warning = None
    try:
        logs = log_store.search(run.id, query=None, stage=None, level=None, limit=200)
    except LogStoreUnavailable:
        logs = []
        log_warning = "Log storage is temporarily unavailable"
    incident = session.exec(select(Incident).where(Incident.pipeline_run_id == run.id)).first()
    return templates.TemplateResponse(
        request=request,
        name="run.html",
        context={
            "page_title": f"Run {run.external_run_id}",
            "user": user,
            "project": project,
            "run": run,
            "incident": incident,
            "logs": logs,
            "log_warning": log_warning,
        },
    )


@router.get("/app/incidents/{incident_id}", response_class=HTMLResponse)
def incident_page(incident_id: UUID, request: Request, session: SessionDep):
    user = _current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    run = session.get(PipelineRun, incident.pipeline_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    project = _project_for_run(session, run, user.id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    report = session.exec(
        select(RCAReport)
        .where(RCAReport.incident_id == incident.id)
        .order_by(RCAReport.created_at.desc())
    ).first()
    plan = session.exec(
        select(RecoveryPlan)
        .where(RecoveryPlan.incident_id == incident.id)
        .order_by(RecoveryPlan.created_at.desc())
    ).first()
    attempt = session.exec(
        select(RecoveryAttempt)
        .where(RecoveryAttempt.incident_id == incident.id)
        .order_by(RecoveryAttempt.started_at.desc())
    ).first()
    audit_events = list(
        session.exec(
            select(RecoveryAuditEvent)
            .where(RecoveryAuditEvent.incident_id == incident.id)
            .order_by(RecoveryAuditEvent.created_at.desc())
        ).all()
    )
    return templates.TemplateResponse(
        request=request,
        name="incident.html",
        context={
            "page_title": f"Incident {incident.id}",
            "user": user,
            "project": project,
            "run": run,
            "incident": incident,
            "report": report,
            "plan": plan,
            "attempt": attempt,
            "audit_events": audit_events,
        },
    )
