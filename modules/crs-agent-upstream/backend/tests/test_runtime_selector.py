from app.agent.runtime.selector import ChatRuntimeMode, RuntimeSelector


def test_runtime_selector_uses_default():
    selector = RuntimeSelector()
    assert selector.resolve() == ChatRuntimeMode.AGENT_LOOP


def test_runtime_selector_accepts_known_mode():
    selector = RuntimeSelector()
    assert selector.resolve("shadow") == ChatRuntimeMode.SHADOW


def test_runtime_selector_falls_back_to_default():
    selector = RuntimeSelector(default_mode=ChatRuntimeMode.SHADOW)
    assert selector.resolve("invalid-mode") == ChatRuntimeMode.SHADOW

