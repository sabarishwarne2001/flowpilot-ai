from unittest.mock import Mock, patch
from uuid import UUID

from app.services.retrieval_service import RetrievalService


WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_empty_work_item_scope_returns_before_search():
    service = RetrievalService()

    with patch(
        "app.services.retrieval_service.intent_service.detect"
    ) as detect, patch(
        "app.services.retrieval_service.hybrid_search_service.search"
    ) as search:
        result = service.hybrid_search(
            workspace_id=WORKSPACE_ID,
            query="anything",
            work_item_ids=[],
            top_k=5,
            similarity_threshold=0.2,
            db=Mock(),
        )

    assert result == []
    detect.assert_called_once()
    search.assert_not_called()


def test_missing_work_item_scope_is_forwarded_as_none():
    service = RetrievalService()

    outcome = Mock()
    outcome.results = []

    with patch(
        "app.services.retrieval_service.intent_service.detect",
        return_value=Mock(confident=False, intent="unknown"),
    ), patch(
        "app.services.retrieval_service.hybrid_search_service.search",
        return_value=outcome,
    ) as search, patch(
        "app.services.retrieval_service.reranker_client.rerank",
        side_effect=lambda *, query, results, request_id: results,
    ):
        result = service.hybrid_search(
            workspace_id=WORKSPACE_ID,
            query="anything",
            work_item_ids=None,
            top_k=5,
            similarity_threshold=0.2,
            db=Mock(),
        )

    assert result == []
    search.assert_called_once()
    assert search.call_args.kwargs["work_item_ids"] is None
