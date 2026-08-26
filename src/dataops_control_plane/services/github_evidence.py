from urllib.parse import quote

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.domain.models import PipelineRun
from dataops_control_plane.services.evidence import (
    MAX_EVIDENCE_EXCERPT_CHARS,
    EvidenceCandidate,
    EvidenceSourceUnavailable,
)

MAX_COMMIT_FILES = 20
MAX_PATCH_CHARS_PER_FILE = 750


class GitHubCommitEvidenceSource:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "GitHubCommitEvidenceSource":
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "dataops-control-plane/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        github_token = (
            settings.github_token.get_secret_value() if settings.github_token is not None else ""
        )
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        return cls(
            httpx.Client(
                base_url=settings.github_api_url,
                headers=headers,
                timeout=10,
                follow_redirects=False,
            )
        )

    def collect(self, run: PipelineRun) -> list[EvidenceCandidate]:
        if run.provider != "github":
            return []

        owner, separator, repository = run.project_ref.partition("/")
        if not separator or not owner or not repository or "/" in repository:
            return []

        try:
            response = self._client.get(
                f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/commits/"
                f"{quote(run.commit_sha, safe='')}"
            )
        except httpx.HTTPError as exc:
            raise EvidenceSourceUnavailable(
                "github_commit",
                "GitHub commit evidence is unavailable (connection error)",
            ) from exc
        if response.status_code != 200:
            raise EvidenceSourceUnavailable(
                "github_commit",
                f"GitHub commit evidence is unavailable (HTTP {response.status_code})",
            )

        try:
            payload = response.json()
            files = payload["files"]
            stats = payload["stats"]
            if not isinstance(files, list) or not isinstance(stats, dict):
                raise TypeError

            truncated = len(files) > MAX_COMMIT_FILES
            lines: list[str] = []
            for file in files[:MAX_COMMIT_FILES]:
                lines.append(
                    f"FILE {file['filename']} status={file['status']} "
                    f"additions={file['additions']} deletions={file['deletions']}"
                )
                patch = file.get("patch")
                if isinstance(patch, str):
                    if len(patch) > MAX_PATCH_CHARS_PER_FILE:
                        patch = patch[:MAX_PATCH_CHARS_PER_FILE]
                        truncated = True
                    lines.append(patch)

            excerpt = "\n".join(lines)
            suffix = "\n...[TRUNCATED]"
            if len(excerpt) > MAX_EVIDENCE_EXCERPT_CHARS - len(suffix):
                excerpt = excerpt[: MAX_EVIDENCE_EXCERPT_CHARS - len(suffix)]
                truncated = True
            if truncated:
                excerpt += suffix

            source_uri = payload.get("html_url")
            if not isinstance(source_uri, str):
                source_uri = f"https://github.com/{run.project_ref}/commit/{run.commit_sha}"
            additions = stats["additions"]
            deletions = stats["deletions"]
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceSourceUnavailable(
                "github_commit",
                "GitHub commit evidence returned an invalid response",
                code="INVALID_RESPONSE",
            ) from exc
        return [
            EvidenceCandidate(
                evidence_type="COMMIT_DIFF",
                source_uri=source_uri,
                excerpt=excerpt,
                metadata={
                    "provider": run.provider,
                    "project_ref": run.project_ref,
                    "commit_sha": run.commit_sha,
                    "changed_files": len(files),
                    "additions": additions,
                    "deletions": deletions,
                    "truncated": truncated,
                },
            )
        ]

    def close(self) -> None:
        self._client.close()
