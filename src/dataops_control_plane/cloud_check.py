"""Bounded cloud preflight. No provider request is sent without --live."""

import argparse
import json
from collections.abc import Sequence

from dataops_control_plane.api.schemas import RCAOutput
from dataops_control_plane.config import Settings
from dataops_control_plane.services.provider_factory import (
    create_embedding_provider,
    create_rca_client,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Send two synthetic requests, sequentially"
    )
    args = parser.parse_args(argv)
    settings = Settings()
    summary = {
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "live_requests": 0,
        "credentials_present": {
            "openai": bool(
                settings.llm_api_key and settings.llm_api_key.get_secret_value().strip()
            ),
            "voyage": bool(
                settings.embedding_api_key and settings.embedding_api_key.get_secret_value().strip()
            ),
        },
    }
    profile_ok = (
        settings.llm_provider == "openai"
        and settings.llm_model == "gpt-6-luna"
        and settings.llm_url.rstrip("/") == "https://api.openai.com/v1"
        and settings.embedding_provider == "voyage"
        and settings.embedding_model == "voyage-4-lite"
        and settings.embedding_url.rstrip("/") == "https://api.voyageai.com/v1"
    )
    if not profile_ok or not all(summary["credentials_present"].values()):
        summary["status"] = "missing_credentials" if profile_ok else "cloud_profile_not_selected"
        print(json.dumps(summary))
        return 2
    if not args.live:
        summary["status"] = "configured_not_live_verified"
        print(json.dumps(summary))
        return 0

    embedder = client = None
    stage = "voyage"
    try:
        embedder = create_embedding_provider(settings)
        summary["live_requests"] += 1
        vector = embedder.embed(
            ["Synthetic connectivity check for schema retrieval."], input_type="query"
        )[0]
        summary["embedding_dimensions_received"] = len(vector)
        stage = "openai"
        client = create_rca_client(settings)
        summary["live_requests"] += 1
        completion = client.generate(
            system_prompt="Analyze only this synthetic evidence. Never execute recovery.",
            user_prompt=(
                "This is a synthetic schema smoke test, not production data. "
                "The only allowed citation_id is EVD-CLOUD-CHECK-001. "
                "Evidence: required column customer_id was renamed to user_id; validation failed. "
                "No knowledge documents are available. Return a supported RCA with action ESCALATE."
            ),
            schema=RCAOutput.model_json_schema(),
        )
        output = RCAOutput.model_validate(completion.payload)
        if {item.citation_id for item in output.evidence} - {"EVD-CLOUD-CHECK-001"}:
            raise ValueError("Unknown citation")
        if output.knowledge_document_ids:
            raise ValueError("Unknown knowledge document")
        summary.update(
            status="live_passed",
            schema_validated=True,
            llm_duration_ms=completion.duration_ms,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )
        print(json.dumps(summary))
        return 0
    except Exception as exc:
        response = getattr(exc.__cause__, "response", None)
        summary.update(
            status="failed",
            stage=stage,
            error_type=type(exc).__name__,
            http_status=getattr(response, "status_code", None),
        )
        print(json.dumps(summary))
        return 1
    finally:
        for provider in (embedder, client):
            if provider is not None:
                provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
