# tests/research/acquisition/test_manager.py
"""
Unit tests for AcquisitionManager (manager.py).

All HTTP calls and DB interactions mocked.
Covers:
  - Weekly cap enforced (checked against DB)
  - Monthly cap enforced
  - Manual approval mode: CANDIDATE state blocked, APPROVED state proceeds
  - Auto approval mode: CANDIDATE above threshold auto-approved
  - Auto approval mode: CANDIDATE below threshold rejected
  - Atomic placement: temp file → final path (failure leaves APPROVED)
  - Download failure leaves paper in APPROVED state for retry
  - Validation failure transitions paper to REJECTED
  - URL resolution failure transitions to REJECTED
  - Already-acquired paper skipped (idempotent)
  - Acquisition provenance stored on PaperRecord
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from quantforge.config.loader import AcquisitionConfig
from quantforge.database.models.paper import PaperRecord
from quantforge.models.states import PaperState
from quantforge.research.acquisition.manager import AcquisitionManager
from quantforge.research.acquisition.resolver import ResolvedUrl
from quantforge.research.acquisition.downloader import DownloadResult
from quantforge.research.acquisition.validator import ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> AcquisitionConfig:
    defaults = dict(
        max_papers_per_week=5,
        max_papers_per_month=20,
        approval_mode="manual",
        storage_path="/tmp/research_library",
        max_pdf_size_mb=50,
    )
    defaults.update(kwargs)
    return AcquisitionConfig(**defaults)


def make_record(
    state: PaperState = PaperState.APPROVED,
    arxiv_id: str | None = "2401.00001",
    doi: str | None = None,
    composite_score: float | None = 0.7,
    file_path: str | None = None,
    file_hash: str | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    r = MagicMock(spec=PaperRecord)
    r.id = uuid4()
    r.state = state.value
    r.arxiv_id = arxiv_id
    r.doi = doi
    r.title = "A Test Paper"
    r.composite_score = composite_score
    r.file_path = file_path
    r.file_hash = file_hash
    r.file_size_bytes = None
    r.sources = []
    r.evaluation = None
    r.updated_at = updated_at or datetime.now(timezone.utc)
    return r


def make_acquired_record(age_days: int = 1) -> MagicMock:
    """Return a mock PaperRecord already in ACQUIRED state."""
    dt = datetime.now(timezone.utc) - timedelta(days=age_days)
    return make_record(state=PaperState.ACQUIRED, updated_at=dt)


def make_manager(
    record: MagicMock,
    config: AcquisitionConfig | None = None,
    acquired_records: list | None = None,
    storage_root: Path | None = None,
) -> tuple[AcquisitionManager, MagicMock]:
    """Build an AcquisitionManager with a mocked PaperRepository."""
    cfg = config or make_config()
    repo = MagicMock()
    repo.get_by_id.return_value = record
    repo.get_by_state.return_value = acquired_records or []
    repo.transition_state.return_value = record
    repo.session = MagicMock()

    client = MagicMock()
    root = storage_root or Path("/tmp/test_research_lib")
    mgr = AcquisitionManager(
        repo=repo,
        config=cfg,
        storage_root=root,
        client=client,
    )
    return mgr, repo


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAcquisitionCaps:
    async def test_weekly_cap_blocks_download(self, tmp_path):
        config = make_config(max_papers_per_week=2)
        record = make_record()
        # 2 papers already acquired this week
        acquired = [make_acquired_record(age_days=1), make_acquired_record(age_days=2)]
        mgr, repo = make_manager(record, config=config, acquired_records=acquired, storage_root=tmp_path)

        with patch.object(mgr, "_ensure_approved", return_value=None):
            outcome = await mgr.acquire(record.id)

        assert not outcome.success
        assert "weekly" in outcome.failure_reason.lower()
        # State must NOT change — paper stays APPROVED for retry
        assert outcome.final_state == PaperState.APPROVED

    async def test_monthly_cap_blocks_download(self, tmp_path):
        config = make_config(max_papers_per_week=99, max_papers_per_month=2)
        record = make_record()
        # 2 papers acquired this month but older than 7 days (not counted weekly)
        acquired = [make_acquired_record(age_days=10), make_acquired_record(age_days=15)]
        mgr, repo = make_manager(record, config=config, acquired_records=acquired, storage_root=tmp_path)

        with patch.object(mgr, "_ensure_approved", return_value=None):
            outcome = await mgr.acquire(record.id)

        assert not outcome.success
        assert "monthly" in outcome.failure_reason.lower()
        assert outcome.final_state == PaperState.APPROVED

    async def test_cap_not_hit_proceeds(self, tmp_path):
        config = make_config(max_papers_per_week=10, max_papers_per_month=50)
        record = make_record()
        mgr, repo = make_manager(record, config=config, acquired_records=[], storage_root=tmp_path)

        # Patch inner steps so we isolate cap logic
        with (
            patch.object(mgr, "_ensure_approved", return_value=None),
            patch(
                "quantforge.research.acquisition.manager.PdfResolver.resolve",
                new_callable=AsyncMock,
                return_value=ResolvedUrl(url=None, failure_reason="no url"),
            ),
        ):
            outcome = await mgr.acquire(record.id)

        # Should NOT fail due to caps (falls through to resolution failure)
        assert "cap" not in (outcome.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# Approval mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestApprovalMode:
    async def test_manual_mode_candidate_state_blocked(self, tmp_path):
        config = make_config(approval_mode="manual")
        record = make_record(state=PaperState.CANDIDATE)
        mgr, _ = make_manager(record, config=config, storage_root=tmp_path)

        outcome = await mgr.acquire(record.id)

        assert not outcome.success
        assert "manual" in outcome.failure_reason.lower() or "approved" in outcome.failure_reason.lower()

    async def test_manual_mode_approved_state_proceeds(self, tmp_path):
        """APPROVED state paper can proceed in manual mode."""
        config = make_config(approval_mode="manual")
        record = make_record(state=PaperState.APPROVED)
        mgr, _ = make_manager(record, config=config, acquired_records=[], storage_root=tmp_path)

        with patch(
            "quantforge.research.acquisition.manager.PdfResolver.resolve",
            new_callable=AsyncMock,
            return_value=ResolvedUrl(url=None, failure_reason="no url"),
        ):
            outcome = await mgr.acquire(record.id)

        # Approval gate was passed; failed at resolution
        assert "manual" not in (outcome.failure_reason or "").lower()

    async def test_auto_mode_candidate_above_threshold_approved(self, tmp_path):
        config = make_config(approval_mode="auto")
        record = make_record(state=PaperState.CANDIDATE, composite_score=0.8)
        mgr, repo = make_manager(record, config=config, acquired_records=[], storage_root=tmp_path)

        with patch(
            "quantforge.research.acquisition.manager.PdfResolver.resolve",
            new_callable=AsyncMock,
            return_value=ResolvedUrl(url=None, failure_reason="no url"),
        ):
            outcome = await mgr.acquire(record.id, auto_approve_threshold=0.5)

        # transition_state called with APPROVED
        calls = [str(c) for c in repo.transition_state.call_args_list]
        assert any("approved" in c.lower() for c in calls)

    async def test_auto_mode_candidate_below_threshold_blocked(self, tmp_path):
        config = make_config(approval_mode="auto")
        record = make_record(state=PaperState.CANDIDATE, composite_score=0.3)
        mgr, _ = make_manager(record, config=config, storage_root=tmp_path)

        outcome = await mgr.acquire(record.id, auto_approve_threshold=0.5)

        assert not outcome.success
        assert "threshold" in outcome.failure_reason.lower()


# ---------------------------------------------------------------------------
# Already-acquired guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAlreadyAcquiredGuard:
    async def test_already_acquired_paper_skipped(self, tmp_path):
        record = make_record(
            state=PaperState.APPROVED,
            file_path="/some/path/paper.pdf",
            file_hash="abc123",
        )
        mgr, repo = make_manager(record, storage_root=tmp_path)

        with patch.object(mgr, "_ensure_approved", return_value=None):
            outcome = await mgr.acquire(record.id)

        assert outcome.success
        assert outcome.final_state == PaperState.ACQUIRED
        # transition_state should NOT be called (paper was already acquired)
        repo.transition_state.assert_not_called()


# ---------------------------------------------------------------------------
# URL resolution failure → REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestResolutionFailure:
    async def test_no_url_transitions_to_rejected(self, tmp_path):
        record = make_record(state=PaperState.APPROVED)
        mgr, repo = make_manager(record, acquired_records=[], storage_root=tmp_path)

        with patch(
            "quantforge.research.acquisition.manager.PdfResolver.resolve",
            new_callable=AsyncMock,
            return_value=ResolvedUrl(url=None, failure_reason="not available"),
        ):
            outcome = await mgr.acquire(record.id)

        assert not outcome.success
        repo.transition_state.assert_called_once()
        called_state = repo.transition_state.call_args[0][1]
        assert called_state == PaperState.REJECTED


# ---------------------------------------------------------------------------
# Download failure → leaves APPROVED for retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDownloadFailure:
    async def test_download_failure_leaves_approved_state(self, tmp_path):
        record = make_record(state=PaperState.APPROVED)
        mgr, repo = make_manager(record, acquired_records=[], storage_root=tmp_path)

        with (
            patch(
                "quantforge.research.acquisition.manager.PdfResolver.resolve",
                new_callable=AsyncMock,
                return_value=ResolvedUrl(url="https://example.com/paper.pdf", source="arxiv"),
            ),
            patch(
                "quantforge.research.acquisition.manager.download_pdf",
                new_callable=AsyncMock,
                return_value=DownloadResult(success=False, failure_reason="connection refused"),
            ),
        ):
            outcome = await mgr.acquire(record.id)

        assert not outcome.success
        assert outcome.final_state == PaperState.APPROVED
        # transition_state must NOT have been called (stays APPROVED for retry)
        repo.transition_state.assert_not_called()


# ---------------------------------------------------------------------------
# Validation failure → REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestValidationFailure:
    async def test_invalid_pdf_transitions_to_rejected(self, tmp_path):
        dest = tmp_path / str(uuid4()) / "paper.pdf"
        record = make_record(state=PaperState.APPROVED)
        mgr, repo = make_manager(record, acquired_records=[], storage_root=tmp_path)

        with (
            patch(
                "quantforge.research.acquisition.manager.PdfResolver.resolve",
                new_callable=AsyncMock,
                return_value=ResolvedUrl(url="https://example.com/p.pdf", source="arxiv"),
            ),
            patch(
                "quantforge.research.acquisition.manager.download_pdf",
                new_callable=AsyncMock,
                return_value=DownloadResult(
                    success=True,
                    dest_path=dest,
                    bytes_written=100,
                ),
            ),
            patch(
                "quantforge.research.acquisition.manager.validate_pdf",
                return_value=ValidationResult(
                    valid=False,
                    failure_reason="not a PDF",
                    sha256="abc",
                    size_bytes=100,
                ),
            ),
        ):
            outcome = await mgr.acquire(record.id)

        assert not outcome.success
        assert outcome.final_state == PaperState.REJECTED
        repo.transition_state.assert_called_once()
        called_state = repo.transition_state.call_args[0][1]
        assert called_state == PaperState.REJECTED


# ---------------------------------------------------------------------------
# Successful acquisition → ACQUIRED + provenance stored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSuccessfulAcquisition:
    async def test_success_transitions_to_acquired(self, tmp_path):
        dest = tmp_path / str(uuid4()) / "2401.00001.pdf"
        record = make_record(state=PaperState.APPROVED)
        mgr, repo = make_manager(record, acquired_records=[], storage_root=tmp_path)

        with (
            patch(
                "quantforge.research.acquisition.manager.PdfResolver.resolve",
                new_callable=AsyncMock,
                return_value=ResolvedUrl(url="https://arxiv.org/pdf/2401.00001", source="arxiv"),
            ),
            patch(
                "quantforge.research.acquisition.manager.download_pdf",
                new_callable=AsyncMock,
                return_value=DownloadResult(
                    success=True,
                    dest_path=dest,
                    bytes_written=1024,
                ),
            ),
            patch(
                "quantforge.research.acquisition.manager.validate_pdf",
                return_value=ValidationResult(
                    valid=True,
                    sha256="a" * 64,
                    size_bytes=1024,
                ),
            ),
        ):
            outcome = await mgr.acquire(record.id)

        assert outcome.success
        assert outcome.final_state == PaperState.ACQUIRED
        assert outcome.sha256 == "a" * 64
        assert outcome.size_bytes == 1024
        assert outcome.source_url == "https://arxiv.org/pdf/2401.00001"

    async def test_success_stores_provenance_on_record(self, tmp_path):
        dest = tmp_path / str(uuid4()) / "2401.00001.pdf"
        record = make_record(state=PaperState.APPROVED)
        record.evaluation = {}
        mgr, repo = make_manager(record, acquired_records=[], storage_root=tmp_path)

        with (
            patch(
                "quantforge.research.acquisition.manager.PdfResolver.resolve",
                new_callable=AsyncMock,
                return_value=ResolvedUrl(url="https://arxiv.org/pdf/2401.00001", source="arxiv"),
            ),
            patch(
                "quantforge.research.acquisition.manager.download_pdf",
                new_callable=AsyncMock,
                return_value=DownloadResult(
                    success=True,
                    dest_path=dest,
                    bytes_written=1024,
                ),
            ),
            patch(
                "quantforge.research.acquisition.manager.validate_pdf",
                return_value=ValidationResult(
                    valid=True,
                    sha256="b" * 64,
                    size_bytes=1024,
                ),
            ),
        ):
            await mgr.acquire(record.id)

        # file_path, file_hash, file_size_bytes must be set on the record
        assert record.file_path == str(dest)
        assert record.file_hash == "b" * 64
        assert record.file_size_bytes == 1024
        # Acquisition metadata stored in evaluation JSON
        assert "acquisition" in record.evaluation
        acq = record.evaluation["acquisition"]
        assert acq["sha256"] == "b" * 64
        assert acq["source_url"] == "https://arxiv.org/pdf/2401.00001"

