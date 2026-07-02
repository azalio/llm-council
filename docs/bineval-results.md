# BINEVAL pilot — session results & handoff

Working log so this survives a `/compact`.

**STATUS: replication DONE.** The earlier binary-judge "no-go" (§1–§4) was an
*out-of-envelope* pairwise probe (§5). The honest, in-envelope replication on the
paper's own protocol (QAGS, task-level generated questions, source-grounded,
pointwise, human-correlation) is now complete — see **§8**. Verdict is unchanged
and now properly grounded: **holistic ≥ BINEVAL in every cell; BINEVAL never
wins and is significantly worse with a weak evaluator, at 7× the cost.**

## 0. Repo state
- Branch: `feat/binary-factuality-judge` (NOT committed; no PR).
- `python -m pytest -q` → 247 passed. `python scripts/audit_eval_leakage.py` → exit 0.
- All new behavior is behind flags, **default OFF**. Production path unchanged.

## 1. Hypotheses tested
- **H1 (BINEVAL core):** decomposing the `factuality` criterion into atomic
  yes/no questions discriminates the better answer more reliably than one holistic
  score (and avoids ceiling clustering).
- **H2 (our own extrapolation, NOT from the paper):** scoring each answer in
  isolation removes pairwise position bias; generalized to order-swap (judge) and
  Stage 2 per-ranker counterbalancing.

## 2. What was implemented (all flags default OFF)
- `backend/config.py`: `JUDGE_BINARY_ENABLED`, `JUDGE_BINARY_TIE_MARGIN` (0.05),
  `JUDGE_BINARY_CRITICAL_CAP` (0.5), `JUDGE_ORDER_SWAP_ENABLED`,
  `COUNCIL_STAGE2_COUNTERBALANCE` (→ `STAGE2_COUNTERBALANCE_ENABLED`).
- `backend/eval/factuality_checklist.py` (NEW): `CHECKLIST_VERSION="0.1"`,
  `ChecklistQuestion` (id/text/polarity/critical), 9 static generic questions.
- `backend/eval/judge.py`: hybrid binary path
  (`_compare_answers_with_binary_factuality_judge`, isolated per-answer calls,
  winner from `overall` delta vs tie margin, `experimental.binary_factuality`);
  order-swap path (`_compare_answers_with_order_swap_judge`, both orders →
  agree=winner / flip=tie, 2× cost).
- `backend/eval/leakage_audit.py` + `scripts/audit_eval_leakage.py`: binary prompt
  + checklist leakage checks wired into the CI gate.
- `backend/council.py`: Stage 2 counterbalancing — `_collect_counterbalanced_rankings`
  rotates response order per ranker (Latin square) then `_relabel_responses_in_text`
  maps each ranker's output back to canonical labels, so aggregation/confidence/
  chairman/UI stay unchanged. Per-ranker prompts via `asyncio.gather` (Stage 2b style).
- `scripts/judge_eval_smoke.py`: `--judge holistic|binary|both`.
- `scripts/judge_binary_ab.py` (NEW): A/B harness, variants
  `holistic`/`holistic_swap`/`binary`, `--live`/`--self-test`, `--concurrency`,
  scorecard (discrimination/winner-for-good/self-consistency/position-bias/parse/cost),
  checkable label cross-check vs `answer_check`.
- `scripts/stage2_position_bias.py` (NEW): Stage 2 order-sensitivity probe.
- Tests (NEW): `test_judge_binary.py`, `test_judge_order_swap.py`,
  `test_stage2_counterbalance.py`; extended `test_leakage_audit.py`.
- `tests/fixtures/binary_metamorphic.json` (NEW): labeled good/bad corpus.
- Docs: `docs/bineval-ab-plan.md`, `docs/judge-evaluation.md`, `CLAUDE.md`.
- `output/` (gitignored): `binary_ab_truthfulqa.json` (corpus, 30 TruthfulQA pairs),
  `binary-ab-3way-live.json`, `binary-ab-truthfulqa-live.json`,
  `binary-ab-live.json`, `stage2-bias-live.json`.

## 3. Measurements (live, gpt-5.5)

### 3a. Easy crafted corpus (5 pairs, blatant good vs wrong)
Both holistic and binary = 1.0 on everything → saturated, uninformative. Cost 20/60.

