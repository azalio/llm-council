# Answer Cache Metrics

The answer cache is active only for context-free first-turn requests using the
default `mode="auto"` policy. It is intentionally skipped for follow-ups,
clarification-gated requests, explicit modes, `thorough=True`, and
`bypass_cache=True`.

## Runtime KPIs

`GET /api/metrics/council` and the MCP `get_council_metrics()` tool include an
`answer_cache` section:

- `totals.lookups`: eligible first-turn cache lookups.
- `totals.hits`: lookups served from stored council-backed answers.
- `totals.misses`: eligible lookups that continued to a fresh council run.
- `totals.bypasses`: requests that explicitly set `bypass_cache=true`.
- `totals.validation_attempts`: borderline semantic candidates sent to the chairman applicability check.
- `totals.validation_approved` and `totals.validation_rejected`: validation outcomes.
- `rates.hit_rate`: share of eligible lookups served from cache.
- `rates.validation_approval_rate`: share of borderline validations accepted.
- `match_types`: hit counts by `token`, `semantic`, and `validated_semantic`.
- `latency_ms.lookup`: all eligible cache lookup latency, including misses and validation rejections.
- `latency_ms.hit`: cache-hit latency, separate from full council stage latency.
- `similarity`: rolling similarity samples for accepted candidates and validation candidates.

## Replay Probe

Run an offline replay over stored first-turn conversations without model calls:

```bash
python scripts/answer_cache_replay.py --limit 200 --samples 10
```

The replay walks the inspected window of stored answers chronologically and asks
whether each later question would have matched an earlier cache source under the
current local policy. `validated_semantic` samples are reported separately as
validation candidates because production must run the chairman validation step
before serving them; the replay does not call the chairman.

## Threshold Decisions

Raise cache thresholds when manual-review samples show mismatched questions,
validation rejection rate is high, or semantic hits cluster near the acceptance
boundary. Lower thresholds only after replay samples show safe near-misses and
runtime validation approval stays high.

Do not use hit rate alone as proof of quality. A higher hit rate is useful only
when the manual-review samples still answer the new question and users retain
the visible `bypass_cache=true` escape hatch for a fresh council run.
