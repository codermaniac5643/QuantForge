# tests/research/acquisition/test_resolver.py
"""
Unit tests for PdfResolver (resolver.py).

All HTTP calls mocked — no real network access.
Covers:
  - arXiv ID → arXiv PDF URL (highest priority, no HTTP call)
  - OA URL in raw_metadata → used directly (no HTTP call)
  - DOI → Unpaywall fallback
  - DOI → Semantic Scholar fallback
  - All sources fail → ResolvedUrl(url=None)
  - No arXiv ID, no DOI → immediate None
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

from quantforge.models.paper import ResearchPaper, SourceReference
from quantforge.models.states import PaperState
from quantforge.research.acquisition.resolver import PdfResolver, ResolvedUrl


def make_paper(arxiv_id=None, doi=None, sources=None) -> ResearchPaper:
    return ResearchPaper(
        title="Test Paper",
        arxiv_id=arxiv_id,
        doi=doi,
        sources=sources or [],
        state=PaperState.APPROVED,
    )


def make_client() -> MagicMock:
    return MagicMock(spec=httpx.AsyncClient)


def mock_response(json_data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


@pytest.mark.asyncio
class TestPdfResolverArxiv:
    async def test_arxiv_id_resolves_without_http(self):
        """arXiv should resolve immediately without any HTTP call."""
        paper = make_paper(arxiv_id="2401.00001")
        client = make_client()
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)

        assert result.url == "https://arxiv.org/pdf/2401.00001"
        assert result.source == "arxiv"
        client.get.assert_not_called()

    async def test_arxiv_takes_priority_over_doi(self):
        """Even if DOI is set, arXiv URL is returned when arXiv ID exists."""
        paper = make_paper(arxiv_id="2401.00001", doi="10.1234/x")
        resolver = PdfResolver(client=make_client())
        result = await resolver.resolve(paper)
        assert result.source == "arxiv"


@pytest.mark.asyncio
class TestPdfResolverOaMetadata:
    async def test_oa_url_in_raw_metadata_used(self):
        """OA URL in source raw_metadata should be used without HTTP call."""
        src = SourceReference(
            source_name="openalex",
            source_id="W1",
            raw_metadata={"is_open_access": True, "oa_url": "https://oa.example.com/paper.pdf"},
        )
        paper = make_paper(doi="10.1234/x", sources=[src])
        client = make_client()
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)

        assert result.url == "https://oa.example.com/paper.pdf"
        assert result.source == "oa_metadata"
        client.get.assert_not_called()

    async def test_oa_flag_false_skips_oa_url(self):
        """is_open_access=False means OA URL should not be used."""
        src = SourceReference(
            source_name="openalex",
            source_id="W1",
            raw_metadata={"is_open_access": False, "oa_url": "https://oa.example.com/paper.pdf"},
        )
        paper = make_paper(doi="10.1234/x", sources=[src])
        client = make_client()
        client.get = AsyncMock(return_value=mock_response({"best_oa_location": None}))
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)
        # Should fall through to Unpaywall/SS
        assert result.source != "oa_metadata"


@pytest.mark.asyncio
class TestPdfResolverUnpaywall:
    async def test_unpaywall_pdf_url_used(self):
        paper = make_paper(doi="10.1234/x")
        unpaywall_resp = mock_response({
            "best_oa_location": {"url_for_pdf": "https://unpaywall.example.com/paper.pdf"}
        })
        ss_resp = mock_response({"openAccessPdf": None})
        client = make_client()
        client.get = AsyncMock(side_effect=[unpaywall_resp, ss_resp])
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)

        assert result.url == "https://unpaywall.example.com/paper.pdf"
        assert result.source == "unpaywall"

    async def test_unpaywall_404_falls_through(self):
        paper = make_paper(doi="10.1234/x")
        unpaywall_resp = mock_response({}, status=404)
        ss_resp = mock_response({"openAccessPdf": {"url": "https://ss.example.com/paper.pdf"}})
        client = make_client()
        client.get = AsyncMock(side_effect=[unpaywall_resp, ss_resp])
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)

        assert result.source == "semantic_scholar"

    async def test_unpaywall_no_pdf_url_falls_through(self):
        paper = make_paper(doi="10.1234/x")
        # Unpaywall returns null best_oa_location
        unpaywall_resp = mock_response({"best_oa_location": None})
        ss_resp = mock_response({"openAccessPdf": {"url": "https://ss.example.com/p.pdf"}})
        client = make_client()
        client.get = AsyncMock(side_effect=[unpaywall_resp, ss_resp])
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)
        assert result.source == "semantic_scholar"

    async def test_unpaywall_exception_falls_through(self):
        paper = make_paper(doi="10.1234/x")
        client = make_client()
        client.get = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout"),  # unpaywall fails
                mock_response({"openAccessPdf": {"url": "https://ss.example.com/p.pdf"}}),
            ]
        )
        resolver = PdfResolver(client=client)
        result = await resolver.resolve(paper)
        assert result.source == "semantic_scholar"


@pytest.mark.asyncio
class TestPdfResolverSemanticScholar:
    async def test_semantic_scholar_url_used(self):
        paper = make_paper(doi="10.1234/x")
        client = make_client()
        client.get = AsyncMock(side_effect=[
            mock_response({"best_oa_location": None}),  # unpaywall
            mock_response({"openAccessPdf": {"url": "https://ss.example.com/paper.pdf"}}),
        ])
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)

        assert result.url == "https://ss.example.com/paper.pdf"
        assert result.source == "semantic_scholar"

    async def test_semantic_scholar_exception_gives_none(self):
        paper = make_paper(doi="10.1234/x")
        client = make_client()
        client.get = AsyncMock(
            side_effect=[
                mock_response({"best_oa_location": None}),
                httpx.TimeoutException("timeout"),
            ]
        )
        resolver = PdfResolver(client=client)
        result = await resolver.resolve(paper)
        assert result.url is None
        assert result.source is None


@pytest.mark.asyncio
class TestPdfResolverAllFail:
    async def test_no_arxiv_no_doi_returns_none(self):
        paper = make_paper()  # no arxiv_id, no doi
        resolver = PdfResolver(client=make_client())
        result = await resolver.resolve(paper)
        assert result.url is None
        assert result.failure_reason is not None
        assert "doi" in result.failure_reason.lower() or "arxiv" in result.failure_reason.lower()

    async def test_all_external_apis_fail_returns_none(self):
        paper = make_paper(doi="10.1234/x")
        client = make_client()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        resolver = PdfResolver(client=client)

        result = await resolver.resolve(paper)

        assert result.url is None
        assert result.failure_reason is not None

    async def test_mailto_passed_to_unpaywall(self):
        paper = make_paper(doi="10.1234/x")
        client = make_client()
        client.get = AsyncMock(return_value=mock_response({"best_oa_location": None}))
        resolver = PdfResolver(client=client, mailto="test@example.com")

        await resolver.resolve(paper)

        # First call should be Unpaywall; check it was called with email param
        first_call = client.get.call_args_list[0]
        params = first_call.kwargs.get("params", {})
        assert params.get("email") == "test@example.com"