### 3b. TruthfulQA A/B (real labels, adversarial misconceptions)
First run (2-way, n=30, repeats=2):
- discrimination: holistic **0.956** vs binary **0.830**
- winner-for-good: 0.944 / 0.932
- position-bias flip: 0.067 / 0.035  ← *looked* like a binary win
- self-consistency flip: 0.0 / 0.0083; parse 1.0 / 0.978; cost 90 / 266

Second run (3-way, n=30, repeats=2):
- discrimination: holistic **0.966** / holistic_swap **0.956** / binary **0.809**
- position-bias flip: **0.0 / 0.0 / 0.0**  ← the earlier 0.067/0.035 was noise; did NOT reproduce
- winner-for-good: 0.966 / 0.933 / 0.933
- self-consistency flip: 0.0 / 0.0167 / 0.0085; parse 0.978 / 1.0 / 0.989
- cost: 90 / 180 / 268

### 3c. Stage 2 position-bias probe (near-tie answers, 4 orderings, 2 cases)
Noisy/inconclusive — counterbalance did NOT reduce order-sensitivity:
- Case 1 (unit tests): fixed sens 0.25 (winners ans2,ans0,ans0,ans0) vs
  counterbalanced 0.5 (ans0,ans2,ans0,ans2)
- Case 2 (index): both 0.25, identical winners (ans1,ans1,ans0,ans1)
- Near-tie answers → winner ≈ coin flip; probe lacks power (needs slot-win-rate
  metric + many more orderings/repeats).

## 4. Provisional verdict (pre-replication)
- H1: not supported — holistic discriminates better (0.96 vs 0.81), binary 3× cost.
- H2: not supported — position bias ≈ 0 for plain holistic on TruthfulQA; the one
  positive signal was small-N noise. Order-swap buys nothing here (2× cost).
  Counterbalancing is free + unit-proven to cancel a pure slot bias, but no live
  benefit shown.

## 5. Paper fidelity — WHY the above is not a fair test
Paper: **"Ask, Don't Judge: Binary Questions..."** (BINEVAL), Cho et al., Capital
One, ICML 2026 CoLLAs workshop, arXiv:2606.27226. We followed the IDEA but broke
three load-bearing parts:
1. **Question generation:** paper uses an LLM meta-prompt to generate
   *task/instance-specific* questions (2 steps: summarize task → decompose into
   yes/no with violation examples). We used **9 static generic** questions.
2. **Reference grounding:** paper provides the **source document** and questions
   check the output against it (its killer SummEval example catches a fabricated
   URL / misattribution vs source). We ran **reference-free** open QA.
3. **Pointwise + human-correlation:** paper scores ONE output and reports
   **Spearman/Kendall correlation with human ratings** on SummEval / Topical-Chat
   / QAGS. We did **pairwise winner discrimination** on TruthfulQA.

The paper's own Limitations predict our outcome: decomposition works best for
**concrete source-grounded factual consistency** with **good generated questions**;
generic questions with no reference collapse to "the same gestalt asked N times."
Position bias is **not a BINEVAL claim** at all — that was our extrapolation.

Reported paper numbers (for reference): BINEVAL(Claude) SummEval avg ρ/τ
0.563/0.491 (best), consistency 0.655/0.615 (best), QAGS avg ρ 0.620 (best);
BINEVAL(gpt-oss) weaker (0.447/0.399) but still > G-Eval/UniEval gpt-oss on avg.

## 6. Honest replication — design (DONE; results in §8)
The faithful replication restores the three parts §5 broke:
1. **Meta-prompt question generator** `F_LLM(T; M)` (`backend/eval/bineval.py`,
   `generate_binary_questions`): two-step summarize→decompose into yes/no with a
   violation example, *task-level* (the paper generates per-task, not per-instance;
   Appendix E Tables 9–12). The paper's Table 10 bank ships as
   `PAPER_CONSISTENCY_QUESTIONS`.
2. **Source grounding** (`score_summary_decomposed`): each question answered with
   the source article in context, one isolated call per question.
3. **Pointwise** score = fraction of satisfied questions; metric = Spearman /
   Kendall / Pearson vs human, on **QAGS** (`backend/eval/qags_dataset.py`).
4. Baselines: `score_summary_holistic` (G-Eval-style CoT 1–5) and
   `score_summary_single_boolean` (one yes/no, UniEval-gpt-oss style).
5. Operator-facing/offline; new prompts in the leakage gate
   (`audit_live_bineval_prompts`, `audit_bineval_questions`); audit green.

