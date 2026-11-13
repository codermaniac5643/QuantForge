# tests/research/discovery/test_arxiv.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from quantforge.research.discovery.arxiv import ArxivConnector, _FINANCE_CATEGORY_FILTER
from quantforge.research.discovery.base import SearchQuery


SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>https://arxiv.org/abs/2401.00001v1</id>
    <title>Cross-Sectional Momentum in Equity Markets</title>
    <summary>We study momentum strategies across equity markets.</summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Jane Smith</name></author>
    <author><name>John Doe</name></author>
    <arxiv:doi>10.1234/example</arxiv:doi>
    <category term="q-fin.PM" scheme="http://arxiv.org/schemas/atom"/>
    <category term="q-fin.ST" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>https://arxiv.org/abs/2401.00002v2</id>
    <title>Time-Series Momentum and Trend Following</title>
    <summary>An analysis of time-series momentum strategies.</summary>
    <published>2024-01-10T00:00:00Z</published>
    <author><name>Alice Brown</name></author>
    <category term="q-fin.TR" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""

EMPTY_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>"""


def make_query(**kwargs) -> SearchQuery:
    defaults = {
        "term": "momentum investing",
        "topic_name": "momentum",
        "max_results": 10,
        "lookback_days": 365,
    }
    defaults.update(kwargs)
    return SearchQuery(**defaults)


def make_mock_response(text: str, status_code: int = 200):
    mock = MagicMock()
    mock.text = text
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    return mock


# --- Search Results ---

@pytest.mark.asyncio
async def test_search_returns_papers():
    connector = ArxivConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_ARXIV_XML)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    assert result.success
    assert result.source_name == "arxiv"
    assert len(result.papers) == 2
    assert result.total_found == 2
    assert result.total_fetched == 2


@pytest.mark.asyncio
async def test_search_paper_fields():
    connector = ArxivConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_ARXIV_XML)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    paper = result.papers[0]
    assert paper.title == "Cross-Sectional Momentum in Equity Markets"
    assert paper.arxiv_id == "2401.00001"     # version stripped
    assert paper.doi == "10.1234/example"
    assert paper.abstract is not None
    assert len(paper.authors) == 2
    assert paper.authors[0].name == "Jane Smith"
    assert "q-fin.PM" in paper.keywords


@pytest.mark.asyncio
async def test_search_source_reference():
    connector = ArxivConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_ARXIV_XML)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    paper = result.papers[0]
    assert len(paper.sources) == 1
    assert paper.sources[0].source_name == "arxiv"
    assert paper.sources[0].source_id == "2401.00001"


@pytest.mark.asyncio
async def test_search_empty_results():
    connector = ArxivConnector(request_delay=0)
    mock_response = make_mock_response(EMPTY_ARXIV_XML)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(return_value=mock_response)
        result = await connector.search(make_query())

    assert result.success
    assert len(result.papers) == 0
    assert result.total_found == 0


# --- Error Handling ---

@pytest.mark.asyncio
async def test_search_timeout_recorded_as_error():
    import httpx
    connector = ArxivConnector(request_delay=0, max_retries=1)

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        result = await connector.search(make_query())

    assert not result.success
    assert len(result.errors) == 1
    assert "Timeout" in result.errors[0]


@pytest.mark.asyncio
async def test_search_http_error_recorded():
    import httpx
    connector = ArxivConnector(request_delay=0, max_retries=1)

    mock_response = MagicMock()
    mock_response.status_code = 503
    http_error = httpx.HTTPStatusError(
        "Service Unavailable",
        request=MagicMock(),
        response=mock_response,
    )

    with patch.object(connector, "_get_client") as mock_client:
        mock_client.return_value.get = AsyncMock(side_effect=http_error)
        result = await connector.search(make_query())

    assert not result.success
    assert "503" in result.errors[0]


# --- Context Manager ---

@pytest.mark.asyncio
async def test_connector_context_manager():
    async with ArxivConnector(request_delay=0) as connector:
        assert connector.source_name == "arxiv"


# --- Query construction ---

@pytest.mark.asyncio
async def test_search_query_uses_ti_abs_not_all():
    """
    The query sent to arXiv must scope the term to title/abstract fields (ti:/abs:),
    NOT the bare `all:` prefix.  Using `all:` matches category tags too, which
    allows physics papers that contain the word "momentum" to slip through the
    finance category filter.
    """
    connector = ArxivConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_ARXIV_XML)

    with patch.object(connector, "_get_client") as mock_client:
        mock_get = AsyncMock(return_value=mock_response)
        mock_client.return_value.get = mock_get
        await connector.search(make_query(term="momentum investing"))

    _, kwargs = mock_get.call_args
    sent_query: str = kwargs["params"]["search_query"]

    assert "all:momentum" not in sent_query, (
        "search_query must not use `all:` prefix — it matches category tags "
        "and lets off-topic papers through the category filter"
    )
    assert "ti:momentum" in sent_query or "abs:momentum" in sent_query, (
        "search_query should scope term to title (ti:) or abstract (abs:)"
    )


@pytest.mark.asyncio
async def test_search_query_uses_explicit_category_list():
    """
    'cat:q-fin.*' is a wildcard the arXiv API silently ignores — the
    connector must enumerate the q-fin sub-categories explicitly.
    """
    connector = ArxivConnector(request_delay=0)
    mock_response = make_mock_response(SAMPLE_ARXIV_XML)

    with patch.object(connector, "_get_client") as mock_client:
        mock_get = AsyncMock(return_value=mock_response)
        mock_client.return_value.get = mock_get
        await connector.search(make_query())

    _, kwargs = mock_get.call_args
    sent_query: str = kwargs["params"]["search_query"]

    assert "cat:q-fin.*" not in sent_query, (
        "cat:q-fin.* wildcard is not supported by arXiv API and must not be used"
    )
    # All five in-scope sub-categories must appear
    for subcat in ("cat:q-fin.PM", "cat:q-fin.ST", "cat:q-fin.TR", "cat:q-fin.GN", "cat:q-fin.RM"):
        assert subcat in sent_query, (
            f"{subcat} must be listed explicitly in the category filter"
        )
    # Out-of-scope categories must NOT appear
    for subcat in ("cat:q-fin.CP", "cat:q-fin.MF", "cat:q-fin.PR", "cat:q-fin.EC", "cat:econ", "cat:cs.CE"):
        assert subcat not in sent_query, (
            f"{subcat} is out of scope and must not appear in the category filter"
        )
    assert sent_query.count("cat:q-fin") == 5, (
        "Expected exactly 5 q-fin sub-categories in the filter"
    )