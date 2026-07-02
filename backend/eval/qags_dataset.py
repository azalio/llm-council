"""QAGS factual-consistency benchmark loader (Wang et al., 2020).

QAGS pairs each machine summary with its source article and human consistency
annotations. Each summary is split into sentences; three crowd workers mark
every sentence as factually consistent with the source ("yes") or not ("no").
The summary-level human consistency score is the mean over sentences of the
worker yes-fraction -- the standard QAGS target the BINEVAL paper correlates
against (Table 8).

The released files are self-contained (the article text is inline), so no
CNN/DM or XSum pairing is needed. Data:
``https://github.com/W4ngatang/qags`` -> ``data/mturk_cnndm.jsonl`` and
``data/mturk_xsum.jsonl``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

QAGS_SPLITS = ("cnndm", "xsum")
QAGS_RAW_URL = "https://raw.githubusercontent.com/W4ngatang/qags/master/data/mturk_{split}.jsonl"


@dataclass(frozen=True)
class QagsSummary:
    """One QAGS summary with its source, joined text, and human consistency label."""

    split: str
    index: int
    source: str
    summary: str
    human_score: float
    n_sentences: int
    n_workers: int


def _sentence_yes_fraction(responses: list[dict]) -> float:
    if not responses:
        return 0.0
    yes = sum(1 for r in responses if str(r.get("response", "")).strip().lower() == "yes")
    return yes / len(responses)


def summary_human_consistency(record: dict) -> float:
    """Mean over sentences of the worker yes-fraction (QAGS summary-level score)."""
    sentences = record.get("summary_sentences") or []
    if not sentences:
        return 0.0
    fractions = [_sentence_yes_fraction(s.get("responses") or []) for s in sentences]
    return sum(fractions) / len(fractions)


def default_qags_path(split: str, *, root: str | Path = "output/qags") -> Path:
    return Path(root) / f"mturk_{split}.jsonl"


def load_qags(path: str | Path, *, split: str) -> list[QagsSummary]:
    """Parse a QAGS mturk jsonl file into summary records."""
    if split not in QAGS_SPLITS:
        raise ValueError(f"split must be one of {QAGS_SPLITS}, got {split!r}")
    records: list[QagsSummary] = []
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            sentences = record.get("summary_sentences") or []
            summary_text = " ".join(
                str(s.get("sentence", "")).strip() for s in sentences
            ).strip()
            n_workers = max(
                (len(s.get("responses") or []) for s in sentences),
                default=0,
            )
            records.append(
                QagsSummary(
                    split=split,
                    index=index,
                    source=str(record.get("article", "")).strip(),
                    summary=summary_text,
                    human_score=summary_human_consistency(record),
                    n_sentences=len(sentences),
                    n_workers=n_workers,
                )
            )
    return records
