# tests/models/test_evaluation.py

import pytest
from uuid import uuid4
from quantforge.models.evaluation import PaperEvaluation, DimensionScore


def make_score(score: float, llm_assisted: bool = False) -> DimensionScore:
    return DimensionScore(
        score=score,
        rationale="Test rationale",
        llm_assisted=llm_assisted,
    )


def test_dimension_score_valid_range():
    s = make_score(0.75)
    assert s.score == 0.75
    assert s.llm_assisted is False


def test_dimension_score_rejects_out_of_range():
    with pytest.raises(Exception):
        DimensionScore(score=1.5)
    with pytest.raises(Exception):
        DimensionScore(score=-0.1)


def test_evaluation_created_with_no_scores():
    ev = PaperEvaluation(paper_id=uuid4())
    assert ev.relevance is None
    assert ev.credibility is None
    assert ev.composite_score is None


def test_composite_with_all_dimensions():
    ev = PaperEvaluation(
        paper_id=uuid4(),
        relevance=make_score(0.9),
        credibility=make_score(0.8),
        implementability=make_score(0.7),
        reproducibility=make_score(0.6),
        priority=make_score(0.5),
    )
    score = ev.compute_composite()
    assert 0.0 < score <= 1.0
    assert ev.composite_score == score


def test_composite_with_partial_dimensions():
    # Only relevance and implementability scored — others missing
    ev = PaperEvaluation(
        paper_id=uuid4(),
        relevance=make_score(1.0),
        implementability=make_score(1.0),
    )
    score = ev.compute_composite()
    # Should still return a valid score using only available dimensions
    assert score == 1.0


def test_composite_with_no_dimensions_returns_zero():
    ev = PaperEvaluation(paper_id=uuid4())
    score = ev.compute_composite()
    assert score == 0.0


def test_composite_respects_custom_weights():
    ev = PaperEvaluation(
        paper_id=uuid4(),
        relevance=make_score(1.0),
        credibility=make_score(0.0),
    )
    # Give all weight to relevance
    score = ev.compute_composite(weights={"relevance": 1.0, "credibility": 0.0})
    assert score == 1.0


def test_llm_assisted_flag_preserved():
    score = make_score(0.8, llm_assisted=True)
    assert score.llm_assisted is True


def test_evidence_list_stored():
    score = DimensionScore(
        score=0.7,
        rationale="Strong methodology",
        evidence=["Uses Fama-French factors", "Out-of-sample tested"],
    )
    assert len(score.evidence) == 2