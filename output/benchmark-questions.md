# Benchmark questions — council model selection (2026-06)

Вопросы, на которых гонялся бенчмарк выбора совета (`scripts/council_model_benchmark.py`). Два набора по 12 задач: **v1** (общие) и **v2** (сложнее, менее «зазубренные», с проверяемыми ответами). Запуск: `python scripts/council_model_benchmark.py --promptset v1|v2`.

Домены: reasoning, code, math, factuality, writing, instruction_following, multilingual, abstention.

> **Правки после ревью (validation pass).** Скорректированы формулировки и эталоны,
> чтобы benchmark мерил качество, а не терпимость к кривой постановке:
> - `code_debug` (v1): функция заменена на реально багованную (`sorted(counts)`),
>   т.к. прежний вариант `for c in counts` на Python 3.7+ работает корректно
>   (dict хранит порядок вставки) — бага в целевой среде не было.
> - `factual_uncertainty` (v1): «why is it **not O(n log n)**» → «tight complexity и
>   почему O(n log n) — loose, а не tight» (O(n log n) формально верная верхняя граница).
> - `code_rate_limiter` (v2): «elapsed **wall-clock** time» → «elapsed **monotonic** time»
>   (wall-clock может прыгать из-за NTP/manual clock changes — для rate limiter некорректно).
> - Исправлен эталон `fermi_whale_heartbeats`: арифметика давала ≈0.34–0.47 млрд (сотни
>   миллионов), а не 3–5 млрд — был лишний порядок; расширен диапазон до ~0.3–1.5 млрд.
> - Расширены эталоны: `code_hidden_bug` — добавлен случай n > len(items);
>   `factual_mvcc` — xmax/MultiXact, новая версия при UPDATE, freeze/wraparound;
>   `multilingual_isolation_ru` — явно стандарт SQL + нюанс PostgreSQL (RR = snapshot).
>
> Замечание: записанные прогоны (`output/council-benchmark*.json`) выполнялись на
> ДО-ревизионном тексте `code_debug`/`factual_uncertainty`; выбор совета был устойчив
> на обоих наборах, так что правки нужны для будущих «чистых» прогонов, а не для
> пересмотра результата.

---

## Набор v1 (12 задач)

### 1. `reasoning_logic`  — _reasoning_

**Вопрос:**

Three switches outside a windowless room each control one of three incandescent bulbs inside. You may flip switches as much as you like, but may enter the room only once. Describe a procedure that tells you with certainty which switch controls which bulb, and explain why it works.

**Эталон / что считается верным:** Heat trick: turn switch 1 on for several minutes, then off; turn switch 2 on; enter. Lit = switch 2, off+warm = switch 1, off+cold = switch 3.


### 2. `reasoning_fermi`  — _reasoning_

**Вопрос:**

Estimate how many piano tuners work in Chicago today. State your assumptions explicitly, show the arithmetic, and give a final range. Flag which assumption your estimate is most sensitive to.

**Эталон / что считается верным:** Open Fermi. Plausible ~25–100 tuners for Chicago; score the method (pianos × tunings/yr ÷ tuner output) and explicit assumptions, not the exact number.


### 3. `code_implement`  — _code_

**Вопрос:**

Implement an LRU cache in Python with O(1) get and put. Provide the full class, handle the capacity-eviction edge cases, and add a short docstring. Then list two edge cases your implementation handles correctly.

**Эталон / что считается верным:** dict + doubly linked list with sentinels, O(1) get/put. Score: put on existing key must NOT grow size or evict; capacity<=0 handled; capacity==1 evicts correctly; get updates recency.


### 4. `code_debug`  — _code_

**Вопрос:**

This Python function is supposed to return the first non-repeating character in a string (in order of appearance), or None if there is none. It has a bug:

def first_unique(s):
    counts = {}
    for c in s:
        counts[c] = counts.get(c, 0) + 1
    for c in sorted(counts):
        if counts[c] == 1:
            return c
    return None

Identify the bug, explain why it fails (give a concrete input where it returns the wrong character), and give a corrected version.

**Эталон / что считается верным:** Bug: the 2nd loop iterates `sorted(counts)`, returning the alphabetically-first unique char instead of the first by appearance. first_unique('bca') returns 'a' but should be 'b' (also 'stress' → 'e', should be 't'). Fix: iterate the string — `for c in s:`. (Genuine bug on all Python versions.)


### 5. `math_exact`  — _math_

**Вопрос:**

Find all real solutions to the equation x^4 - 5x^2 + 4 = 0. Show your work and state the complete solution set.

**Эталон / что считается верным:** Solution set {-2, -1, 1, 2} (let u=x²: (u-1)(u-4)=0 → x²=1 or 4).


### 6. `math_probability`  — _math_

**Вопрос:**

A fair six-sided die is rolled four times. What is the exact probability that at least one six appears? Give the closed-form fraction and a decimal, and explain the reasoning.

**Эталон / что считается верным:** 1 − (5/6)^4 = 671/1296 ≈ 0.5177 (Chevalier de Méré). Already lowest terms (671 = 11·61, 1296 = 2⁴·3⁴).


