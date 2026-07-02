# LLM-as-a-Judge Evaluation

`backend.eval.judge` provides an operator-facing evaluation path for comparing
a chairman synthesis with the best available Stage 1 baseline answer. It does
not run during normal `ask_council` requests.

The judge prompt uses the configured rubric from `backend.config` and requires a
strict `judge.v1` JSON object with per-criterion scores, overall scores, a
winner, confidence, and short explanations. Unparseable or failed judge calls
return an unavailable result instead of being treated as a product score.

Configuration:

- `JUDGE_MODEL` overrides the active provider's default judge model.
- `JUDGE_MODEL_OPENROUTER` sets the provider-specific default.
- `JUDGE_TEMPERATURE`, `JUDGE_TOP_P`, `JUDGE_MAX_TOKENS`, and
  `JUDGE_TIMEOUT_SECONDS` control single-pass judge calls. The default
  `JUDGE_TEMPERATURE=0.0` favors reproducibility and cost for calibration
  artifacts.
- `JUDGE_ENSEMBLE_ENABLED=false` keeps the default single-pass behavior. When
  enabled, `JUDGE_ENSEMBLE_SAMPLES` and `JUDGE_ENSEMBLE_TEMPERATURES` run an
  operator-facing Ensemble Thermo-Judge, majority-vote parseable verdicts, and
  record per-sample verdicts plus ambiguity entropy/flip-rate in the artifact.
  Failed or unparseable samples are recorded but excluded from the vote.

Provider caveats:

- The repo does not set provider seeds. Ensemble diversity relies on
  provider-side nondeterminism, so it can be weaker than papers that repeat each
  temperature with explicit distinct seeds.

Offline smoke:

```bash
python scripts/judge_eval_smoke.py --output output/judge-eval-smoke.json
```

The smoke uses a mocked provider response and writes the parsed judge artifact
so the JSON contract can be inspected without spending model calls. Pass
`--judge holistic|binary|both` to exercise the binary path; the artifact is keyed
by variant.

## Binary factuality judge (BINEVAL pilot)

A BINEVAL-style hybrid judge is available behind `JUDGE_BINARY_ENABLED` (default
`false`). When enabled, the `factuality` criterion is scored by an atomic yes/no
checklist instead of a holistic float, while `completeness`, `reasoning`, and
`clarity` stay holistic. See `docs/bineval-ab-plan.md` for the rationale and A/B
design.

Mechanics:

- Each answer is scored in its own isolated call (`build_binary_factuality_prompt`
  is single-answer), so the pairwise comparison has no position bias by
  construction. A pair therefore costs three judge calls (one holistic pass for
  the other criteria plus one binary call per answer).
- The question bank is human-owned and code-versioned in
  `backend/eval/factuality_checklist.py` (`CHECKLIST_VERSION`). Each question
  carries a polarity (a `yes` is good or a defect) and a `critical` flag; a single
  failed critical question caps the factuality score at `JUDGE_BINARY_CRITICAL_CAP`.
- `not_applicable` verdicts are excluded from the score denominator.
- The `winner` is derived deterministically from the recomputed `overall` delta
  against `JUDGE_BINARY_TIE_MARGIN`; `confidence` is computed from that margin,
  never asked of the model.
- The result keeps the `judge.v1` contract and adds `judge_variant`
  (`hybrid_binary_factuality`) plus an `experimental.binary_factuality` block with
  per-question verdicts, the checklist version, and the holistic factuality scores
  for comparison.

Config: `JUDGE_BINARY_ENABLED`, `JUDGE_BINARY_TIE_MARGIN` (default 0.05),
`JUDGE_BINARY_CRITICAL_CAP` (default 0.5).

A/B harness (pre-registered scorecard over a labeled good/bad corpus):

```bash
python scripts/judge_binary_ab.py --self-test          # offline pipeline check
python scripts/judge_binary_ab.py --live --repeats 3   # real measurement
```

