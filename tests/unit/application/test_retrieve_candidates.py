from unittest.mock import Mock

from deployd.application.dtos.retrieval import RetrievalResult
from deployd.application.use_cases.retrieve_candidates import RetrieveCandidates


def test_retrieve_candidates_success() -> None:
    mock_retriever = Mock()
    expected_result = RetrievalResult(candidates=[])
    mock_retriever.retrieve.return_value = expected_result
    use_case = RetrieveCandidates(retriever=mock_retriever)
    result = use_case.execute(query="test incident", top_k=3)
    mock_retriever.retrieve.assert_called_once_with(query="test incident", top_k=3)
    assert result == expected_result


def test_retrieve_candidates_default_top_k() -> None:
    mock_retriever = Mock()
    mock_retriever.retrieve.return_value = RetrievalResult(candidates=[])
    use_case = RetrieveCandidates(retriever=mock_retriever)
    use_case.execute(query="test incident")
    mock_retriever.retrieve.assert_called_once_with(query="test incident", top_k=5)
