import json

from dataops_control_plane.api.schemas import GitHubOnboardingRead, OnboardingSecretRead
from dataops_control_plane.domain.models import Project


def build_github_onboarding(project: Project, *, public_url: str) -> GitHubOnboardingRead:
    branch = json.dumps(project.default_branch)
    workflow = f"""name: DataOps Pipeline

on:
  push:
    branches:
      - {branch}
  workflow_dispatch:

permissions:
  contents: read

jobs:
  dataops:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
      - name: Run DataOps pipeline
        uses: AndyAnh174/dataops-agent@v0
        env:
          DATAOPS_URL: ${{{{ secrets.DATAOPS_URL }}}}
          DATAOPS_TOKEN: ${{{{ secrets.DATAOPS_TOKEN }}}}
"""
    dataops_config = """version: 1
pipeline:
  stages:
    - name: test
      run: echo \"Replace this command with your test command\"
"""
    return GitHubOnboardingRead(
        provider="github",
        project_id=project.id,
        project_ref=project.project_ref,
        workflow_path=".github/workflows/dataops.yml",
        workflow_yaml=workflow,
        dataops_config_path="dataops.yaml",
        dataops_config_yaml=dataops_config,
        required_secrets=[
            OnboardingSecretRead(
                name="DATAOPS_URL",
                value=public_url.rstrip("/"),
                sensitive=False,
                description="Public URL of this DataOps Platform",
            ),
            OnboardingSecretRead(
                name="DATAOPS_TOKEN",
                value=None,
                sensitive=True,
                description="Project token shown once when it is created",
            ),
        ],
    )