Harness: `scripts/bineval_replication.py`. Tests: `tests/test_bineval.py`.

## 7. Quick commands
```bash
python -m pytest -q
python scripts/audit_eval_leakage.py                       # exit 0
# Faithful BINEVAL replication on QAGS (Part I, evaluation quality):
python scripts/bineval_replication.py \
  --splits cnndm,xsum --limit 60 --model anthropic/claude-sonnet-4-5 \
  --questions generate --output output/bineval-replication-sonnet.json
# Earlier out-of-envelope pairwise probe (kept for the record):
python scripts/judge_binary_ab.py --live --corpus output/binary_ab_truthfulqa.json --repeats 2
```

## 8. Faithful replication — RESULTS (QAGS, pointwise, human-correlation)
Setup: QAGS CNN/DM + XSum, **n = 60 summaries per split (120 total)**, evaluator
temperature 0. Three configurations (the only variable in the
last two is the evaluator model — both on the paper's fixed Table 10 bank).
Artifacts (original run, not included in this OSS snapshot):
`output/bineval-replication-{sonnet,sonnet-paper,gpt41mini}.json`.

Summary-level Spearman ρ vs human factual-consistency (combined, n=120):

| evaluator (bank)              | BINEVAL ρ | holistic ρ | single-bool ρ | BINEVAL calls |
|-------------------------------|:---------:|:----------:|:-------------:|:-------------:|
| Claude Sonnet 4.5 (generated) | 0.691     | **0.711**  | 0.618         | 7× holistic   |
| Claude Sonnet 4.5 (paper)     | 0.655     | **0.709**  | 0.618         | 7× holistic   |
| gpt-4.1-mini (paper)          | 0.621     | **0.735**  | 0.564         | 7× holistic   |

Paired bootstrap of Δρ = ρ(BINEVAL) − ρ(holistic), 5000 resamples, combined:

| evaluator              | Δρ      | 95% CI            | verdict                 |
|------------------------|:-------:|:-----------------:|-------------------------|
| Sonnet (generated)     | −0.020  | [−0.084, +0.040]  | tie (CI spans 0)        |
| Sonnet (paper)         | −0.054  | [−0.131, +0.024]  | tie (CI spans 0)        |
| gpt-4.1-mini (paper)   | −0.104  | [−0.177, −0.034]  | **holistic > BINEVAL**  |

Per split, the same pattern holds (holistic ≥ BINEVAL in all 6 split×model cells;
BINEVAL never leads, including on hallucination-prone XSum). `single_boolean` is
the weakest in 2/3 runs — so decomposition does beat *one* Boolean, but that
advantage never overtakes a proper holistic score. Timeouts on gpt-4.1-mini
dropped a few individual questions but **0 summaries** lost the BINEVAL variant,
so scores stay valid.

### Verdict
**H1 (BINEVAL's core evaluation-quality claim) does NOT reproduce for us, even
inside the paper's design envelope.** Holistic G-Eval-style pointwise scoring
correlates with human consistency ratings as well (strong model) or significantly
better (weak model) than 7-question decomposition, at 1/7 the cost.

### Why it differs from the paper (honest reconciliation)
The paper's headline QAGS win was driven by a **holistic baseline that collapses
on a genuinely weak/open evaluator**: G-Eval(gpt-oss-120b) scored ρ=0.132
(near-random) because gpt-oss can't produce a discriminative 1–5 holistic score,
while BINEVAL(gpt-oss) held at 0.563. Decomposition *rescues* a collapsed
holistic judge. We could not reproduce that regime: our cheapest working
model, gpt-4.1-mini, still produces an excellent holistic correlation (0.735),
so there is nothing for decomposition to rescue — and the 7× cost just adds
harshness and noise. For Claude Sonnet the paper itself reports a tie
(BINEVAL 0.620 vs G-Eval-GPT-4 0.611); we reproduce that tie.

### Implication for the council
Keep the holistic judge. Binary decomposition is **not** worth adopting for our
judge or for `ask_council`: with the capable models we actually run it is at best
a tie and at worst a regression, always at multiplied cost. It would only pay off
if we were forced to evaluate with a model too weak to score holistically — not
our situation. This corroborates, and now properly grounds, the §1–§4 no-go.
All replication code stays as an offline, flag-free measurement surface; nothing
touches the production answer path.
