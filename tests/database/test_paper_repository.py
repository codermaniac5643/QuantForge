# tests/database/test_paper_repository.py

import pytest
import uuid

from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import (
    PaperRepository,
    DuplicatePaperError,
    InvalidStateTransitionError,
)
from quantforge.models.states import PaperState


@pytest.fixture()
def repo(session):
    return PaperRepository(session)


def make_record(**kwargs) -> PaperRecord:
    """Helper — creates a minimal valid PaperRecord."""
    defaults = {
        "id": uuid.uuid4(),
        "title": "A Study on Momentum Strategies",
        "state": PaperState.DISCOVERED.value,
    }
    defaults.update(kwargs)
    return PaperRecord(**defaults)


# --- Create ---

def test_create_paper(repo):
    record = make_record()
    saved = repo.create(record)
    assert saved.id is not None
    assert saved.title == "A Study on Momentum Strategies"
    assert saved.state == PaperState.DISCOVERED.value


def test_create_paper_with_doi(repo):
    record = make_record(doi="10.1234/test")
    saved = repo.create(record)
    assert saved.doi == "10.1234/test"


def test_create_duplicate_doi_raises(repo):
    repo.create(make_record(doi="10.1234/duplicate"))
    with pytest.raises(DuplicatePaperError):
        repo.create(make_record(doi="10.1234/duplicate"))


def test_create_duplicate_arxiv_id_raises(repo):
    repo.create(make_record(arxiv_id="2401.00001"))
    with pytest.raises(DuplicatePaperError):
        repo.create(make_record(arxiv_id="2401.00001"))


# --- Fetch ---

def test_get_by_id(repo):
    record = make_record()
    repo.create(record)
    fetched = repo.get_by_id(record.id)
    assert fetched is not None
    assert fetched.id == record.id


def test_get_by_id_not_found(repo):
    result = repo.get_by_id(uuid.uuid4())
    assert result is None


def test_get_by_doi(repo):
    repo.create(make_record(doi="10.1234/findme"))
    fetched = repo.get_by_doi("10.1234/findme")
    assert fetched is not None
    assert fetched.doi == "10.1234/findme"


def test_get_by_doi_not_found(repo):
    result = repo.get_by_doi("10.9999/doesnotexist")
    assert result is None


def test_get_by_arxiv_id(repo):
    repo.create(make_record(arxiv_id="2401.99999"))
    fetched = repo.get_by_arxiv_id("2401.99999")
    assert fetched is not None
    assert fetched.arxiv_id == "2401.99999"


def test_get_by_state(repo):
    repo.create(make_record(title="Paper A", state=PaperState.DISCOVERED.value))
    repo.create(make_record(title="Paper B", state=PaperState.DISCOVERED.value))
    repo.create(make_record(title="Paper C", state=PaperState.NORMALIZED.value))

    discovered = repo.get_by_state(PaperState.DISCOVERED)
    assert len(discovered) == 2

    normalized = repo.get_by_state(PaperState.NORMALIZED)
    assert len(normalized) == 1


# --- State Transitions ---

def test_valid_state_transition(repo):
    record = make_record()
    repo.create(record)
    updated = repo.transition_state(record.id, PaperState.NORMALIZED)
    assert updated.state == PaperState.NORMALIZED.value


def test_invalid_state_transition_raises(repo):
    record = make_record()
    repo.create(record)
    with pytest.raises(InvalidStateTransitionError):
        # Cannot jump from DISCOVERED to ACQUIRED
        repo.transition_state(record.id, PaperState.ACQUIRED)


def test_transition_nonexistent_paper_raises(repo):
    with pytest.raises(ValueError):
        repo.transition_state(uuid.uuid4(), PaperState.NORMALIZED)


def test_transition_to_rejected(repo):
    record = make_record()
    repo.create(record)
    updated = repo.transition_state(record.id, PaperState.REJECTED)
    assert updated.state == PaperState.REJECTED.value


# --- Scores ---

def test_update_scores(repo):
    record = make_record()
    repo.create(record)
    updated = repo.update_scores(
        record.id,
        relevance=0.9,
        credibility=0.8,
        composite=0.85,
    )
    assert updated.relevance_score == 0.9
    assert updated.credibility_score == 0.8
    assert updated.composite_score == 0.85
    # Unset scores remain None
    assert updated.implementability_score is None


def test_update_scores_nonexistent_paper_raises(repo):
    with pytest.raises(ValueError):
        repo.update_scores(uuid.uuid4(), relevance=0.5)


# --- Count ---

def test_count_by_state(repo):
    repo.create(make_record(title="P1", state=PaperState.DISCOVERED.value))
    repo.create(make_record(title="P2", state=PaperState.DISCOVERED.value))
    repo.create(make_record(title="P3", state=PaperState.NORMALIZED.value))

    counts = repo.count_by_state()
    assert counts.get("discovered") == 2
    assert counts.get("normalized") == 1


def test_count_by_state_empty(repo):
    counts = repo.count_by_state()
    assert counts == {}