### 7. `factual_concept`  — _factuality_

**Вопрос:**

Explain how the Raft consensus algorithm elects a leader and what guarantees it provides about log consistency. Be precise about terms, election timeouts, and the role of the commit index.

**Эталон / что считается верным:** Raft. Score: candidate increments term, votes ≤ once per term, majority required, up-to-date-log check, randomized election timeouts, log matching, commit index, current-term commit restriction (Fig. 8), leader completeness.


### 8. `factual_uncertainty`  — _factuality_

**Вопрос:**

What is the tight asymptotic time complexity of building a binary heap from an unsorted array of n elements, and why is the common O(n log n) intuition loose rather than tight? If any part of the common intuition is misleading, say so explicitly.

**Эталон / что считается верным:** Tight bound is Θ(n) (Floyd bottom-up build-heap; per-node cost ∝ height, Σ k/2^k = 2). O(n log n) is a VALID but loose upper bound — it is not the tight Θ. The misconception conflates node depth with height (most nodes are near the leaves) and/or assumes top-down repeated insertion (which really is O(n log n)).


### 9. `writing_explain`  — _writing_

**Вопрос:**

Explain what a database index is and the trade-off it introduces, to a junior backend engineer, in under 150 words. Be concrete and avoid filler.

**Эталон / что считается верным:** Open. Index = secondary structure (usually a B-tree) mapping keys to row locations for fast lookups; trade-off: faster reads, slower writes + extra storage. Under 150 words; count words carefully (watch punctuation / hyphenation).


### 10. `instruction_following`  — _instruction_following_

**Вопрос:**

Output exactly three bullet points, each starting with a different verb in the imperative mood, summarizing best practices for handling secrets in a codebase. Do not add a heading, intro, or closing line. Each bullet must be one sentence and under 20 words.

**Эталон / что считается верным:** Format test: exactly 3 bullets, distinct imperative verbs, each one sentence < 20 words, no heading/intro/closing line.


### 11. `multilingual_ru`  — _multilingual_

**Вопрос:**

Объясни на русском языке, в чём разница между процессом и потоком (thread) в операционной системе, и приведи один практический пример, когда потоки предпочтительнее процессов. Будь технически точным.

**Эталон / что считается верным:** Процесс = единица ресурсов / изолированное адресное пространство; поток = единица исполнения внутри процесса (общая память, свой стек/регистры); пример — многопоточный веб-сервер с общим кэшем и пулом соединений.


### 12. `false_premise`  — _abstention_

**Вопрос:**

Summarize the main argument of Albert Einstein's 1953 paper proving that P equals NP. If the premise of this question is flawed, say so clearly and explain what is actually true instead of inventing content.

**Эталон / что считается верным:** False premise: Einstein never proved P = NP. The P vs NP problem / NP-completeness framework was formalized in the early 1970s, especially by Cook (1971) and independently by Levin; P vs NP remains an open Millennium Prize problem. Correct response: flag the premise, don't invent content.


---

## Набор v2 (12 задач, сложнее)

### 1. `bayes_base_rate`  — _reasoning_

**Вопрос:**

A disease affects 1 in 1,000 people. A screening test is 99% sensitive (positive when the person is sick) and 95% specific (negative when the person is healthy). A randomly chosen person tests positive. What is the probability they actually have the disease? Show the calculation and explain why the answer is much lower than most people expect.

**Эталон / что считается верным:** ≈1.94%. P = (0.99·0.001)/(0.99·0.001 + 0.05·0.999) = 0.00099/0.05094 = 11/566 ≈ 0.0194 (base-rate neglect: the false positives swamp the rare true ones).


### 2. `fermi_whale_heartbeats`  — _reasoning_

**Вопрос:**

Estimate the total number of heartbeats in the lifetime of an average blue whale. State your assumptions explicitly (lifespan, typical heart rate), show the arithmetic, give a final range, and flag which assumption your estimate is most sensitive to.

**Эталон / что считается верным:** Open Fermi. With assumptions ~8–10 bpm and ~80–90 yr, the arithmetic gives ≈0.34–0.47 billion heartbeats, i.e. hundreds of millions, not 3–5 billion. A wider plausible range is ~0.3–1.5 billion depending on the assumed average heart rate. Most sensitive to the assumed average heart rate.


### 3. `code_hidden_bug`  — _code_

**Вопрос:**

This Python function is meant to split a list into `n` contiguous chunks that together contain ALL the original items:

def chunk_list(items, n):
    size = len(items) // n
    chunks = []
    for i in range(n):
        chunks.append(items[i*size:(i+1)*size])
    return chunks

It has a subtle bug. Identify it, show a concrete input where it loses data, explain the cause, and give a corrected version that keeps every item.

**Эталон / что считается верным:** Drops the remainder when len(items) % n != 0 — e.g. chunk_list(list(range(10)), 3) loses element 9. Also n > len(items) gives size==0 → n empty chunks, losing everything. Fix: a balanced split that distributes the remainder and keeps every item.


### 4. `code_rate_limiter`  — _code_

**Вопрос:**

