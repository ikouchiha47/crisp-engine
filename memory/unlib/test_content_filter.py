"""
Private validation for ContentFilter. Not for display.
Run: python -m pytest memory/test_content_filter.py -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from lib.content_filter import ContentFilter
from lib.wordlist import WORDLIST

cf = ContentFilter()

# ── helpers ───────────────────────────────────────────────────────────────────

def should_drop(text):
    return cf.clean(text) is None

def should_redact(text):
    result = cf.clean(text)
    return result is not None and "[redacted]" in result

def should_pass(text):
    result = cf.clean(text)
    return result == text

# ── DROP tier: sexual violence ────────────────────────────────────────────────

def test_drop_rape():
    assert should_drop("he raped her")

def test_drop_rape_variant():
    assert should_drop("rapist caught on camera")

def test_drop_sexual_assault():
    assert should_drop("she was sexually assaulted")

def test_drop_molestation():
    assert should_drop("child molestation case")

def test_drop_csam():
    assert should_drop("csam link shared")

def test_drop_incest():
    assert should_drop("incest porn")

# ── DROP tier: slurs ──────────────────────────────────────────────────────────

def test_drop_racial_slur_1():
    assert should_drop("called him a " + "nigger")

def test_drop_racial_slur_2():
    assert should_drop("you " + "chink" + " go home")

def test_drop_homophobic_slur():
    assert should_drop("stop being a " + "faggot")

def test_drop_nazi():
    assert should_drop("neo-nazi rally planned")

def test_drop_white_supremacy():
    assert should_drop("white supremacy group")

# ── DROP tier: self-harm ──────────────────────────────────────────────────────

def test_drop_self_harm_1():
    assert should_drop("I want to kill myself")

def test_drop_self_harm_2():
    assert should_drop("how to commit suicide")

def test_drop_self_harm_3():
    assert should_drop("suicide note found")

# ── REDACT tier: profanity ────────────────────────────────────────────────────

def test_pass_fuck():
    # "fuck" allowed — common frustration/technical expression
    assert should_pass("what the fuck is this")

def test_pass_asshole():
    assert should_pass("he's such an asshole")

def test_pass_bastard():
    assert should_pass("you bastard")

def test_redact_motherfucker():
    assert should_redact("motherfucker broke the build")

def test_redact_bitch():
    assert should_redact("stop being a bitch about it")

def test_redact_cunt():
    assert should_redact("absolute cunt move")

# ── REDACT tier: disability slurs ────────────────────────────────────────────

def test_redact_retard():
    assert should_redact("that's retarded")

def test_redact_spaz():
    assert should_redact("stop being a spaz")

# ── REDACT tier: explicit ─────────────────────────────────────────────────────

def test_redact_porn():
    assert should_redact("watching porn at work")

def test_redact_blowjob():
    assert should_redact("got a blowjob")

# ── PASS: clean technical content ────────────────────────────────────────────

def test_pass_git_commit():
    assert should_pass("feat: add retry logic to the API client")

def test_pass_code_snippet():
    assert should_pass("def compute_hash(content: str) -> str:")

def test_pass_error_log():
    assert should_pass("KeyError: 'session_id' at hooks.py:103")

def test_pass_frustration_clean():
    # "bullshit" and "shit" intentionally allowed (see design note)
    result = cf.clean("this is bullshit, the test keeps failing")
    assert result is not None  # not dropped

def test_pass_mild_expletive():
    result = cf.clean("damn, that's a tricky bug")
    assert result is not None

# ── edge cases ────────────────────────────────────────────────────────────────

def test_empty_string():
    assert cf.clean("") == ""

def test_none_passthrough():
    # disabled filter
    cf2 = ContentFilter(config={"enabled": False})
    assert cf2.clean("rape murder whatever") == "rape murder whatever"

def test_redact_preserves_surrounding_text():
    # "fuck" is allowed; "fucking" is still redacted
    result = cf.clean("the function is broken, this fucking build is a mess")
    assert result is not None
    assert "the function is broken" in result
    assert "[redacted]" in result

# ── run standalone ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
