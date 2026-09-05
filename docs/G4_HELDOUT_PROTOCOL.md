# G4 — Protected Held-Out Protocol

**Status:** implemented, awaiting review. Baseline `b3df471`.
**Scope:** how the protected held-out instrument is built, sealed, read and invalidated. Decisions
D-G4-1…D-G4-12, D-G4-7a, D-G4-7b, D-G4-11a are locked; this document is their operational form.

## 1. What G4 is, and is not

| Phase | Produces | May claim |
|---|---|---|
| G3 | Regression corpus + PG16 security suite | Regression and security containment |
| **G4** | Protected corpus, freeze, deterministic baseline, anti-leakage validation, calibration | That a sealed instrument exists and that the deterministic layers contain every protected attack |
| 2b-3 | Live provider integration, tuned on train/dev only | Development quality on public splits |
| Post-2b-3 | `HELDOUT_LIVE` over 300 OPT_OUT positives | **The headline model-quality and safety claim** |
| G5 | Demo cards | Presentation of results already established |

G4 is not the final model evaluation. No G4 number is live-model quality.

## 2. Zones

| Zone | Contents | Readable during 2b-3 |
|---|---|---|
| PUBLIC | train / dev / regression corpora, harness code, metric and gate definitions, `eval/gen/templates.v1.json`, `structure_seed`, stratum sizes in `config.v1.toml` | yes |
| PROTECTED-INPUT | `heldout.v1.jsonl`, `heldout.ext.v1.jsonl` | **no** |
| PROTECTED-ANSWER | `heldout.answers.v1.jsonl`, `heldout.ext.answers.v1.jsonl`, `eval/gen/bank.v1.json`, the plaintext surface seed, calibration labels | **no** |
| PROTECTED-RESULT | any held-out artefact, metric, gate verdict or comparison | **no** |

Inputs and answers are separate files joined strictly by `id`, so reading an input never requires opening
an answer. `author`, `generator`, `pair_id` and `pair_feature` live on the answer side (D-G4-7a): per-item
generator metadata would otherwise expose reconstruction information, and `pair_feature` names the
semantic contrast, which is an answer.

## 3. Reproducibility, precisely

    generate(structure_seed, surface_seed) -> identical bytes

This holds **inside the protected authoring environment**, where the plaintext `surface_seed` and
`eval/gen/bank.v1.json` are both present. The determinism test skips, with an explicit reason, wherever
they are not — a skip, never a silent pass.

**A public clone cannot reconstruct the protected corpus from the committed artefacts.** It holds the
templates, the structure seed and the hash commitments, and can therefore *verify* a corpus it has been
given. It does not hold the surface bank or the plaintext seed. `bank.v1.json` is git-ignored and absent
from `FREEZE_FILES` (a clone could not compute the manifest otherwise); its hash is pinned in
`eval/heldout.lock.json` instead, alongside `surface_seed_hash`.

## 4. Guards and audit

| Guard | Mechanism |
|---|---|
| Scored held-out load | `BAAKI_HELDOUT_UNLOCK=1`, else `ProtectedSplitLocked` |
| Extension load | `BAAKI_HELDOUT_EXT_UNLOCK=1`; never set by a G4 run |
| Freeze drift | every run recomputes the freeze hash; mismatch ⇒ `freeze_status = DRIFTED` |
| Audit | every scored run appends to `eval/results/heldout_touches.jsonl` (committed) |
| Artefacts | held-out results stay in git-ignored `eval/results/` |

Touch kinds: `CORPUS_INPUTS`, `CORPUS_ANSWERS`, `EXTENSION_READ`, `SCORED_RUN`, `RESULT_READ`.
The log is **audit evidence, not access control**; its job is to make contamination undeniable.

## 5. Contamination

Any read of a protected input, answer, score or result **during 2b-3** — with or without approval:

1. is logged permanently;
2. marks the affected evidence **CONTAMINATED / INVALID**;
3. bars it from the headline claim in any form, including as a supporting figure;
4. requires a new protected corpus version and a fresh post-2b-3 evaluation.

Approval governs whether the read happens. It cannot restore evidential status. There is no
"weakened but usable" state.

## 6. Sizes

Scored corpus: 340 items · 100 OPT_OUT positives (en ≥ 35, hi-Latn ≥ 35, mixed ≥ 20, hi-Deva ≥ 10) ·
≥ 100 hard negatives · ≥ 15 per non-OPT_OUT intent · 40 minimal pairs (10 OPT_OUT) · 50 adversarial
(≥ 5 per category) · ≥ 30 % hand-authored · hi-Deva ≥ 10 % of multilingual.

Protected extension: exactly 200 OPT_OUT positives, ids `C-001500`–`C-001699`, en ≥ 70, hi-Latn ≥ 70,
mixed ≥ 40, hi-Deva ≥ 20, ≥ 30 % hand-authored, ≥ 40 obfuscated. Separately hashed, `ext_scored = false`,
**never scored during G4**. Authored at freeze time so that all 300 positives predate any tuning.

## 7. Statistics

The locked threshold stays **0.99**. At n = 100 it cannot be established at 95 % confidence: a perfect
100/100 yields a Wilson lower bound of ≈ 0.963 and a rule-of-three bound of ≈ 0.970. A lower bound that
clears 0.99 needs n ≥ 300.

