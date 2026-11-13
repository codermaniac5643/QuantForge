# src/quantforge/research/pipeline/context.py

from dataclasses import dataclass, field
from pathlib import Path

from quantforge.config.loader import (
    ResearchPipelineConfig,
    ResearchTopicsConfig,
    LLMConfig,
)
from quantforge.database.repositories.paper import PaperRepository
from quantforge.research.filtering.ranker import CandidateRanker


@dataclass
class PipelineContext:
    """
    Carries all configuration and shared services for a pipeline run.
    Passed to every stage — stages never construct their own dependencies.

    dry_run: when True, acquisition and LLM calls are skipped.
             All other stages run normally so the pipeline can be
             validated without spending money or downloading files.
    """

    # --- Config ---
    pipeline_config: ResearchPipelineConfig
    topics_config: ResearchTopicsConfig
    llm_config: LLMConfig

    # --- Services ---
    repo: PaperRepository
    ranker: CandidateRanker

    # --- Storage ---
    library_root: Path

    # --- Run Controls ---
    dry_run: bool = False
    topic_names: list[str] = field(default_factory=list)   # empty = all topics

    # --- Optional Services (set after construction) ---
    # Budget manager is optional — None when LLM is disabled or dry_run
    budget_manager: object | None = None

    @property
    def acquisition_config(self):
        return self.pipeline_config.acquisition

    @property
    def discovery_config(self):
        return self.pipeline_config.discovery

    @property
    def filtering_config(self):
        return self.pipeline_config.filtering

    def active_topics(self):
        """Return topics to process — all topics or filtered by topic_names."""
        topics = self.topics_config.topics
        if self.topic_names:
            topics = [t for t in topics if t.name in self.topic_names]
        return topics