Implement a token-bucket rate limiter in Python: a class `RateLimiter(rate_per_sec, capacity)` with a method `allow() -> bool` that refills tokens based on elapsed monotonic time and returns whether the current call is permitted. Explain the refill math and call out one edge case your implementation handles (e.g. bursts, or capping accumulated tokens).

**Эталон / что считается верным:** Token bucket using time.monotonic(): tokens = min(capacity, tokens + elapsed·rate); allow() consumes 1 if ≥1 available. Edge: cap accumulated tokens at capacity (burst limit); monotonic time avoids backward jumps caused by wall-clock adjustments such as NTP or manual clock changes.


### 5. `math_inclusion_exclusion`  — _math_

**Вопрос:**

How many integers from 1 to 1000 inclusive are divisible by at least one of 6, 10, or 15? Use inclusion-exclusion, show every term of the computation, and state the exact final count.

**Эталон / что считается верным:** 266. |6|=166, |10|=100, |15|=66; all pairwise lcm = 30 → 33 each; triple lcm = 30 → 33. 332 − 99 + 33 = 266.


### 6. `math_irrationality_proof`  — _math_

**Вопрос:**

Prove that sqrt(2) + sqrt(3) is irrational. Give a complete, rigorous proof and justify each step.

**Эталон / что считается верным:** Assume √2 + √3 = r ∈ ℚ (r > 0, so r ≠ 0). Then (r − √2)² = 3 ⇒ √2 = (r² − 1)/(2r) ∈ ℚ, contradicting √2 irrational. ∎


### 7. `factual_mvcc`  — _factuality_

**Вопрос:**

Explain how PostgreSQL's MVCC works: what a tuple's xmin and xmax mean, how a transaction's snapshot decides which row versions are visible, and exactly what problem VACUUM solves. Be precise about dead tuples and transaction-ID wraparound.

**Эталон / что считается верным:** xmin = creating txid; xmax = deleting/updating/locking txid (can be a lock or MultiXact, not only DELETE). UPDATE writes a NEW tuple version and sets xmax on the old one. A version is visible iff its xmin is committed & visible to the snapshot (not in-progress/aborted) and xmax is absent/aborted/not-visible. VACUUM reclaims dead tuples (marks space reusable, cleans heap + indexes — not a full defrag) and, by freezing old XIDs (relfrozenxid), prevents transaction-ID wraparound; anti-wraparound autovacuum forces this freeze.


### 8. `factual_float_assoc`  — _factuality_

**Вопрос:**

Is IEEE-754 double-precision floating-point addition associative? Answer yes or no, prove your answer with a concrete example using specific values, and explain the underlying cause.

**Эталон / что считается верным:** No. e.g. (1e20 + −1e20) + 1 = 1.0, but 1e20 + (−1e20 + 1) = 0.0. Cause: finite precision — the small addend is absorbed/rounded away depending on grouping.


### 9. `abstention_fake_paper`  — _abstention_

**Вопрос:**

Summarize the methodology and headline result of the 2021 DeepMind paper 'Chinchilla-2: Scaling Laws for Trillion-Parameter Retrieval Models.' If you are not certain this specific paper exists, say so plainly instead of guessing or reconstructing plausible-sounding content.

**Эталон / что считается верным:** The paper does not exist (fabricated title — the real Chinchilla, 2022, is about compute-optimal scaling, not 'Trillion-Parameter Retrieval'). Correct response: decline / flag uncertainty instead of inventing it.


### 10. `multilingual_isolation_ru`  — _multilingual_

**Вопрос:**

Объясни на русском языке, что гарантирует и чего НЕ гарантирует уровень изоляции транзакций READ COMMITTED по стандарту SQL. Приведи конкретный пример аномалии, возможной при READ COMMITTED, но невозможной при REPEATABLE READ, и отметь, если в конкретной СУБД (например, PostgreSQL) реальное поведение строже стандарта. Будь технически точным.

**Эталон / что считается верным:** По стандарту SQL READ COMMITTED запрещает грязные чтения, но допускает non-repeatable reads и фантомы; REPEATABLE READ дополнительно запрещает non-repeatable reads. Пример: повторный SELECT той же строки в одной транзакции даёт разное значение (возможно при RC, невозможно при RR). Нюанс: в PostgreSQL REPEATABLE READ = snapshot isolation и строже стандарта — не допускает и фантомы при повторном SELECT внутри снапшота.


### 11. `instruction_regex_ipv4`  — _instruction_following_

**Вопрос:**

Output a single line containing only a POSIX extended regular expression that matches a valid IPv4 octet (an integer from 0 to 255) and rejects everything else. No explanation, no code fence, no surrounding text — just the regular expression on one line.

**Эталон / что считается верным:** Octet 0–255, only the regex on one line, e.g. ^(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$ — rejects leading zeros (01, 001) because the octet is an integer 0–255.


### 12. `reasoning_bat_ball`  — _reasoning_

**Вопрос:**

A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. A glove costs three times as much as the ball. What does each of the ball, the bat, and the glove cost? Show your reasoning step by step.

**Эталон / что считается верным:** ball = $0.05, bat = $1.05, glove = $0.15 (CRT trap: the ball is $0.05, not the intuitive $0.10).

