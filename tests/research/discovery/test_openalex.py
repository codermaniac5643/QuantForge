# tests/research/discovery/test_openalex.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from quantforge.research.discovery.openalex import OpenAlexConnector
from quantforge.research.discovery.base import SearchQuery


SAMPLE_OPENALEX_RESPONSE = {
    "meta": {"count": 2, "page": 1, "per_page": 25},
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.1234/momentum",
            "title": "Cross-Sectional Momentum Strategies",
            "abstract_inverted_index": {
                "We": [0],
                "study": [1],
                "momentum": [2],
                "strategies": [3],
            },
            "publication_date": "2024-01-15",
            "authorships": [
                {
                    "author": {"display_name": "Jane Smith"},
                    "institutions": [{"display_name": "MIT"}],
                },
                {
                    "author": {"display_name": "John Doe"},
                    "institutions": [],
                },
            ],
            "primary_location": {
                "source": {"display_name": "Journal of Finance"}
            },
            "keywords": [
                {"display_name": "momentum"},
                {"display_name": "equity markets"},
            ],
            "cited_by_count": 42,
            "open_access": {"is_oa": True},
        },
        {
            "id": "https://openalex.org/W1234567890",
            "doi": None,
            "title": "Time-Series Momentum",
            "abstract_inverted_index": None,
            "publication_date": "2024-01-10",
            "authorships": [
                {"author": {"display_name": "Alice Brown"}, "institutions": []}
            ],
            "primary_location": None,
            "keywords": [],
            "cited_by_count": 5,
            "open_access": {"is_oa": False},
        },
    ],
}

EMPTY_OPENALEX_RESPONSE = {
    "meta": {"count": 0, "page": 1, "per_page": 25},
    "results": [],
}


def make_query(**kwargs) -> SearchQuery:
    defaults = {
        "term": "momentum investing",
        "topic_name": "momentum",
        "max_results": 10,
        "lookback_days": 365,
    }
    defaults.update(kwargs)
    return SearchQuery(**defaults)


def make_mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.json = MagicMock(return_value=data)
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    return mock


# --- Search Results ---

@pytest.mark.asyncio
async def test_search_returns_papers():
    connector = OpenAlexConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_OPENALEX_RESPONSE)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    assert result.success
    assert result.source_name == "openalex"
    assert len(result.papers) == 2
    assert result.total_found == 2


@pytest.mark.asyncio
async def test_search_paper_fields():
    connector = OpenAlexConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_OPENALEX_RESPONSE)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    paper = result.papers[0]
    assert paper.title == "Cross-Sectional Momentum Strategies"
    assert paper.doi == "10.1234/momentum"       # URL prefix stripped
    assert paper.abstract == "We study momentum strategies"
    assert paper.journal == "Journal of Finance"
    assert len(paper.authors) == 2
    assert paper.authors[0].name == "Jane Smith"
    assert paper.authors[0].affiliation == "MIT"
    assert "momentum" in paper.keywords


@pytest.mark.asyncio
async def test_search_source_reference():
    connector = OpenAlexConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_OPENALEX_RESPONSE)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    paper = result.papers[0]
    assert paper.sources[0].source_name == "openalex"
    assert paper.sources[0].source_id == "W2741809807"
    assert paper.sources[0].raw_metadata["cited_by_count"] == 42
    assert paper.sources[0].raw_metadata["is_open_access"] is True


@pytest.mark.asyncio
async def test_paper_with_no_abstract():
    connector = OpenAlexConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_OPENALEX_RESPONSE)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    # Second paper has no abstract_inverted_index
    paper = result.papers[1]
    assert paper.abstract is None


@pytest.mark.asyncio
async def test_search_empty_results():
    connector = OpenAlexConnector(request_delay=0)
    mock_response = make_mock_response(EMPTY_OPENALEX_RESPONSE)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    assert result.success
    assert len(result.papers) == 0
    assert result.total_found == 0


# --- Abstract Reconstruction ---

def test_reconstruct_abstract():
    connector = OpenAlexConnector()
    inverted = {
        "We": [0],
        "study": [1],
        "momentum": [2],
        "strategies": [3],
    }
    result = connector._reconstruct_abstract(inverted)
    assert result == "We study momentum strategies"


def test_reconstruct_abstract_none():
    connector = OpenAlexConnector()
    assert connector._reconstruct_abstract(None) is None


def test_reconstruct_abstract_empty():
    connector = OpenAlexConnector()
    assert connector._reconstruct_abstract({}) is None


# --- Error Handling ---

@pytest.mark.asyncio
async def test_search_timeout_recorded_as_error():
    import httpx
    connector = OpenAlexConnector(request_delay=0, max_retries=1)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        result = await connector.search(make_query())

    assert not result.success
    assert "Timeout" in result.errors[0]


@pytest.mark.asyncio
async def test_search_http_error_recorded():
    import httpx
    connector = OpenAlexConnector(request_delay=0, max_retries=1)

    mock_response = MagicMock()
    mock_response.status_code = 429
    http_error = httpx.HTTPStatusError(
        "Too Many Requests",
        request=MagicMock(),
        response=mock_response,
    )

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(side_effect=http_error)
        result = await connector.search(make_query())

    assert not result.success
    assert "429" in result.errors[0]


# --- Context Manager ---

@pytest.mark.asyncio
async def test_connector_context_manager():
    async with OpenAlexConnector(request_delay=0) as connector:
        assert connector.source_name == "openalex"