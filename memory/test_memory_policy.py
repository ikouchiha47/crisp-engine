"""Black-box tests for lib/memory_policy.py — hardcoded expected values."""
from lib.memory_policy import is_code_index, is_episodic, is_injectable


def test_conversation_is_episodic():
    assert is_episodic("conversation") is True


def test_correction_is_episodic():
    assert is_episodic("correction") is True


def test_code_element_is_not_episodic():
    assert is_episodic("code_element") is False


def test_code_index_dir_is_not_episodic():
    assert is_episodic("code_index_dir") is False


def test_code_element_is_code_index():
    assert is_code_index("code_element") is True


def test_code_index_prefixed_is_code_index():
    assert is_code_index("code_index_dir") is True


def test_conversation_is_not_code_index():
    assert is_code_index("conversation") is False


def test_preference_always_injectable():
    assert is_injectable("preference", confidence=0.0) is True


def test_correction_always_injectable():
    assert is_injectable("correction", confidence=0.0) is True


def test_instinct_below_threshold_not_injectable():
    assert is_injectable("instinct", confidence=0.3) is False


def test_instinct_at_threshold_injectable():
    assert is_injectable("instinct", confidence=0.5) is True


def test_code_element_never_injectable():
    assert is_injectable("code_element", confidence=1.0) is False
