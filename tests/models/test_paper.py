# tests/models/test_paper.py

import pytest
from datetime import date
from quantforge.models.paper import ResearchPaper, SourceReference, Author
from quantforge.models.states import PaperState


def make_source(source_name: str, source_id: str) -> SourceReference:
    return SourceReference(
        source_name=source_name,
        source_id=source_id,
        source_url="https://arxiv.org/abs/2401.00001",
    )


def test_paper_created_with_defaults():
    paper = ResearchPaper(title="A Study on Momentum")
    assert paper.state == PaperState.DISCOVERED
    assert paper.doi is None
    assert paper.arxiv_id is None
    assert paper.abstract is None
    assert paper.sources == []
    assert paper.authors == []
    assert paper.file_path is None
    assert paper.file_hash is None


def test_paper_id_is_unique():
    p1 = ResearchPaper(title="Paper One")
    p2 = ResearchPaper(title="Paper Two")
    assert p1.id != p2.id


def test_add_source_reference():
    paper = ResearchPaper(title="Momentum Study")
    source = make_source("arxiv", "2401.00001")
    paper.add_source(source)
    assert len(paper.sources) == 1
    assert paper.sources[0].source_name == "arxiv"


def test_add_duplicate_source_ignored():
    paper = ResearchPaper(title="Momentum Study")
    source = make_source("arxiv", "2401.00001")
    paper.add_source(source)
    paper.add_source(source)
    assert len(paper.sources) == 1


def test_add_multiple_different_sources():
    paper = ResearchPaper(title="Momentum Study")
    paper.add_source(make_source("arxiv", "2401.00001"))
    paper.add_source(make_source("openalex", "W123456789"))
    assert len(paper.sources) == 2


def test_has_source():
    paper = ResearchPaper(title="Momentum Study")
    paper.add_source(make_source("arxiv", "2401.00001"))
    assert paper.has_source("arxiv")
    assert not paper.has_source("openalex")


def test_paper_with_full_metadata():
    paper = ResearchPaper(
        title="Cross-Sectional Momentum",
        doi="10.1234/example",
        arxiv_id="2401.00001",
        abstract="We study momentum...",
        published_date=date(2024, 1, 15),
        journal="Journal of Finance",
        keywords=["momentum", "equity"],
        authors=[Author(name="Jane Smith", affiliation="MIT")],
    )
    assert paper.title == "Cross-Sectional Momentum"
    assert paper.doi == "10.1234/example"
    assert len(paper.authors) == 1
    assert paper.authors[0].name == "Jane Smith"