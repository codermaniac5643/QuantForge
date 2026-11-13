# tests/config/test_loader.py

import pytest
from pathlib import Path
import yaml
import tempfile
from quantforge.config.loader import (
    load_research_topics,
    load_pipeline_config,
    load_llm_config,
    ResearchTopicsConfig,
    ResearchPipelineConfig,
    LLMConfig,
)


def write_yaml(tmp_path: Path, filename: str, data: dict) -> Path:
    """Helper — writes a dict as a YAML file and returns its path."""
    p = tmp_path / filename
    p.write_text(yaml.dump(data), encoding="utf-8")
    return tmp_path


# --- research_topics.yaml ---

def test_load_valid_research_topics(tmp_path):
    write_yaml(tmp_path, "research_topics.yaml", {
        "topics": [
            {
                "name": "momentum",
                "description": "Momentum strategies",
                "search_terms": ["momentum investing"],
                "categories": ["quantitative finance"],
            }
        ]
    })
    config = load_research_topics(tmp_path)
    assert isinstance(config, ResearchTopicsConfig)
    assert len(config.topics) == 1
    assert config.topics[0].name == "momentum"


def test_load_topics_empty_list_raises(tmp_path):
    write_yaml(tmp_path, "research_topics.yaml", {"topics": []})
    with pytest.raises(Exception):
        load_research_topics(tmp_path)


def test_load_topics_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_research_topics(tmp_path)


# --- research_pipeline.yaml ---

def test_load_valid_pipeline_config(tmp_path):
    write_yaml(tmp_path, "research_pipeline.yaml", {
        "discovery": {"results_per_term": 25, "lookback_days": 180},
        "acquisition": {"approval_mode": "manual"},
    })
    config = load_pipeline_config(tmp_path)
    assert isinstance(config, ResearchPipelineConfig)
    assert config.discovery.results_per_term == 25
    assert config.acquisition.approval_mode == "manual"


def test_pipeline_config_invalid_approval_mode_raises(tmp_path):
    write_yaml(tmp_path, "research_pipeline.yaml", {
        "acquisition": {"approval_mode": "automatic"},
    })
    with pytest.raises(Exception):
        load_pipeline_config(tmp_path)


def test_pipeline_config_uses_defaults_for_missing_sections(tmp_path):
    write_yaml(tmp_path, "research_pipeline.yaml", {})
    config = load_pipeline_config(tmp_path)
    assert config.discovery.results_per_term == 50
    assert config.acquisition.max_papers_per_week == 20


# --- llm.yaml ---

def test_load_valid_llm_config(tmp_path):
    write_yaml(tmp_path, "llm.yaml", {
        "provider": {
            "name": "openrouter",
            "model": "anthropic/claude-3-haiku",
            "base_url": "https://openrouter.ai/api/v1",
        },
        "enabled": True,
        "budget": {
            "max_spend_usd": 5.00,
            "warn_at_usd": 3.00,
            "auto_recharge": False,
        },
    })
    config = load_llm_config(tmp_path)
    assert isinstance(config, LLMConfig)
    assert config.provider.model == "anthropic/claude-3-haiku"
    assert config.budget.max_spend_usd == 5.00
    assert config.enabled is True


def test_llm_config_missing_provider_raises(tmp_path):
    write_yaml(tmp_path, "llm.yaml", {
        "budget": {"max_spend_usd": 5.00, "warn_at_usd": 3.00},
    })
    with pytest.raises(Exception):
        load_llm_config(tmp_path)