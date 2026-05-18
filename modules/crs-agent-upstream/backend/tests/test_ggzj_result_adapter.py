from types import SimpleNamespace

from app.legacy.services.ggzj.result_adapter import GgzjResultAdapter


class _FakeQueryPreprocessor:
    def __init__(self, _db):
        self._db = _db

    def process(self, query: str):
        return SimpleNamespace(
            original_query=query,
            normalized_query=query,
            corrected_query=query,
            expanded_query=f"{query} 扩展",
            entities={"supplier": ["国方"], "eng_code": ["MDD01"]},
            synonym_expansions={"MDD01": ["MDD01", "MDD-01"]},
            pinyin_corrections=[],
            has_correction=False,
            query_tokens=["国方", "MDD01", "22080203"],
            token_expansions={
                "MDD01": ["MDD01", "MDD-01"],
                "22080203": ["22080203"],
            },
            expanded_fulltext_query=f"{query} MDD-01 22080203",
        )


class _FakeSession:
    def close(self):
        return None


def test_ggzj_result_adapter_build_preprocessing_includes_query_tokens(monkeypatch):
    adapter = GgzjResultAdapter()

    monkeypatch.setattr(
        "app.legacy.models.database.get_session_local",
        lambda: (lambda: _FakeSession()),
    )
    monkeypatch.setattr(
        "app.legacy.services.query_preprocessor.QueryPreprocessor",
        _FakeQueryPreprocessor,
    )

    preprocessing = adapter._build_preprocessing("国方MDD01资料")

    assert preprocessing["original_query"] == "国方MDD01资料"
    assert preprocessing["expanded_query"] == "国方MDD01资料 扩展"
    assert preprocessing["query_tokens"] == ["国方", "MDD01", "22080203"]
    assert preprocessing["token_expansions"]["MDD01"] == ["MDD01", "MDD-01"]
    assert preprocessing["expanded_fulltext_query"] == "国方MDD01资料 MDD-01 22080203"
