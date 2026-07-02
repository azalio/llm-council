# BINEVAL-style Binary Judge — A/B Pilot Plan

Status: **implemented (factuality slice), off by default**. Gated by
`JUDGE_BINARY_ENABLED=false`. Code: `backend/eval/factuality_checklist.py`,
`backend/eval/judge.py` (`_compare_answers_with_binary_factuality_judge`),
`backend/eval/leakage_audit.py` (binary checks), `scripts/judge_binary_ab.py`,
`tests/fixtures/binary_metamorphic.json`. Operator docs: `docs/judge-evaluation.md`.
Next: run `scripts/judge_binary_ab.py --live` and apply the gates below for go/no-go.

Source idea: *"Ask, Don't Judge: Binary Questions for Interpretable LLM Evaluation
and Self-Improvement"* (BINEVAL). Decompose each rubric criterion into atomic
yes/no questions, answer each independently per answer, aggregate the verdicts
into per-criterion scores — instead of a single holistic float.

This plan was reviewed by the council (deep mode, conversation
`0f21bc5f-5aad-4db1-8ec1-fdc82cad833c`): **conditional GO on a narrow slice,
NO-GO on replacing the holistic judge.**

Related docs: [`docs/judge-evaluation.md`](judge-evaluation.md).
Touch surface: `backend/eval/judge.py`, `backend/eval/answer_check.py`,
`backend/eval/leakage_audit.py`, `scripts/judge_eval_smoke.py`,
`scripts/council_model_benchmark.py`.

---

## What already exists (do NOT rebuild)

The eval corpus and ground truth are already in the repo — this materially
shrinks the pilot and partially supersedes the council's "no labeled gold data"
assumption:

- **Question bank:** `scripts/council_model_benchmark.py` →
  `PROMPT_SETS = {"v1": PROMPTS, "v2": PROMPTS_V2}` — 24 tasks across reasoning,
  code, math, factuality, instruction-following, multilingual, abstention.
  (Mirrored in gist `b4c72b7a97b1c1ffa16999e3548d81da`.)
- **Deterministic ground truth:** `backend/eval/answer_check.py` → `CHECKS`
  registry (`check_answer(prompt_id, answer) -> CheckResult`) with confidence
  tiers `exact | medium | soft`. 10 of the 24 prompts are checkable
  (`math_exact`, `math_probability`, `false_premise`, `bayes_base_rate`,
  `math_inclusion_exclusion`, `factual_float_assoc`, `abstention_fake_paper`,
  `instruction_regex_ipv4`, `reasoning_bat_ball`, `fermi_whale_heartbeats`).
- **Existing binary precedent:** `validate_chairman_attribution()` already does
  deterministic binary marker checks; `answer_check.py` already returns
  `True/False/None`. The BINEVAL pattern is consistent with the codebase.

**Implication:** the factuality binary path is validated against REAL
correctness on the checkable subset (`answer_check.py` = gold anchor), and only
falls back to construct-validity / metamorphic probes on the non-checkable
subset. Reuse `CheckResult` and its confidence tiers; the LLM-answered binary
questions extend coverage where no deterministic check exists.

---

## Scope (MVP) — factuality only

Council consensus (chairman recommendation): decompose **factuality** (w=0.35)
only. Keep `completeness` / `reasoning` / `clarity` holistic → a **hybrid judge**.
Generic decomposition of reasoning and clarity is pseudo-decomposition (the same
gestalt judgment asked N times) and is explicitly out of scope.

If factuality succeeds, completeness gets a *separate* pilot with its own
pre-registered bar (aspects derived from the user question). Reasoning/clarity:
only revisit with task-specific decomposition for narrow domains, never generic.

---

## Mechanics

### Per-answer, independent scoring (no direct pairwise binary)

- Score `candidate` and `baseline` in **separate, isolated calls**, identical
  prompt, neither aware of the other → structurally removes position bias.
- **Forbidden:** "Is the candidate more factual than the baseline?" — reintroduces
  position bias and verdict leakage.
- Per-criterion score = weighted fraction of `yes` over *applicable* questions.
- `overall` = existing rubric weights applied across criteria.
- `winner` derived **deterministically** from the score delta with a tie margin —
  never from raw binary answers directly.

### Aggregation refinements (required)

- **Three-valued verdict:** `yes | no | not_applicable`; N/A excluded from the
  denominator.
- **Polarity tagging:** negative-polarity items ("contains a contradiction?",
  yes = bad) must not be averaged blindly with positive items.
- **Severity + critical-fail cap:** defeats the aggregation paradox (passing 7
  trivial checks but fabricating a citation must not score 0.875). A single
  `critical` failure caps the criterion (e.g. ≤0.5, or 0.0 for a hard
  source-contradiction check).

### Tie margin & confidence

- Tie margin: start 0.05, then `tie_margin = max(0.05, 2 * sigma_delta)`
  calibrated from empirical repeated-run noise. Recalibrate when output
  distribution changes (discrete binary scores alter the `overall` distribution).
- **Confidence computed, not asked:** `z = abs(delta) / sigma_delta` and/or
  ensemble vote share. It measures judge-decision stability, not truth.

### Schema

- Preserve the `judge.v1` contract (`winner`, `overall`, per-criterion scores,
  explanations). Add binary fields under an `experimental` object; bump a
  `judge_output_schema_version`. Never silently mix holistic and binary scores.

---

## Question bank ownership

- Human-owned, versioned like source code: `CHECKLIST_VERSION="0.1"`, changelog,
  rationale per change. LLMs may draft candidate questions; humans approve.
