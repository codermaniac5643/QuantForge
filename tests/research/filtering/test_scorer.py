# tests/research/filtering/test_scorer.py
"""
Unit tests for the deterministic scorer module.

Covers:
  - score_relevance: high-value terms, abstract, categories, topic keywords, penalty
  - score_credibility: DOI, arXiv ID, venue, abstract, date, author count
  - score_implementability: rule signals, asset terms, arXiv categories
  - score_reproducibility: data signals, method signals, abstract size, recency

All scorers must:
  - Return DimensionScore in [0.0, 1.0]
  - Set llm_assisted = False
  - Provide a non-empty rationale
  - Return higher scores for obviously relevant papers than irrelevant ones
"""

import pytest
from datetime import date, timedelta

from quantforge.models.paper import Author, ResearchPaper, SourceReference
from quantforge.models.states import PaperState
from quantforge.models.evaluation import DimensionScore
from quantforge.research.filtering.scorer import (
    score_relevance,
    score_credibility,
    score_implementability,
    score_reproducibility,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_paper(**kwargs) -> ResearchPaper:
    defaults = {
        "title": "A Research Paper",
        "state": PaperState.DISCOVERED,
    }
    defaults.update(kwargs)
    return ResearchPaper(**defaults)


MOMENTUM_PAPER = make_paper(
    title="Cross-Sectional Momentum Strategy in Equity Markets",
    abstract=(
        "We study cross-sectional momentum and trend following strategies "
        "applied to US equity markets using CRSP daily data from 1963 to 2023. "
        "We evaluate risk-adjusted returns using the Sharpe ratio and "
        "Fama-French five-factor model. Out-of-sample tests confirm robustness."
    ),
    keywords=["q-fin.PM", "q-fin.ST"],
    doi="10.1234/momentum",
    arxiv_id="2401.00001",
    authors=[Author(name="Alice"), Author(name="Bob")],
    published_date=date.today() - timedelta(days=100),
    journal="Journal of Finance",
)

IRRELEVANT_PAPER = make_paper(
    title="Deep Learning for Medical Image Segmentation",
    abstract=(
        "We propose a convolutional neural network for semantic segmentation "
        "of MRI scans in clinical trial settings. Object detection and "
        "speech recognition experiments are reported."
    ),
    keywords=["cs.CV", "cs.LG"],
    published_date=date.today() - timedelta(days=50),
)


# ---------------------------------------------------------------------------
# score_relevance
# ---------------------------------------------------------------------------

class TestScoreRelevance:
    def test_returns_dimension_score(self):
        result = score_relevance(MOMENTUM_PAPER)
        assert isinstance(result, DimensionScore)

    def test_score_in_range(self):
        for paper in [MOMENTUM_PAPER, IRRELEVANT_PAPER]:
            result = score_relevance(paper)
            assert 0.0 <= result.score <= 1.0

    def test_not_llm_assisted(self):
        assert score_relevance(MOMENTUM_PAPER).llm_assisted is False

    def test_has_rationale(self):
        result = score_relevance(MOMENTUM_PAPER)
        assert result.rationale and len(result.rationale) > 10

    def test_momentum_paper_scores_higher_than_irrelevant(self):
        rel = score_relevance(MOMENTUM_PAPER).score
        irr = score_relevance(IRRELEVANT_PAPER).score
        assert rel > irr

    def test_high_value_title_raises_score(self):
        paper_with_hit = make_paper(title="Momentum Strategy in Equity Markets")
        paper_no_hit = make_paper(title="A Generic Study of Markets")
        assert score_relevance(paper_with_hit).score > score_relevance(paper_no_hit).score

    def test_low_value_title_penalised(self):
        paper_irrelevant = make_paper(
            title="Drug Discovery Using Clinical Trial Data"
        )
        result = score_relevance(paper_irrelevant)
        assert result.score < 0.1  # penalty applied

    def test_arxiv_category_raises_score(self):
        with_cat = make_paper(
            title="A Study of Markets",
            keywords=["q-fin.pm"],
        )
        without_cat = make_paper(title="A Study of Markets", keywords=[])
        assert score_relevance(with_cat).score > score_relevance(without_cat).score

    def test_topic_keywords_boost_score(self):
        paper = make_paper(title="Market Study", abstract="momentum trend following")
        without = score_relevance(paper, topic_keywords=None).score
        with_kw = score_relevance(paper, topic_keywords=["momentum"]).score
        # Providing matching topic keywords should not decrease score
        assert with_kw >= without

    def test_evidence_non_empty_for_relevant_paper(self):
        result = score_relevance(MOMENTUM_PAPER)
        assert len(result.evidence) > 0

    def test_empty_paper_gives_zero_score(self):
        paper = make_paper(title="A Paper", abstract=None, keywords=[])
        result = score_relevance(paper)
        assert result.score == 0.0

    def test_score_capped_at_1(self):
        # Craft a paper that hits many signals
        paper = make_paper(
            title="momentum trend following mean reversion breakout risk management",
            abstract="momentum trend following mean reversion breakout " * 10,
            keywords=["q-fin.pm", "q-fin.st", "q-fin.tr"],
        )
        result = score_relevance(paper)
        assert result.score <= 1.0


# ---------------------------------------------------------------------------
# score_credibility
# ---------------------------------------------------------------------------

class TestScoreCredibility:
    def test_returns_dimension_score(self):
        result = score_credibility(MOMENTUM_PAPER)
        assert isinstance(result, DimensionScore)

    def test_score_in_range(self):
        for paper in [MOMENTUM_PAPER, IRRELEVANT_PAPER]:
            result = score_credibility(paper)
            assert 0.0 <= result.score <= 1.0

    def test_not_llm_assisted(self):
        assert score_credibility(MOMENTUM_PAPER).llm_assisted is False

    def test_has_rationale(self):
        result = score_credibility(MOMENTUM_PAPER)
        assert result.rationale

    def test_doi_increases_score(self):
        with_doi = make_paper(title="A Paper", doi="10.1234/x")
        without_doi = make_paper(title="A Paper", doi=None)
        assert score_credibility(with_doi).score > score_credibility(without_doi).score

    def test_arxiv_id_increases_score(self):
        with_arxiv = make_paper(title="A Paper", arxiv_id="2401.00001")
        without = make_paper(title="A Paper", arxiv_id=None)
        assert score_credibility(with_arxiv).score > score_credibility(without).score

    def test_credible_venue_increases_score(self):
        with_venue = make_paper(title="A Paper", journal="Journal of Finance")
        without_venue = make_paper(title="A Paper", journal=None)
        assert score_credibility(with_venue).score > score_credibility(without_venue).score

    def test_unranked_venue_still_adds_score(self):
        with_venue = make_paper(title="A Paper", journal="Some Unknown Journal")
        without_venue = make_paper(title="A Paper", journal=None)
        assert score_credibility(with_venue).score > score_credibility(without_venue).score

    def test_credible_venue_scores_higher_than_unranked(self):
        credible = make_paper(title="A Paper", journal="Journal of Finance")
        unranked = make_paper(title="A Paper", journal="Some Unknown Blog")
        assert score_credibility(credible).score > score_credibility(unranked).score

    def test_abstract_increases_score(self):
        with_abstract = make_paper(title="A Paper", abstract="This is an abstract.")
        without = make_paper(title="A Paper", abstract=None)
        assert score_credibility(with_abstract).score > score_credibility(without).score

    def test_publication_date_increases_score(self):
        with_date = make_paper(title="A Paper", published_date=date(2024, 1, 1))
        without = make_paper(title="A Paper", published_date=None)
        assert score_credibility(with_date).score > score_credibility(without).score

    def test_multiple_authors_increases_score(self):
        multi = make_paper(
            title="A Paper",
            authors=[Author(name="Alice"), Author(name="Bob")],
        )
        single = make_paper(title="A Paper", authors=[Author(name="Alice")])
        assert score_credibility(multi).score > score_credibility(single).score

    def test_momentum_paper_credibility_high(self):
        result = score_credibility(MOMENTUM_PAPER)
        # Has DOI + arXiv + credible journal + abstract + date + 2 authors
        assert result.score > 0.7

    def test_score_capped_at_1(self):
        result = score_credibility(MOMENTUM_PAPER)
        assert result.score <= 1.0

    def test_evidence_populated(self):
        result = score_credibility(MOMENTUM_PAPER)
        assert len(result.evidence) > 0


# ---------------------------------------------------------------------------
# score_implementability
# ---------------------------------------------------------------------------

class TestScoreImplementability:
    def test_returns_dimension_score(self):
        result = score_implementability(MOMENTUM_PAPER)
        assert isinstance(result, DimensionScore)

    def test_score_in_range(self):
        for paper in [MOMENTUM_PAPER, IRRELEVANT_PAPER]:
            result = score_implementability(paper)
            assert 0.0 <= result.score <= 1.0

    def test_not_llm_assisted(self):
        assert score_implementability(MOMENTUM_PAPER).llm_assisted is False

    def test_has_rationale(self):
        result = score_implementability(MOMENTUM_PAPER)
        assert result.rationale

    def test_rule_signal_in_title_raises_score(self):
        with_rule = make_paper(title="Momentum Strategy with Stop Loss Rules")
        without = make_paper(title="A Study of Financial Markets")
        assert score_implementability(with_rule).score > score_implementability(without).score

    def test_asset_mentions_in_abstract_raises_score(self):
        with_asset = make_paper(
            title="A Study",
            abstract="We analyse equity and stock returns across multiple markets.",
        )
        without = make_paper(
            title="A Study",
            abstract="We explore general economic theory and market equilibrium.",
        )
        assert score_implementability(with_asset).score > score_implementability(without).score

    def test_trading_arxiv_category_raises_score(self):
        with_cat = make_paper(title="A Study", keywords=["q-fin.tr"])
        without = make_paper(title="A Study", keywords=["q-fin.ec"])
        assert score_implementability(with_cat).score > score_implementability(without).score

    def test_momentum_paper_has_nonzero_implementability(self):
        result = score_implementability(MOMENTUM_PAPER)
        assert result.score > 0.0

    def test_irrelevant_paper_low_implementability(self):
        result = score_implementability(IRRELEVANT_PAPER)
        assert result.score < 0.3

    def test_score_capped_at_1(self):
        # Hit every signal maximally
        paper = make_paper(
            title="momentum strategy with stop loss position sizing rebalancing",
            abstract=(
                "We propose a long-short equity strategy with entry signal exit signal "
                "stop loss and risk parity. Using CRSP daily stock and ETF data. "
                "RSI MACD Bollinger band moving average crossover used."
            ),
            keywords=["q-fin.pm", "q-fin.tr", "q-fin.st"],
        )
        result = score_implementability(paper)
        assert result.score <= 1.0


# ---------------------------------------------------------------------------
# score_reproducibility
# ---------------------------------------------------------------------------

class TestScoreReproducibility:
    def test_returns_dimension_score(self):
        result = score_reproducibility(MOMENTUM_PAPER)
        assert isinstance(result, DimensionScore)

    def test_score_in_range(self):
        for paper in [MOMENTUM_PAPER, IRRELEVANT_PAPER]:
            result = score_reproducibility(paper)
            assert 0.0 <= result.score <= 1.0

    def test_not_llm_assisted(self):
        assert score_reproducibility(MOMENTUM_PAPER).llm_assisted is False

    def test_has_rationale(self):
        result = score_reproducibility(MOMENTUM_PAPER)
        assert result.rationale

    def test_data_source_in_abstract_raises_score(self):
        with_data = make_paper(
            title="A Study",
            abstract="Using CRSP monthly data from 1963 to 2020.",
        )
        without = make_paper(
            title="A Study",
            abstract="We analyse market returns.",
        )
        assert score_reproducibility(with_data).score > score_reproducibility(without).score

    def test_methodology_signals_raise_score(self):
        with_method = make_paper(
            title="A Study",
            abstract=(
                "We use Fama-MacBeth regressions and Newey-West standard errors. "
                "Out-of-sample tests with transaction costs included."
            ),
        )
        without = make_paper(title="A Study", abstract="We examine returns.")
        assert score_reproducibility(with_method).score > score_reproducibility(without).score

    def test_long_abstract_raises_score(self):
        long_abstract = "This paper studies momentum. " * 20  # > 200 chars
        short_abstract = "Studies momentum."
        with_long = make_paper(title="A Study", abstract=long_abstract)
        with_short = make_paper(title="A Study", abstract=short_abstract)
        assert score_reproducibility(with_long).score > score_reproducibility(with_short).score

    def test_recent_paper_gets_recency_bonus(self):
        recent = make_paper(
            title="A Study",
            abstract="Some abstract text here.",
            published_date=date.today() - timedelta(days=100),
        )
        old = make_paper(
            title="A Study",
            abstract="Some abstract text here.",
            published_date=date(2015, 1, 1),
        )
        assert score_reproducibility(recent).score > score_reproducibility(old).score

    def test_momentum_paper_has_high_reproducibility(self):
        result = score_reproducibility(MOMENTUM_PAPER)
        # Has CRSP + out-of-sample + Sharpe ratio + Fama-French + recent
        assert result.score > 0.5

    def test_no_abstract_gives_low_score(self):
        paper = make_paper(title="A Paper", abstract=None)
        result = score_reproducibility(paper)
        assert result.score < 0.3

    def test_score_capped_at_1(self):
        # Maximise all signals
        abstract = (
            "Using CRSP Compustat Bloomberg daily tick data from 1926, "
            "sample period 1963–2023 with Fama-French five factor model, "
            "Fama-MacBeth regressions, Newey-West t-statistics, out-of-sample "
            "walk-forward bootstrap robustness with transaction costs slippage "
            "bid ask spread. Sharpe ratio information ratio maximum drawdown. " * 3
        )
        paper = make_paper(
            title="A Study",
            abstract=abstract,
            published_date=date.today() - timedelta(days=90),
        )
        result = score_reproducibility(paper)
        assert result.score <= 1.0

