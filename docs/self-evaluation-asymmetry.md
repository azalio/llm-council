# Self-Evaluation Asymmetry Benchmark

`backend.eval.self_eval_asymmetry` + `scripts/self_eval_asymmetry.py` implement
an offline, operator-facing benchmark motivated by arXiv:2606.28050 ("Can LLMs
Judge Better Than They Generate? Evaluating Task Asymmetry, Mechanistic
Interpretability and Transferability for In-Context QA", Bandyopadhyay, Adobe
Research). It does not run during normal `ask_council` requests and is not
part of the production judge (`backend.eval.judge`) or Stage 2b's evidence-gated
revision policy — it is a measurement tool for deciding whether same-model
self-evaluation (quick mode's prompt-only self-check, Stage 2b's same-model
revision) deserves the trust those surfaces implicitly place in it.

## What it measures

For each case in a small, fixed, local corpus
(`tests/fixtures/self_eval_asymmetry.json` — short-answer, numeric, multi-hop,
and false-premise questions, each with a deterministic ground-truth check):

1. **Generation** — the model under test answers the question.
2. **Self-evaluation** — the same model, in a separate call, judges whether
   its own generated answer is correct (`yes`/`no`).
3. **C-MASK ablation** — the same self-evaluation prompt, but with the
   candidate answer replaced by a redacted placeholder. A shallow evaluator
   that still confidently answers "yes"/"no" isn't actually grounding its
   verdict in the candidate.
4. **C-SWAP ablation** — the same prompt, but with the candidate replaced by a
   plausible-but-wrong answer. A reliable evaluator should reject it; a high
   acceptance rate here indicates candidate-anchored, shallow evaluation.

Reported metrics (`compute_asymmetry_metrics()`):

- **GA** (generation accuracy) — fraction of generated answers that are
  correct per the deterministic oracle.
- **EA** (evaluation accuracy) — fraction of self-evaluation verdicts that
  agree with the oracle (treating "yes" as "predicted correct").
- **Delta = EA − GA** — the paper's central signal; negative means
  self-evaluation is *worse* than generation.
- **Evaluation precision/recall/F1** — of the self-eval "yes" verdicts, how
  many are actually correct (precision), and of the actually-correct
  generations, how many did self-eval catch (recall). This is what makes a
  rubber-stamp evaluator (always says "yes") visible: it gets recall = 1.0 but
  precision no better than GA itself — high recall, poor precision, no
  discriminative value over the base rate. See
  `test_compute_asymmetry_metrics_rubber_stamp_evaluator_is_not_reliable` in
  `tests/test_self_eval_asymmetry.py`.
- **C-MASK flip rate** / **C-SWAP rejection rate**, or an explicit
  `unavailable_reason` when no case in the run has a `wrong_plausible`
  alternative or every sample is unparseable — never silently omitted.
- Generation-failure and self-eval-unparseable counts, and total model calls
  (up to 4 per case: generation, real self-eval, C-MASK, C-SWAP).

## Usage

```bash
# Deterministic offline pipeline check — no provider access, CI-safe.
python scripts/self_eval_asymmetry.py --self-test

# Live measurement against the configured provider.
python scripts/self_eval_asymmetry.py --live \
    --model anthropic/claude-sonnet-4-6 --output output/self-eval-asymmetry.json
```

`--self-test` uses a deterministic stub model that always generates a correct
answer, confirms it under real self-evaluation, rejects the redacted (C-MASK)
candidate, and rejects the wrong-plausible (C-SWAP) candidate — a
well-behaved synthetic baseline that exercises every metric field end-to-end
without a live call, matching the corresponding `--self-test` convention in
`scripts/judge_binary_ab.py`.

## Scope and boundaries

- **Operator-facing only.** Nothing in `backend/eval/self_eval_asymmetry.py`
  is imported by `backend/council.py`, `backend/main.py`, or
  `mcp_server/server.py` — it never runs during a normal `ask_council` request
  (same boundary as `backend.eval.judge`, CLAUDE.md gotcha #14).
- **Not wired into `scripts/audit_eval_leakage.py`.** That audit targets
  comparative/pairwise judge prompts (candidate vs. baseline) where a neutral
  placeholder could leak which side is "supposed" to win. This benchmark's
  self-eval prompt only ever shows a single question + a single answer with no
  comparative framing, so there's no equivalent verdict-priming surface to
  audit — the C-MASK/C-SWAP ablations are themselves this benchmark's
  analogue of a leakage probe (do shallow cues change the verdict?).
- **Fixed local corpus, not a vendored dataset.** Eight cases, two per
  category, each with a mechanical check (`contains_ci`, `contains_any_ci`,
  `numeric_exact`, or `false_premise_flag`) — small and deterministic by
  design, per the issue's guardrail against vendoring large benchmark data.
- **A negative Delta on this corpus is a signal to act on, not a bug.** If a
  live run shows EA < GA for the models quick/deep mode actually use, that is
  evidence against trusting quick mode's prompt-only self-check or Stage 2b's
  same-model revision at face value — it does not by itself change any
  production behavior; see issue #32 (Stage 2b self-critique exclusion) for
  the kind of downstream design change this benchmark's findings could
  motivate.
