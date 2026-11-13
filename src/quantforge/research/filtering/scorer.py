# src/quantforge/research/filtering/scorer.py
"""
Deterministic relevance, credibility, and implementability scorer.

All scoring is fully deterministic — no LLM is called here.
LLM-assisted evaluation is a separate later step (Milestone 6).

Scoring philosophy (from ROADMAP + PROJECT_SPEC):
  - Keep component scores separate; never hide them inside a single number.
  - Scores support prioritisation — they don't claim scientific certainty.
  - Avoid false precision: round to 4 decimal places at most.
  - Rule-based scores must be clearly distinguished from LLM assessments.
  - Citation count is contextual metadata, not a quality proxy.

Each scorer produces a DimensionScore (from quantforge.models.evaluation)
that carries the numeric value, a human-readable rationale, and a list of
evidence strings that explain why the score was assigned.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from quantforge.models.evaluation import DimensionScore
from quantforge.models.paper import ResearchPaper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists (all lowercase for case-insensitive matching)
# ---------------------------------------------------------------------------

# High-value research signal words — appear in titles / abstracts of papers
# that are likely relevant to QuantForge's scope.
_HIGH_VALUE_TERMS: frozenset[str] = frozenset({
    # Strategies
    "momentum", "trend following", "mean reversion", "breakout",
    "relative strength", "pairs trading", "statistical arbitrage",
    "reversal", "carry", "value investing", "factor investing",
    # Market analysis
    "market regime", "market breadth", "bull market", "bear market",
    "volatility regime", "regime detection", "market timing",
    "advance decline", "business cycle", "macroeconomic",
    # Risk & portfolio
    "risk management", "position sizing", "stop loss", "drawdown",
    "portfolio optimization", "kelly criterion", "volatility targeting",
    "max drawdown", "sharpe ratio", "sortino", "calmar",
    # Technical
    "moving average", "rsi", "macd", "bollinger", "atr",
    "volume profile", "order flow", "market microstructure",
    # Data & methods
    "backtesting", "out of sample", "walk forward", "equity curve",
    "alpha", "beta", "factor model", "cross-sectional", "time-series",
})

# Terms that lower relevance — papers primarily about these are less useful
_LOW_VALUE_TERMS: frozenset[str] = frozenset({
    "clinical trial", "medical imaging", "drug discovery", "genomics",
    "natural language processing", "computer vision", "object detection",
    "speech recognition", "recommender system", "social network",
    "autonomous driving", "robotics", "game playing",
})

# Journals / venues that indicate credibility
_CREDIBLE_VENUES: frozenset[str] = frozenset({
    "journal of finance", "review of financial studies",
    "journal of financial economics", "financial analysts journal",
    "journal of portfolio management", "quantitative finance",
    "journal of financial and quantitative analysis",
    "review of asset pricing studies", "management science",
    "operations research", "journal of econometrics",
    "review of economics and statistics", "econometrica",
    "ssrn", "nber", "journal of banking",
    "journal of risk", "risk magazine",
})

# arXiv categories that indicate quantitative finance relevance
_RELEVANT_ARXIV_CATEGORIES: frozenset[str] = frozenset({
    "q-fin.pm",   # portfolio management
    "q-fin.st",   # statistical finance
    "q-fin.tr",   # trading and market microstructure
    "q-fin.rm",   # risk management
    "q-fin.pr",   # pricing of securities
    "q-fin.mf",   # mathematical finance
    "q-fin.cp",   # computational finance
    "q-fin.ec",   # economics
    "econ.gn",    # general economics
})


# ---------------------------------------------------------------------------
# Public scorer functions
# ---------------------------------------------------------------------------

def score_relevance(
    paper: ResearchPaper,
    topic_keywords: list[str] | None = None,
) -> DimensionScore:
    """
    Score a paper's relevance to QuantForge's research scope.

    Signals (all deterministic):
      +  Title contains high-value terms
      +  Abstract contains high-value terms
      +  Keywords / arXiv categories match high-value terms
      +  Topic-specific keywords provided by the caller
      -  Title or abstract contains low-value terms

    Returns a DimensionScore in [0.0, 1.0].
    """
    text_parts: list[str] = []
    evidence: list[str] = []
    score = 0.0
    max_score = 0.0

    title_lower = paper.title.lower()
    abstract_lower = (paper.abstract or "").lower()
    keywords_lower = " ".join(k.lower() for k in paper.keywords)

    # --- High-value term matching in title (weight: 0.40) ---
    max_score += 0.40
    title_hits = [t for t in _HIGH_VALUE_TERMS if t in title_lower]
    if title_hits:
        # Diminishing returns: first hit gives full weight, more hits give less
        title_weight = min(len(title_hits) * 0.15, 0.40)
        score += title_weight
        evidence.append(f"Title matches: {title_hits[:5]}")

    # --- High-value term matching in abstract (weight: 0.25) ---
    max_score += 0.25
    abstract_hits = [t for t in _HIGH_VALUE_TERMS if t in abstract_lower]
    if abstract_hits:
        abstract_weight = min(len(abstract_hits) * 0.05, 0.25)
        score += abstract_weight
        evidence.append(f"Abstract matches ({len(abstract_hits)} terms)")

    # --- arXiv category match (weight: 0.20) ---
    max_score += 0.20
    kw_lower_set = {k.lower() for k in paper.keywords}
    cat_hits = kw_lower_set & _RELEVANT_ARXIV_CATEGORIES
    if cat_hits:
        score += 0.20
        evidence.append(f"arXiv categories: {sorted(cat_hits)}")

    # --- Topic-specific keyword match (weight: 0.15) ---
    max_score += 0.15
    if topic_keywords:
        all_text = f"{title_lower} {abstract_lower} {keywords_lower}"
        topic_hits = [kw for kw in topic_keywords if kw.lower() in all_text]
        if topic_hits:
            topic_weight = min(len(topic_hits) * 0.05, 0.15)
            score += topic_weight
            evidence.append(f"Topic keywords matched: {topic_hits[:5]}")

    # --- Low-value term penalty ---
    lv_title_hits = [t for t in _LOW_VALUE_TERMS if t in title_lower]
    if lv_title_hits:
        penalty = min(len(lv_title_hits) * 0.15, 0.40)
        score = max(0.0, score - penalty)
        evidence.append(f"Off-topic title terms (penalty): {lv_title_hits[:3]}")

    final = round(min(score, 1.0), 4)

    rationale = (
        f"Deterministic relevance: {len(title_hits)} title hits, "
        f"{len(abstract_hits)} abstract hits."
        + (f" arXiv categories matched." if cat_hits else "")
    )

    return DimensionScore(
        score=final,
        rationale=rationale,
        evidence=evidence,
        llm_assisted=False,
    )


def score_credibility(paper: ResearchPaper) -> DimensionScore:
    """
    Score a paper's credibility using metadata signals.

    Signals (all deterministic — no LLM):
      +  Has a DOI (peer-reviewed or formally deposited)
      +  Has an arXiv ID (open preprint with traceable version)
      +  Published in a credible venue
      +  Has multiple authors (single-author finance papers are rarer at top venues)
      +  Has an abstract (completeness)
      +  Has a publication date (not undated)
      -  No DOI and no arXiv ID (source unclear)

    Returns a DimensionScore in [0.0, 1.0].
    """
    score = 0.0
    evidence: list[str] = []

    # DOI present (0.25)
    if paper.doi:
        score += 0.25
        evidence.append(f"Has DOI: {paper.doi}")

    # arXiv ID present (0.15)
    if paper.arxiv_id:
        score += 0.15
        evidence.append(f"Has arXiv ID: {paper.arxiv_id}")

    # Credible venue (0.30)
    if paper.journal:
        journal_lower = paper.journal.lower()
        if any(v in journal_lower for v in _CREDIBLE_VENUES):
            score += 0.30
            evidence.append(f"Credible venue: {paper.journal!r}")
        else:
            score += 0.10
            evidence.append(f"Venue present (unranked): {paper.journal!r}")

    # Has abstract (0.10)
    if paper.abstract:
        score += 0.10
        evidence.append("Abstract present")

    # Has publication date (0.10)
    if paper.published_date:
        score += 0.10
        evidence.append(f"Publication date: {paper.published_date}")

    # Multiple authors (0.10)
    if len(paper.authors) >= 2:
        score += 0.10
        evidence.append(f"Author count: {len(paper.authors)}")

    final = round(min(score, 1.0), 4)
    rationale = (
        "Deterministic credibility: "
        + ("DOI present. " if paper.doi else "No DOI. ")
        + ("arXiv ID present. " if paper.arxiv_id else "")
        + (f"Venue: {paper.journal!r}." if paper.journal else "No venue.")
    )

    return DimensionScore(
        score=final,
        rationale=rationale,
        evidence=evidence,
        llm_assisted=False,
    )


def score_implementability(paper: ResearchPaper) -> DimensionScore:
    """
    Score the likelihood that a paper's methodology can be implemented.

    This is a lightweight heuristic based on metadata alone —
    real implementability assessment requires reading the paper
    (Milestone 6, LLM evaluation).

    Signals (all deterministic):
      +  Title / abstract contains strategy-specific, rule-like language
      +  Title / abstract mentions specific indicators or parameters
      +  arXiv category is explicitly trading / portfolio management
      -  Title / abstract contains only general market analysis without rules

    Returns a DimensionScore in [0.0, 1.0].
    """
    _RULE_SIGNAL_TERMS: frozenset[str] = frozenset({
        "trading rule", "trading strategy", "investment strategy",
        "portfolio strategy", "entry signal", "exit signal",
        "buy signal", "sell signal", "stop loss", "position sizing",
        "rebalancing", "momentum strategy", "breakout strategy",
        "moving average crossover", "rsi", "macd", "bollinger band",
        "52-week high", "52 week high", "lookback period",
        "holding period", "formation period", "ranking period",
        "long short", "long-short", "equity long short",
        "risk parity", "volatility targeting", "trend filter",
        "market filter", "regime filter", "dollar cost",
        "mean variance", "black litterman", "kelly",
    })

    _ABSTRACT_REQUIRED_TERMS: frozenset[str] = frozenset({
        "equity", "stock", "futures", "etf", "option",
        "cryptocurrency", "bond", "commodity", "currency",
        "s&p", "nasdaq", "russell", "market index",
        "daily return", "monthly return", "weekly return",
        "excess return", "abnormal return", "alpha",
    })

    score = 0.0
    evidence: list[str] = []
    title_lower = paper.title.lower()
    abstract_lower = (paper.abstract or "").lower()
    all_text = f"{title_lower} {abstract_lower}"

    # Strategy rule signals in title (0.35)
    rule_hits_title = [t for t in _RULE_SIGNAL_TERMS if t in title_lower]
    if rule_hits_title:
        weight = min(len(rule_hits_title) * 0.18, 0.35)
        score += weight
        evidence.append(f"Rule signals in title: {rule_hits_title[:4]}")

    # Strategy rule signals in abstract (0.25)
    rule_hits_abstract = [t for t in _RULE_SIGNAL_TERMS if t in abstract_lower]
    if rule_hits_abstract:
        weight = min(len(rule_hits_abstract) * 0.08, 0.25)
        score += weight
        evidence.append(f"Rule signals in abstract ({len(rule_hits_abstract)} terms)")

    # Asset-class / instrument mentions (0.20) — implies empirical strategy
    asset_hits = [t for t in _ABSTRACT_REQUIRED_TERMS if t in all_text]
    if asset_hits:
        weight = min(len(asset_hits) * 0.07, 0.20)
        score += weight
        evidence.append(f"Asset/instrument mentions: {asset_hits[:4]}")

    # arXiv trading/PM category (0.20)
    kw_lower = {k.lower() for k in paper.keywords}
    if kw_lower & {"q-fin.pm", "q-fin.tr", "q-fin.st"}:
        score += 0.20
        evidence.append("arXiv category implies trading/portfolio strategy")

    final = round(min(score, 1.0), 4)
    rationale = (
        "Deterministic implementability: heuristic based on title/abstract strategy "
        "signals and arXiv category. Full assessment requires paper text (Milestone 6)."
    )

    return DimensionScore(
        score=final,
        rationale=rationale,
        evidence=evidence,
        llm_assisted=False,
    )


def score_reproducibility(paper: ResearchPaper) -> DimensionScore:
    """
    Score the likelihood that a paper's results can be reproduced.

    Signals (all deterministic — metadata only):
      +  Abstract mentions data sources, sample periods, or specific metrics
      +  Abstract mentions testing methodology (backtest, out-of-sample, etc.)
      +  Has keywords suggesting rigorous empirical work
      +  Recency: newer papers are more likely to use reproducible data

    Returns a DimensionScore in [0.0, 1.0].
    """
    _DATA_SIGNALS: frozenset[str] = frozenset({
        "crsp", "compustat", "bloomberg", "refinitiv", "datastream",
        "ken french", "wrds", "quandl", "yahoo finance",
        "daily data", "monthly data", "tick data", "intraday",
        "1963", "1926", "sample period", "data from",
        "universe of", "stock universe", "equity universe",
    })

    _METHOD_SIGNALS: frozenset[str] = frozenset({
        "out-of-sample", "out of sample", "walk-forward", "walk forward",
        "robustness", "bootstrap", "monte carlo", "transaction cost",
        "trading cost", "slippage", "market impact", "bid ask",
        "sharpe ratio", "information ratio", "maximum drawdown",
        "risk-adjusted", "statistical significance", "t-statistic",
        "fama macbeth", "fama-macbeth", "newey west", "newey-west",
        "four factor", "five factor", "three factor", "carhart",
        "fama french",
    })

    score = 0.0
    evidence: list[str] = []
    abstract_lower = (paper.abstract or "").lower()

    # Data source signals (0.30)
    data_hits = [t for t in _DATA_SIGNALS if t in abstract_lower]
    if data_hits:
        weight = min(len(data_hits) * 0.10, 0.30)
        score += weight
        evidence.append(f"Data source signals: {data_hits[:4]}")

    # Methodology signals (0.35)
    method_hits = [t for t in _METHOD_SIGNALS if t in abstract_lower]
    if method_hits:
        weight = min(len(method_hits) * 0.07, 0.35)
        score += weight
        evidence.append(f"Methodology signals ({len(method_hits)} terms)")

    # Abstract is present and substantial (0.20)
    if paper.abstract and len(paper.abstract) > 200:
        score += 0.20
        evidence.append(f"Substantial abstract ({len(paper.abstract)} chars)")
    elif paper.abstract:
        score += 0.08
        evidence.append(f"Short abstract ({len(paper.abstract)} chars)")

    # Recency bonus for papers published in the last 5 years (0.15)
    if paper.published_date:
        cutoff = date.today() - timedelta(days=5 * 365)
        if paper.published_date >= cutoff:
            score += 0.15
            evidence.append(f"Recent paper ({paper.published_date})")

    final = round(min(score, 1.0), 4)
    rationale = (
        "Deterministic reproducibility: metadata-level signals only. "
        "Full reproducibility assessment requires reading the paper."
    )

    return DimensionScore(
        score=final,
        rationale=rationale,
        evidence=evidence,
        llm_assisted=False,
    )

