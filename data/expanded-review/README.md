# Expanded-data screening

The public `datasets/homework_dpo_6000_local.zip` contains 6000 preference pairs,
including the unchanged original 600. Its own README, source cards, revisions,
license notices and detailed provenance remain inside the archive. This is
mostly rule-selected silver data, not 6000 independently verified examples.

`python prepare_expanded.py` imports the Russian subset into ignored
`data/expanded/`. The recorded recipe contains 4225 training, 235 validation and
235 test pairs. `--language all` imports both languages (5395/300/300 after the
same five training exclusions). Training remains controlled by YAML data paths.
The importer preserves frozen splits, verifies all original pairs before
applying the nine existing corrections, and checks IDs, normalized prompts,
groups and exact training overlap with the original behavior suites.

A new assistant screening sample used seed 131: up to eight new ordinary
training examples and three new homework examples per source, 33 total.
`sample.jsonl` records the IDs and qualified findings. This review checked labels
and obvious response-usefulness defects; it did not independently fact-check
all answers. The sample exposed a placeholder recipe; a follow-up placeholder
scan found two more. Two further questionable answers were conservatively
excluded. All five exclusions and reasons are in `exclusions.jsonl`, affect only
new training records, and precede training. No holdout was cleaned based on
model performance. The remaining data still contains weak or incorrect answers.

Using the pinned model tokenizer, all 6000 original pairs fit the 2048-token
limit after the experiment's system prompt is applied. Maximum train length was
1639 tokens, validation 1035 and test 1120. No pair requires truncation. Source
and category remain strongly correlated, and refusal text remains templated.

`manifest.json` records the Russian import hashes. The six source licenses are
retained in the archive; RuArena examples carry a noncommercial CC BY-NC 4.0
license. Preserve these terms and attribution when reusing the mixed data.

`data/expanded-test24.jsonl` is a seed-551, category-stratified sample of 12
homework and 12 ordinary Russian prompts from the new, non-core test records.
It was fixed before the SFT experiment. Prompt labels were screened before
training; the translated triangle wording is awkward but clearly a concrete
exercise. References remain source-provided guidance, not verified ground truth.
Report this fresh subset separately from the already inspected original 84-case
suite. Repeated results on the original suite are longitudinal comparisons,
not a new untouched test. Neither test chooses checkpoints; the fixed dev rule
remains in `results/RUBRIC.md`.
