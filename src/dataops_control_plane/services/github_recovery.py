import json
from urllib.parse import quote

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.recovery_execution import (
    RecoveryDispatch,
    RecoveryExecutorError,
    RecoveryRequest,
)

MAX_PARAMETERS_JSON_CHARS = 10_000


class GitHubActionsRecoveryExecutor:
    provider = "github"
    capabilities = frozenset({"RETRY", "QUARANTINE", "ROLLBACK_IMAGE"})

    def __init__(self, client: httpx.Client, *, workflow: str) -> None:
        self._client = client
        self._workflow = workflow

    @classmethod
    def from_settings(cls, settings: Settings) -> "GitHubActionsRecoveryExecutor":
        token = (
            settings.github_recovery_token.get_secret_value()
            if settings.github_recovery_token is not None
            else ""
        )
        if not token:
            raise ValueError("A GitHub recovery token is required")
        return cls(
            httpx.Client(
                base_url=settings.github_api_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "dataops-control-plane/0.1",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=settings.github_recovery_timeout_seconds,
                follow_redirects=False,
            ),
            workflow=settings.github_recovery_workflow,
        )

    def execute(self, request: RecoveryRequest) -> RecoveryDispatch:
        owner, separator, repository = request.project_ref.partition("/")
        if not separator or not owner or not repository or "/" in repository:
            raise RecoveryExecutorError("GitHub project reference must use owner/repository")

        parameters_json = json.dumps(
            request.parameters,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(parameters_json) > MAX_PARAMETERS_JSON_CHARS:
            raise RecoveryExecutorError("Recovery parameters exceed the GitHub input limit")

        external_reference = f"github:workflow_dispatch:{request.attempt_id}"
        payload = {
            "ref": request.branch,
            "inputs": {
                "recovery_action": request.action_type,
                "incident_id": str(request.incident_id),
                "attempt_id": str(request.attempt_id),
                "idempotency_key": request.idempotency_key,
                "external_reference": external_reference,
                "parameters_json": parameters_json,
            },
        }
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/actions/workflows/"
            f"{quote(self._workflow, safe='')}/dispatches"
        )
        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise RecoveryExecutorError(
                "GitHub recovery dispatch failed (connection error)"
            ) from exc
        if response.status_code not in {200, 204}:
            raise RecoveryExecutorError(
                f"GitHub recovery dispatch failed (HTTP {response.status_code})"
            )
        return RecoveryDispatch(
            external_reference=external_reference,
            details={
                "workflow": self._workflow,
                "ref": request.branch,
                "http_status": response.status_code,
            },
        )

    def close(self) -> None:
        self._client.close()
