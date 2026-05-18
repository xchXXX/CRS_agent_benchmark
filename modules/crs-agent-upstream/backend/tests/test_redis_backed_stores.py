from redis.exceptions import RedisError

from app.agent.memory.deferred_store import DeferredState, DeferredStateStore
from app.agent.memory.message_history_store import MessageHistoryStore


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl_by_key: dict[str, int | None] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.data[key] = value
        self.ttl_by_key[key] = ex
        return True


class FailingRedis(FakeRedis):
    def get(self, key: str):
        raise RedisError("redis get failed")

    def set(self, key: str, value: str, ex: int | None = None):
        raise RedisError("redis set failed")


def test_message_history_store_uses_redis_client():
    client = FakeRedis()
    store = MessageHistoryStore(
        redis_client=client,
        redis_key_prefix="crs_test",
        ttl_seconds=321,
    )

    assert store.save_serialized_history("sess_1", '{"messages": []}') is True
    assert store.load_serialized_history("sess_1") == '{"messages": []}'
    assert client.data["crs_test:message_history:sess_1"] == '{"messages": []}'
    assert client.ttl_by_key["crs_test:message_history:sess_1"] == 321


def test_deferred_state_store_uses_redis_client():
    client = FakeRedis()
    store = DeferredStateStore(
        redis_client=client,
        redis_key_prefix="crs_test",
        ttl_seconds=654,
    )

    state = DeferredState(
        tool_call_id="tool_1",
        tool_name="ask_user_question",
        message_history_json='{"messages": []}',
        payload={"question": "请选择车型"},
    )
    assert store.save("sess_2", state) is True

    loaded = store.load("sess_2", "tool_1")
    assert loaded is not None
    assert loaded.tool_name == "ask_user_question"
    assert loaded.payload["question"] == "请选择车型"
    assert client.ttl_by_key["crs_test:deferred_state:sess_2:tool_1"] == 654


def test_message_history_store_falls_back_to_file_when_redis_fails(tmp_path):
    store = MessageHistoryStore(
        base_dir=str(tmp_path / "history"),
        redis_client=FailingRedis(),
        redis_key_prefix="crs_test",
    )

    assert store.save_serialized_history("sess_3", '{"messages": ["fallback"]}') is True
    assert store.load_serialized_history("sess_3") == '{"messages": ["fallback"]}'


def test_message_history_store_sanitizes_fallback_filename(tmp_path):
    base_dir = tmp_path / "history"
    store = MessageHistoryStore(
        base_dir=str(base_dir),
        redis_client=FailingRedis(),
        redis_key_prefix="crs_test",
    )

    assert store.save_serialized_history("../../etc/passwd", '{"messages": ["safe"]}') is True
    assert store.load_serialized_history("../../etc/passwd") == '{"messages": ["safe"]}'

    created_files = list(base_dir.glob("*.json"))
    assert len(created_files) == 1
    assert created_files[0].parent == base_dir
    assert ".." not in created_files[0].name


def test_deferred_state_store_sanitizes_fallback_filename(tmp_path):
    base_dir = tmp_path / "deferred"
    store = DeferredStateStore(
        base_dir=str(base_dir),
        redis_client=FailingRedis(),
        redis_key_prefix="crs_test",
    )

    state = DeferredState(
        tool_call_id="../tool",
        tool_name="ask_user_question",
        message_history_json='{"messages": []}',
        payload={"question": "请选择车型"},
    )

    assert store.save("../../sess", state) is True

    loaded = store.load("../../sess", "../tool")
    assert loaded is not None
    assert loaded.tool_name == "ask_user_question"

    created_files = list(base_dir.glob("*.json"))
    assert len(created_files) == 1
    assert created_files[0].parent == base_dir
    assert ".." not in created_files[0].name