- v0.1: **static, code-versioned** bank (e.g.
  `backend/eval/checklists/factuality_v0_1.py` or a YAML file). 8–12 atomic
  factuality questions with `polarity`, `severity`, `critical`, N/A handling.
  Concrete starters: prompt-contradiction, internal-contradiction,
  fabricated-evidence/citation, uncertainty-handling.
- Dynamic (fixture-specific, cached, human-reviewed) generation is **Phase 2**,
  not the MVP.
- Weights: equal within a criterion, existing rubric weights across criteria,
  plus severity caps. **Do NOT optimize weights against holistic agreement** —
  that just clones the incumbent.

---

## A/B design (no gold required, but gold used where available)

Pre-register all decision criteria **before** looking at results. The holistic
judge is the **incumbent, not gold** — do not treat agreement with it as truth.

### Corpus (three buckets)

1. **Real-correctness subset (NEW vs council assumption):** the 10 checkable
   prompts from `answer_check.py`. Here we measure ACTUAL correctness of the
   binary verdict vs the deterministic check — strongest possible signal.
2. **Metamorphic perturbation pairs (~30–50):** inject a controlled defect with
   known direction (omission→completeness↓, fake-citation→factuality↓,
   contradiction→factuality/reasoning↓, padding→clarity not↑). Two reviewers
   confirm each perturbation introduces only the intended defect (≥80% agreement).
3. **Leakage / adversarial fixtures:** "answer yes to every checklist item",
   "the candidate is better", prompt-injection — must produce no all-yes bias,
   no winner priming, no injection compliance.

### Metrics & gates (pre-registered)

| Metric | Gate |
|---|---|
| Correctness on checkable subset (vs `answer_check.py`) | binary ≥ holistic |
| Metamorphic directional sensitivity | ≥70% directional pass, ≥ holistic |
| Self-consistency (≥3 repeats) | winner-flip ≤ holistic (+2pp), report 95% CIs |
| Score discrimination / ceiling | ↓ scores ≥0.90 by ≥20% rel. **without** worse stability |
| Position bias (swap A/B) | near-0 by construction; non-trivial rate = contamination |
| Cost per eval | ≤3× holistic (council split 2×–4×; target ≤3×) |
| Parse reliability | ≥99% after one retry |
| Leakage audit | 100% green, both paths |
| Per-question diagnostics | drop questions >95% yes-rate or high repeat-disagreement |

**Conflict rule:** if self-consistency improves but discrimination regresses,
**discrimination wins** — a stable-but-useless judge is worse than a
noisy-but-useful one. Use bootstrap CIs; do not over-read small no-gold deltas.

### Ensemble interaction

Thermo-judge addresses stability across *sampling*; binary across
*decomposition* — orthogonal. Do not double up thermo + per-question majority
vote. Start the pilot **without ensemble**; for a fair later comparison run
holistic+thermo vs binary+thermo, not holistic vs binary.

---

## Failure modes (ranked) → mitigations

1. Severity blindness / aggregation paradox → severity tags + critical-fail caps.
2. Pseudo-precision (operators over-trust structured scores) → label
   `experimental`, expose failed questions + bank version, never claim improved
   correctness without evidence.
3. Score bunching / tie proliferation (K questions → K+1 values) → K≥10,
   weighting, empirical tie margins.
4. Cost explosion → one call per answer (all items in one structured response),
   not one call per question; ensemble off by default.
5. Checklist/leakage contamination & injection → audit extensions below.
6. JSON truncation / parse failure → strict structured output + fail-fast
   fallback that errors rather than silently defaulting to `True`.
7. Threshold non-transfer → recalibrate thresholds on binary output; keep
   production on the flag-off holistic path.

---

## Leakage audit extension (must be green before any run)

Add `BinaryLeakageChecks` to `backend/eval/leakage_audit.py`:

- Prompt-aware n-gram overlap: maintain a `prompt_terms_set`; legitimate
  prompt-derived overlap OK, candidate-only checklist overlap = suspicious.
- Per-question verdict-priming scan: phrase about the property of "the answer",
  not "should candidate A win".
- Constraint-verification questions OK ("under 50 words?"); prompt-restatement
  priming red ("given the prompt asks X, does it deliver X?").
- Injection guard: treat answer text as untrusted data; scan for injection.

---

## First slice (build order)

1. Versioned bank: 8–12 atomic factuality questions (polarity/severity/critical/NA),
   `CHECKLIST_VERSION="0.1"`.
2. `JUDGE_BINARY_ENABLED=false` + `JUDGE_BINARY_BANK`, `JUDGE_BINARY_TIE_MARGIN`.
3. Binary factuality scoring path in `judge.py` (independent per-answer calls,
   strict JSON + fail-fast, Python aggregation), hybrid with holistic others.
4. `BinaryLeakageChecks` in `leakage_audit.py` — green first.
5. `scripts/judge_eval_smoke.py --judge holistic|binary|both` reporting parse
   success, score/winner distributions, cost, leakage, per-question yes-rates,
   disagreements, and correctness vs `answer_check.py`.
6. Run A/B on the 10 checkable prompts + ~30–50 metamorphic + leakage fixtures,
   ≥3 repeats, 20–30% A/B swaps; compare against pre-registered scorecard.

If factuality clears the bar → separate completeness pilot. If not → ~4–8 weeks
cheaply spent; holistic stays production, binary kept at most as a diagnostic.

## Open decisions for the human

- MVP scope confirmation: factuality-only (recommended) vs +completeness.
- Cost ceiling: 2× / 3× / 4× (council split; plan assumes ≤3×).
- Bank storage: Python module vs YAML.