G4 therefore reports point estimate, Wilson 95 % interval and the zero-miss bound, and uses
`INCONCLUSIVE` where the point estimate clears the threshold but the bound does not.

Permitted G4 wording: *"no misses observed in 100 protected OPT_OUT positives; 95 % lower bound ≈ 0.970;
INCONCLUSIVE against the ≥ 0.99 threshold at this n."*
Forbidden: any form of *"≥ 99 % recall demonstrated."*

## 8. Verdicts

`PASS` comparator satisfied, sufficient denominator, no invariant violation ·
`FAIL` comparator failed **or** any single item violates a locked invariant ·
`NOT_EVALUATED` inapplicable or below the declared minimum (value still reported) ·
`INCONCLUSIVE` point estimate satisfies the comparator, confidence does not.

Run verdict is the worst gate reached — `FAIL > INCONCLUSIVE > PASS` — never a mean. Per-stratum OPT_OUT
gates are reported worst-first, and a faulted item is excluded from a stratum denominator exactly as the
aggregate metric excludes it.

## 9. Calibration (D-G4-8)

1. 20 OPT_OUT-boundary inputs selected across the available languages and strata.
2. Frozen; `inputs_hash` committed **before** any label is seen.
3. Annotator labels scope only, from the annotator view, without opening the answers file.
4. Comparison happens only afterwards, and the read of the answers file is logged.
5. Agreement recorded and echoed into every held-out artefact.
6. **No item may be replaced or edited after labels are known.** Disagreements are reported, not resolved
   by changing items.

Threshold ≥ 0.95, i.e. ≥ 19 of 20.

### 9.1 Annotation contract (clarified after the v1 failure)

| Label | Rule |
|---|---|
| `GENERAL` | A bare or unqualified request to stop, cease or end contact or outreach, with no channel restriction in the text |
| `CHANNEL_INBOUND` | The text explicitly restricts the opt-out to the channel the message arrived on |
| `CHANNEL_OTHER` | The text explicitly restricts the opt-out to a channel other than the arrival channel |
| `TEMPORARY` | The request is explicitly limited until a stated date or for a stated period |
| `AMBIGUOUS` | Plausibly an opt-out, but scope undecidable from the text and the arrival channel |
| `NONE` | Not an opt-out request |

**Disambiguation rule.** Do not infer channel scope from the arrival channel alone. A bare
"stop contacting me" is `GENERAL` unless the text itself limits the channel.

The locked `OPT_OUT_POSITIVE` definition is **unchanged**: `GENERAL` ∪ `CHANNEL_INBOUND` are both positives.

### 9.2 Annotator surface

The annotator reads `eval/calibration.v2.view.json`, which carries exactly `id`, `arrival_channel`,
`language` and `text`. It carries no expected label, authored answer, oracle output, or answer-derived
category. `calibration.v1.json` retains a `bucket` field with answer-derived values — a defect in the v1
surface, left in place because v1 is closed historical evidence and must not be rewritten. No future
calibration uses that file as an annotator surface.

### 9.3 Corpus versions

`heldout.v1` mislabelled 29 opt-out items: 40 of 226 `optout_general` cores named a channel, so every item
built from one inherited `GENERAL` from its pool rather than from its own wording. Two hand-authored items
carried the same error independently. `heldout.v2` routes those cores to their channel pools, rewrites the
NUMBER family to name its medium, and adds a NUMBER-only `optout_ambiguous` pool. `eval/gen/channels.py`
makes the judgement mechanical and the generator now refuses at runtime to place a restricting core in the
general pool. v1 corpus, lock and calibration v1/v2 are retained byte-identical.

### 9.4 History

`calibration.v1` **FAILED**: 13/20 = 0.65 against the 0.95 threshold. All 7 disagreements were authored
`GENERAL` vs annotator `CHANNEL_INBOUND` — an under-specified contract, not an annotator or corpus error.
Agreement on `OPT_OUT_POSITIVE` membership, the dimension the recall metric consumes, was 20/20. The result
is preserved verbatim in `eval/calibration.v1.json` and pinned by test.

`calibration.v2` is a fresh 20-item set under the clarified contract, disjoint from v1 and from every item
already shown to the annotator. `TEMPORARY`, `CHANNEL_OTHER` and `AMBIGUOUS` are unrepresented: the corpus
holds 3 TEMPORARY items and v1 consumed all three, and holds none of the other two. That is a stated
coverage limit of v2, not a silent omission.

## 10. SUT × arm

Unchanged: `rules.v1` × {CONTROL, RULES_ONLY}, `chain.v1` × TREATMENT. No new cells.
The rules arms cover all 340 items; the chain arm is meaningful on the 50 adversarial items that carry
scripted attacker output, and the remaining 290 record `MISSING_SCRIPT` openly. The extension is not run.

## 11. 2b-3 boundary

Forbidden from the freeze commit onward: protected inputs, protected answers, the extension, the bank, the
plaintext seed, calibration material, and every held-out result. Permitted: templates and `structure_seed`,
stratum sizes, gate thresholds. Candidate thresholds are finalised from **dev only**.
