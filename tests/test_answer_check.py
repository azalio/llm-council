"""Tests for the deterministic benchmark answer checks."""

from backend.eval.answer_check import CHECKS, check_answer


def test_inclusion_exclusion():
    assert check_answer("math_inclusion_exclusion", "By IE the final count is 266.").correct is True
    assert check_answer("math_inclusion_exclusion", "The answer is 260.").correct is False


def test_probability():
    assert check_answer("math_probability", "1 - (5/6)^4 = 671/1296 ≈ 0.5177").correct is True
    assert check_answer("math_probability", "It is about 0.4 or 40%.").correct is False


def test_bayes():
    assert check_answer("bayes_base_rate", "The posterior is ≈ 1.94%.").correct is True
    assert check_answer("bayes_base_rate", "It is 0.0194 exactly.").correct is True
    assert check_answer("bayes_base_rate", "Roughly 50 percent.").correct is False


def test_bat_ball():
    good = "The ball costs $0.05, the bat $1.05, and the glove $0.15."
    assert check_answer("reasoning_bat_ball", good).correct is True
    bad = "The ball is $0.10, the bat $1.00."
    assert check_answer("reasoning_bat_ball", bad).correct is False


def test_math_exact():
    assert check_answer("math_exact", "The solution set is {-2, -1, 1, 2}.").correct is True
    assert check_answer("math_exact", "x = ±1 and x = ±2.").correct is True
    assert check_answer("math_exact", "The only real solution is x = 3.").correct is False


def test_regex_ipv4_correct():
    good = "^(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$"
    res = check_answer("instruction_regex_ipv4", good)
    assert res.correct is True and res.confidence == "exact"


def test_regex_ipv4_in_code_fence():
    good = "```\n^(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$\n```"
    assert check_answer("instruction_regex_ipv4", good).correct is True


def test_regex_ipv4_too_permissive():
    bad = "^[0-9]{1,3}$"  # matches 256..999
    assert check_answer("instruction_regex_ipv4", bad).correct is False


def test_regex_ipv4_allows_leading_zero_is_wrong():
    bad = "^(0?[0-9]?[0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$"  # matches 01, 001
    assert check_answer("instruction_regex_ipv4", bad).correct is False


def test_false_premise():
    good = "Einstein never proved P = NP; there is no such paper."
    assert check_answer("false_premise", good).correct is True
    bad = "Einstein's 1953 paper argues via a clever reduction that P = NP."
    assert check_answer("false_premise", bad).correct is False


def test_fake_paper():
    good = "I cannot find this paper; it does not appear to exist."
    assert check_answer("abstention_fake_paper", good).correct is True
    bad = "The paper introduces a trillion-parameter retrieval model and shows..."
    assert check_answer("abstention_fake_paper", bad).correct is False


def test_float_assoc():
    assert check_answer("factual_float_assoc", "No, IEEE-754 addition is not associative.").correct is True
    assert check_answer("factual_float_assoc", "Yes, addition is associative.").correct is False


def test_whale_magnitude():
    assert check_answer("fermi_whale_heartbeats", "About 4×10^8 heartbeats.").correct is True
    assert check_answer("fermi_whale_heartbeats", "Roughly 3.7e8 beats.").correct is True
    assert check_answer("fermi_whale_heartbeats", "Around 30 billion heartbeats.").correct is False


def test_non_checkable_returns_not_checkable():
    res = check_answer("code_implement", "class LRUCache: ...")
    assert res.checkable is False and res.correct is None


def test_empty_answer_is_incorrect():
    assert check_answer("math_inclusion_exclusion", "").correct is False


def test_every_check_handles_arbitrary_text():
    # no validator should raise on messy input
    for pid in CHECKS:
        check_answer(pid, "garbage ``` 10^99 256 not associative $0.05 266 ")
