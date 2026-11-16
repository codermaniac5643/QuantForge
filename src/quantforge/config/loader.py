# src/quantforge/config/loader.py

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


# --- Topic Models ---

class TopicConfig(BaseModel):
    name: str
    description: str
    search_terms: list[str]
    categories: list[str] = Field(default_factory=list)


class ResearchTopicsConfig(BaseModel):
    topics: list[TopicConfig]

    @field_validator("topics")
    @classmethod
    def at_least_one_topic(cls, v: list[TopicConfig]) -> list[TopicConfig]:
        if not v:
            raise ValueError("At least one research topic must be defined")
        return v


# --- Pipeline Models ---

class DiscoveryConfig(BaseModel):
    results_per_term: int = 50
    lookback_days: int = 365
    request_delay_seconds: float = 1.0
    max_retries: int = 3
    timeout_seconds: int = 30


class FilteringConfig(BaseModel):
    min_relevance_score: float = 0.3
    min_acquisition_score: float = 0.5


class AcquisitionConfig(BaseModel):
    max_papers_per_week: int = 20
    max_papers_per_month: int = 50
    approval_mode: str = "manual"
    storage_path: str = "research_library"
    max_pdf_size_mb: int = 50

    @field_validator("approval_mode")
    @classmethod
    def valid_approval_mode(cls, v: str) -> str:
        if v not in ("auto", "manual"):
            raise ValueError("approval_mode must be 'auto' or 'manual'")
        return v


class ProcessingConfig(BaseModel):
    extract_sections: bool = True
    extract_tables: bool = False


class ResearchPipelineConfig(BaseModel):
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    filtering: FilteringConfig = Field(default_factory=FilteringConfig)
    acquisition: AcquisitionConfig = Field(default_factory=AcquisitionConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)


# --- LLM Models ---

class ProviderConfig(BaseModel):
    name: str
    model: str
    base_url: str


class LLMBudgetConfig(BaseModel):
    max_spend_usd: float
    warn_at_usd: float
    auto_recharge: bool = False


class LLMLimitsConfig(BaseModel):
    max_input_tokens: int = 8000
    max_output_tokens: int = 1000
    max_calls_per_paper: int = 3
    max_concurrent_requests: int = 2
    max_retries: int = 2


class LLMTasksConfig(BaseModel):
    relevance_evaluation: bool = True
    credibility_evaluation: bool = False
    section_extraction: bool = False


class LLMConfig(BaseModel):
    provider: ProviderConfig
    enabled: bool = True
    limits: LLMLimitsConfig = Field(default_factory=LLMLimitsConfig)
    budget: LLMBudgetConfig
    tasks: LLMTasksConfig = Field(default_factory=LLMTasksConfig)


# --- Loader ---

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_research_topics(config_dir: Path) -> ResearchTopicsConfig:
    data = _load_json(config_dir / "research_topics.json")
    return ResearchTopicsConfig(**data)


def load_pipeline_config(config_dir: Path) -> ResearchPipelineConfig:
    data = _load_json(config_dir / "research_pipeline.json")
    return ResearchPipelineConfig(**data)


def load_llm_config(config_dir: Path) -> LLMConfig:
    data = _load_json(config_dir / "llm.json")
    return LLMConfig(**data)