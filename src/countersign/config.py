"""Settings.

Read from the environment, with defaults chosen so that ``countersign serve``
works immediately after a checkout: seeded corpus, deterministic model mode, a
SQLite file under ``data/``. Every setting that changes what a judge sees is
surfaced in the product's own header, so nothing about the run is hidden.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelMode = Literal["demo", "bedrock", "agentcore"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COUNTERSIGN_", env_file=".env", extra="ignore"
    )

    # -- model ------------------------------------------------------------
    model_mode: ModelMode = Field(
        default="demo",
        description=(
            "demo runs the deterministic composer and touches no model. bedrock runs the Strands "
            "agents against Amazon Bedrock. agentcore invokes the deployed AgentCore runtime."
        ),
    )
    bedrock_model_id: str = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_region: str = "eu-west-1"
    agentcore_arn: str = Field(
        default="",
        description=(
            "The deployed runtime's ARN. Required by model_mode=agentcore: with it the console "
            "invokes the runtime over InvokeAgentRuntime and builds no local model at all."
        ),
    )
    agentcore_region: str = Field(
        default="",
        description="Region of the deployed runtime. Defaults to bedrock_region.",
    )
    agentcore_qualifier: str = Field(
        default="DEFAULT",
        description="Runtime version alias to invoke. DEFAULT is the live version.",
    )
    agentcore_timeout_seconds: int = 300
    model_temperature: float = 0.2
    model_max_tokens: int = 4096

    # -- data -------------------------------------------------------------
    database_path: Path = Path("data/countersign.db")
    session_path: Path = Path("data/sessions")
    tenant: str = "kestrel"
    allow_live_connectors: bool = True
    allow_source_mixing: bool = Field(
        default=False,
        description=(
            "Whether an uncredentialed source may fall back to the seeded corpus while another "
            "source is live. Off, because a control that joins real evidence to invented evidence "
            "publishes one outcome over both. Turn it on only to demonstrate a single live "
            "adapter against the seeded estate."
        ),
    )
    as_of: date = Field(
        default=date(2026, 9, 1),
        description=(
            "The date the schedule and every control period are measured from. It defaults to "
            "the date the seeded estate is anchored to, so a screenshot taken in March and one "
            "taken in November show the same numbers. Set it to today when the connectors are live."
        ),
    )

    # -- service ----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8080
    console_user: str = "risk"
    console_password: str = "countersign"
    public_base_url: str = ""

    # -- scheduler --------------------------------------------------------
    scheduler_enabled: bool = Field(
        default=True,
        description=(
            "Run every control that has fallen due in the background, without a person "
            "pressing anything. The console process owns the one SQLite writer, so the "
            "scheduler runs inside that process; a second container would break the "
            "single-writer boundary. It is a no-op when nothing is due."
        ),
    )
    scheduler_interval_seconds: int = Field(
        default=3600,
        description=(
            "How often the background scheduler wakes to run everything that has fallen due. "
            "Each control still runs on its own cadence; this only sets how often the schedule "
            "is checked."
        ),
    )

    # -- behaviour --------------------------------------------------------
    max_evidence_rows_per_prompt: int = Field(
        default=60,
        description=(
            "How many population rows an agent may be shown. The outcome is counted over the "
            "whole population regardless; this bounds only what reaches a prompt."
        ),
    )

    @property
    def uses_model(self) -> bool:
        return self.model_mode != "demo"

    @property
    def uses_runtime(self) -> bool:
        """Whether the model work leaves this process for the deployed runtime."""
        return self.model_mode == "agentcore"

    @property
    def runtime_region(self) -> str:
        return self.agentcore_region or self.bedrock_region

    def describe_mode(self) -> str:
        if self.model_mode == "demo":
            return "deterministic, no model is invoked"
        if self.model_mode == "bedrock":
            return f"Strands agents on Bedrock ({self.bedrock_model_id})"
        if not self.agentcore_arn:
            return "AgentCore runtime not configured (COUNTERSIGN_AGENTCORE_ARN is unset)"
        return f"Strands agents on the AgentCore runtime ({self.agentcore_arn})"


def load_settings() -> Settings:
    return Settings()
