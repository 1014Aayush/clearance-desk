"""Runtime configuration, loaded from environment or a local .env file."""

from __future__ import annotations

import functools

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Google Cloud ------------------------------------------------------
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = True

    # Verified callable against Vertex AI in us-central1. Model availability is
    # project- and region-specific, so confirm before changing these:
    #   python -m clearance_desk check-models
    spotter_model: str = "gemini-2.5-pro"
    drafter_model: str = "gemini-2.5-flash"
    gcs_bucket: str = ""

    # --- Parallel ----------------------------------------------------------
    parallel_api_key: str = ""
    parallel_processor_standard: str = "core"
    parallel_processor_deep: str = "pro"
    parallel_timeout_seconds: int = 1800

    # --- Acoustic identification (optional) --------------------------------
    # Names commercial needle-drops so research has something to look up.
    # Free tier at https://audd.io — 300 requests, no card.
    audd_api_token: str = ""

    # --- Public deployment guard -------------------------------------------
    # The browser's cost confirmation stops an honest tester clicking around
    # and does nothing whatever about a loop against the API. A public,
    # unauthenticated deployment spending a fixed prepaid balance needs the
    # limit enforced on the server, so this caps *billed* runs per day.
    # Replays are never counted: they cost nothing, and they are what the
    # bundled samples are for. Zero disables the cap, which is right for
    # running from source and wrong for anything public.
    max_live_runs_per_day: int = 5

    # Where that count is kept. The counter is otherwise a number in memory,
    # which resets whenever the container does — on every deploy, and on every
    # cold start once the service has scaled to zero. That makes the "per day"
    # in the setting above a lie: a caller patient enough to wait out an idle
    # timeout gets a fresh allowance. Naming a bucket here moves the count to a
    # small object in Cloud Storage, where it survives both. Empty falls back
    # to the in-process counter, which is right for running from source.
    budget_bucket: str = ""

    # --- Runtime -----------------------------------------------------------
    use_fixtures: bool = False
    port: int = 8080

    @property
    def parallel_enabled(self) -> bool:
        return bool(self.parallel_api_key) and not self.use_fixtures

    @property
    def audio_id_enabled(self) -> bool:
        return bool(self.audd_api_token) and not self.use_fixtures

    @property
    def vertex_configured(self) -> bool:
        return bool(self.google_cloud_project)

    def describe(self) -> dict[str, str]:
        """Non-secret summary, surfaced on the UI's status strip."""
        return {
            "project": self.google_cloud_project or "(unset)",
            "location": self.google_cloud_location,
            "spotter_model": self.spotter_model,
            "research": "parallel" if self.parallel_enabled else "fixtures",
            "processor_standard": self.parallel_processor_standard,
            "processor_deep": self.parallel_processor_deep,
        }


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
