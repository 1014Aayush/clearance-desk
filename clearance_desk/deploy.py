"""Deploy the ADK agent to Vertex AI Agent Engine.

    python -m clearance_desk.deploy

Requires ``google-cloud-aiplatform[agent_engines,adk]`` and a staging bucket:

    pip install "google-cloud-aiplatform[agent_engines,adk]>=1.112.0"
    export GOOGLE_CLOUD_PROJECT=... GCS_BUCKET=your-staging-bucket
"""

from __future__ import annotations

import sys

from .config import get_settings


def main() -> int:
    settings = get_settings()

    if not settings.google_cloud_project:
        print("GOOGLE_CLOUD_PROJECT is not set.", file=sys.stderr)
        return 1
    if not settings.gcs_bucket:
        print(
            "GCS_BUCKET is not set — Agent Engine needs a staging bucket.",
            file=sys.stderr,
        )
        return 1
    if not settings.parallel_api_key:
        print(
            "warning: PARALLEL_API_KEY is not set; the deployed agent will "
            "fall back to recorded fixtures instead of live research.",
            file=sys.stderr,
        )

    try:
        import vertexai
        from vertexai import agent_engines
        from vertexai.preview import reasoning_engines
    except ImportError:
        print(
            'Install the Agent Engine extras first:\n'
            '  pip install "google-cloud-aiplatform[agent_engines,adk]>=1.112.0"',
            file=sys.stderr,
        )
        return 1

    from .agent import build_agent

    vertexai.init(
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
        staging_bucket=f"gs://{settings.gcs_bucket}",
    )

    app = reasoning_engines.AdkApp(agent=build_agent(), enable_tracing=True)

    print(f"Deploying to {settings.google_cloud_project}/{settings.google_cloud_location}…")
    remote = agent_engines.create(
        agent_engine=app,
        display_name="Clearance Desk",
        description=(
            "Agentic rights clearance for film and television. Gemini spots "
            "clearable material; Parallel researches chain of title; a "
            "deterministic rules engine produces the E&O clearance position."
        ),
        requirements=[
            "google-cloud-aiplatform[agent_engines,adk]>=1.112.0",
            "google-genai>=1.28.0",
            "parallel-web>=0.4.0",
            "pydantic>=2.9.0",
            "pydantic-settings>=2.6.0",
            "pypdf>=5.1.0",
        ],
        extra_packages=["clearance_desk"],
        env_vars={
            "PARALLEL_API_KEY": settings.parallel_api_key,
            "GOOGLE_CLOUD_PROJECT": settings.google_cloud_project,
            "GOOGLE_CLOUD_LOCATION": settings.google_cloud_location,
            "SPOTTER_MODEL": settings.spotter_model,
            "DRAFTER_MODEL": settings.drafter_model,
        },
    )

    print(f"\nDeployed: {remote.resource_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