The corpus (`tests/fixtures/binary_metamorphic.json`) pairs a sound answer with
one carrying a single injected defect; checkable records are cross-validated
against `backend.eval.answer_check` ground truth. The harness reports factuality
discrimination, winner-for-good rate, self-consistency and position-bias flip
rates, parse rate, and cost for both variants. Decision gates live in
`docs/bineval-ab-plan.md`.

The binary path is leak-audited like the holistic one: `audit_eval_leakage.py`
runs `audit_live_binary_judge_prompt()` and `audit_binary_checklist()` and must
stay green before its numbers are trusted.

## Order-swap judge (position-bias symmetrization)

`JUDGE_ORDER_SWAP_ENABLED` (default `false`) judges each pair in both
candidate/baseline orderings and combines the verdicts: agreement keeps the
winner, a flip resolves to a `tie`; per-criterion scores are averaged in the
canonical frame. This kills pairwise position bias at 2× cost while keeping the
holistic discrimination signal (which the binary path loses). It is ignored when
`JUDGE_BINARY_ENABLED` (the binary path already scores each answer in isolation)
and when `JUDGE_ENSEMBLE_ENABLED`. The result adds `judge_variant`
(`holistic_order_swap`) and `experimental.order_swap` (per-order winners +
`agree`).

The A/B harness measures all three variants — `holistic`, `holistic_swap`,
`binary` — so the position-bias / discrimination / cost trade-off is directly
comparable:

```bash
python scripts/judge_binary_ab.py --live --corpus output/<corpus>.json --repeats 2
```

## BINEVAL paper replication (QAGS, pointwise)

The pilot above was a *pairwise* probe; it deviated from the BINEVAL paper
(arXiv:2606.27226) on three load-bearing points (static generic questions,
reference-free, pairwise discrimination instead of pointwise human-correlation),
so its "no-go" was outside the paper's design envelope. `backend/eval/bineval.py`
is a faithful replication of the paper's evaluation-quality protocol (Part I):

- **Task-level question generation** `Q = F_LLM(T; M)` — `generate_binary_questions()`
  runs a two-step meta-prompt (summarize the task into requirements, then
  decompose each into atomic yes/no questions with a violation example). The bank
  is generated once per dimension and reused, like Appendix E Tables 9–12. The
  paper's published consistency bank (Table 10) ships as `PAPER_CONSISTENCY_QUESTIONS`.
- **Source grounding** — `score_summary_decomposed()` answers each question with
  the source article in context (`f_E(x, y, q_i) ∈ {0,1}`), one isolated call per
  question so verdicts stay independent (the precondition for the Section 5.6
  variance-reduction argument).
- **Pointwise scoring** — the summary score is the fraction of satisfied
  questions `S(x,y) = (1/N) Σ f_E`, correlated with human ratings.

Baselines: `score_summary_holistic()` is a G-Eval-style single CoT call returning
a 1–5 consistency score; `score_summary_single_boolean()` is the coarse
single-question ablation (UniEval-gpt-oss style).

The dataset is QAGS (Wang et al., 2020), loaded by `backend/eval/qags_dataset.py`
from the self-contained `mturk_cnndm.jsonl` / `mturk_xsum.jsonl` (the summary-level
human consistency label is the mean per-sentence worker yes-fraction).

```bash
python scripts/bineval_replication.py \
    --splits cnndm,xsum --limit 60 --model anthropic/claude-sonnet-4-5 \
    --questions generate --output output/bineval-replication-sonnet.json
```

The harness reports Spearman/Kendall/Pearson vs. human for each variant, per
split and combined, plus call counts. It is operator-facing and offline (it never
touches `ask_council` or the production judge). All BINEVAL prompts and the
question bank are covered by the leakage gate (`audit_live_bineval_prompts()`,
`audit_bineval_questions()`); the audit must stay green before the numbers are
trusted. Measured results live in `docs/bineval-results.md`.
