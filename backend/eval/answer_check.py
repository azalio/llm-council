"""Deterministic ground-truth checks for the checkable benchmark questions.

The LLM-judge and peer-ranking compare answers to each other and reward fluent,
well-structured prose — they do NOT verify arithmetic or a known fact against
ground truth. So an order-of-magnitude slip (e.g. the whale-heartbeat key) would
not be caught by the judge if every model made it. These checks close that blind
spot for the subset of questions that have a verifiable answer: each returns a
CheckResult with `correct` True/False (or None when not applicable).

Confidence levels:
- "exact"  — mechanically decidable (run the regex; the exact number must appear).
- "medium" — robust keyword/value heuristic (abstention flagged; root set present).
- "soft"   — order-of-magnitude sanity only (open Fermi estimate).

Questions without a verifiable answer (open code/proof/explanation) are absent
from CHECKS, i.e. not checkable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class CheckResult:
    checkable: bool
    correct: Optional[bool]
    confidence: str  # "exact" | "medium" | "soft" | "none"
    detail: str


def _norm(text: str) -> str:
    return (text or "").lower()


def _has(text: str, *needles: str) -> bool:
    low = _norm(text)
    return any(n.lower() in low for n in needles)


# ---------------------------------------------------------------------------
# Numeric / exact checks
# ---------------------------------------------------------------------------
def check_inclusion_exclusion(answer: str) -> CheckResult:
    ok = bool(re.search(r"(?<!\d)266(?!\d)", answer))
    return CheckResult(True, ok, "exact", "final count 266 present" if ok else "266 not found")


def check_probability(answer: str) -> CheckResult:
    ok = _has(answer, "671/1296") or bool(re.search(r"0\.517[0-9]?|51\.7\s*%", answer))
    return CheckResult(True, ok, "exact", "671/1296 ≈ 0.5177 present" if ok else "value missing")


def check_bayes(answer: str) -> CheckResult:
    ok = (
        _has(answer, "11/566")
        or bool(re.search(r"0\.019[0-9]|1\.9[0-9]?\s*%", answer))
    )
    return CheckResult(True, ok, "exact", "≈1.94% present" if ok else "value missing")


def check_bat_ball(answer: str) -> CheckResult:
    def money(v: str) -> bool:
        # match 0.05 / $0.05 / 5 cents style for the given dollar value
        return bool(re.search(rf"\$?\s*{re.escape(v)}\b", answer))
    ball = money("0.05") or _has(answer, "5 cents", "5 cent", "5¢")
    bat = money("1.05")
    glove = money("0.15") or _has(answer, "15 cents", "15¢")
    ok = ball and bat and glove
    miss = [n for n, p in (("ball=0.05", ball), ("bat=1.05", bat), ("glove=0.15", glove)) if not p]
    return CheckResult(True, ok, "exact", "all three values present" if ok else f"missing {miss}")


def check_math_exact(answer: str) -> CheckResult:
    # roots {-2, -1, 1, 2}
    has_neg2 = bool(re.search(r"-\s*2\b", answer))
    has_neg1 = bool(re.search(r"-\s*1\b", answer))
    has_pm2 = bool(re.search(r"±\s*2\b|∓\s*2\b", answer)) or has_neg2
    has_pm1 = bool(re.search(r"±\s*1\b|∓\s*1\b", answer)) or has_neg1
    has_pos2 = bool(re.search(r"(?<![\d.\-])2\b", answer))
    has_pos1 = bool(re.search(r"(?<![\d.\-])1\b", answer))
    ok = (has_pm1 or (has_neg1 and has_pos1)) and (has_pm2 or (has_neg2 and has_pos2))
    return CheckResult(True, ok, "medium",
                       "roots {-2,-1,1,2} present" if ok else "full root set not detected")


# ---------------------------------------------------------------------------
# Regex execution check (the strongest one)
# ---------------------------------------------------------------------------
# 25 and 250 are VALID octets, so they stay out of the reject battery.
_IPV4_MATCH = ["0", "1", "9", "10", "25", "42", "99", "100", "127", "199", "200", "249", "250", "255"]
_IPV4_REJECT = ["256", "300", "999", "-1", "01", "001", "00", "1a", "a", "2550", "1000", "-5"]


def _extract_regex(answer: str) -> Optional[str]:
    text = (answer or "").strip()
    # strip code fences
    fenced = re.search(r"```[a-z]*\s*(.+?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    # prefer a line that looks like the octet regex
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        if ("25[0-5]" in line) or (line.startswith("^") and ("[0-9]" in line or r"\d" in line)):
            return line
    # fallback: first non-empty stripped line
    for line in text.splitlines():
        s = line.strip().strip("`").strip()
        if s:
            return s
    return None


def check_regex_ipv4(answer: str) -> CheckResult:
    pat = _extract_regex(answer)
    if not pat:
        return CheckResult(True, False, "exact", "no regex found in answer")
    try:
        rx = re.compile(pat)
    except re.error as exc:
        return CheckResult(True, False, "exact", f"regex did not compile: {exc}")
    bad = []
    for s in _IPV4_MATCH:
        if not rx.fullmatch(s):
            bad.append(f"should match {s!r}")
    for s in _IPV4_REJECT:
        if rx.fullmatch(s):
            bad.append(f"should reject {s!r}")
    ok = not bad
    detail = f"regex {pat!r} passes 0–255 battery" if ok else f"regex {pat!r} failed: {bad[:4]}"
    return CheckResult(True, ok, "exact", detail)


# ---------------------------------------------------------------------------
# Keyword / abstention checks
# ---------------------------------------------------------------------------
def check_false_premise(answer: str) -> CheckResult:
    flagged = _has(
        answer, "did not prove", "never proved", "no such paper", "does not exist",
        "doesn't exist", "premise is false", "premise is flawed", "flawed premise",
        "incorrect premise", "no known paper", "fabricat", "did not write", "no record",
        "is not true", "did not exist",
    )
    return CheckResult(True, flagged, "medium",
                       "false premise flagged" if flagged else "did not flag the false premise")


def check_fake_paper(answer: str) -> CheckResult:
    declined = _has(
        answer, "does not exist", "doesn't exist", "no such paper", "not aware",
        "cannot find", "can't find", "unable to verify", "not certain", "no record",
        "appears to be fabricat", "fabricat", "i could not find", "not familiar",
        "does not appear to exist", "no evidence", "likely does not",
    )
    return CheckResult(True, declined, "medium",
                       "declined / flagged non-existent paper" if declined
                       else "did not flag the fabricated paper")


def check_float_assoc(answer: str) -> CheckResult:
    not_assoc = bool(re.search(r"\bnot associative\b|\bnon-?associative\b", _norm(answer)))
    # accept a leading "no" answer too
    if not not_assoc and re.match(r"\s*(\*\*)?no\b", _norm(answer)):
        not_assoc = True
    return CheckResult(True, not_assoc, "medium",
                       "correctly says not associative" if not_assoc
                       else "did not state non-associativity")


# ---------------------------------------------------------------------------
# Soft order-of-magnitude check (open Fermi)
# ---------------------------------------------------------------------------
def check_whale_magnitude(answer: str) -> CheckResult:
    """The correct order of magnitude is ~10^8 (hundreds of millions to ~1.5 billion).
    Flag answers whose stated estimate is clearly off by an order (tens of billions+)."""
    low = _norm(answer)
    # extract scientific-notation magnitudes like 3.7e8 / 4×10^8 / 10^9
    exps = [int(m) for m in re.findall(r"10\s*[\^*]+\s*(\d+)", low)]
    exps += [int(m) for m in re.findall(r"e\+?(\d+)", low)]
    if exps:
        top = max(exps)
        ok = 8 <= top <= 9
        return CheckResult(True, ok, "soft",
                           f"order 10^{top} ({'plausible' if ok else 'off by an order'})")
    # fall back to words: hundreds of millions or low billions = ok; tens of billions = off
    if _has(answer, "hundred million", "hundreds of millions"):
        return CheckResult(True, True, "soft", "hundreds of millions (plausible)")
    if re.search(r"\b([1-9]\d{1,2})\s*billion", low):  # 10+ billion
        return CheckResult(True, False, "soft", "tens+ of billions (off by an order)")
    return CheckResult(True, None, "soft", "no parseable magnitude")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
CHECKS: dict[str, Callable[[str], CheckResult]] = {
    # v1
    "math_exact": check_math_exact,
    "math_probability": check_probability,
    "false_premise": check_false_premise,
    # v2
    "bayes_base_rate": check_bayes,
    "math_inclusion_exclusion": check_inclusion_exclusion,
    "factual_float_assoc": check_float_assoc,
    "abstention_fake_paper": check_fake_paper,
    "instruction_regex_ipv4": check_regex_ipv4,
    "reasoning_bat_ball": check_bat_ball,
    "fermi_whale_heartbeats": check_whale_magnitude,
}

NOT_CHECKABLE = CheckResult(False, None, "none", "no deterministic ground-truth check")


def check_answer(prompt_id: str, answer: str) -> CheckResult:
    """Run the deterministic check for a prompt id, if one exists."""
    fn = CHECKS.get(prompt_id)
    if fn is None:
        return NOT_CHECKABLE
    if not (answer or "").strip():
        return CheckResult(True, False, "exact", "empty answer")
    return fn(answer)
