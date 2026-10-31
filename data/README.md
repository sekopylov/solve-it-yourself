# Data

`train.jsonl`, `validation.jsonl`, and `test.jsonl` contain 480/60/60 preference
pairs, balanced between homework and ordinary questions. The supplied archive's
group split is preserved. Each record has conversational `prompt`, `chosen`, and
`rejected` fields, an ID, group ID, category, and source provenance.

For homework, the preferred response refuses to perform the task. For ordinary
questions, it gives a useful answer. A wrong homework solution can still be a
valid rejected response: its role is to represent the unwanted behavior.

`original.jsonl` preserves the imported responses. `corrections.jsonl` records
nine ordinary-answer corrections with reasons and sources: seven training, one
validation, and one test reference. These corrections predate model evaluation.
`prepare.py` applies them reproducibly. `manifest.json` records hashes and counts.
The separate 711-row file was not imported: its additional 231 rows contain
placeholder completions and are not ready for DPO.

## Sources and attribution

The source cards and available license text are retained in `licenses/`.
License labels below are those supplied in the source cards, not a new license
grant over all combined data. Preserve the original attribution when reusing it.

| Source | Count | Revision | Source-card license |
| --- | ---: | --- | --- |
| [MERA](https://huggingface.co/datasets/MERA-evaluation/MERA) | 230 | `15c0e5b8e27551b50aa27f5497074810c8740661` | MIT |
| [OpenAssistant / LAION OASST1](https://huggingface.co/datasets/OpenAssistant/oasst1) | 250 | `fdf72ae0827c1cda404aff25b6603abec9e3399b` | Apache 2.0 |
| [Ilya Gusev's ru_turbo_alpaca](https://huggingface.co/datasets/IlyaGusev/ru_turbo_alpaca) | 120 | `460b1f3312aa21ef774e916e532a9576f7938a0d` | CC BY 4.0 |

The supplied collection reformats source prompts and answers into preference
pairs, assigns task labels, and adds refusal responses. This project adds the
documented corrections and two assistant-authored behavior suites.

## Known limitations

- The original sample was assistant-reviewed, not exhaustively human-verified.
  Correcting nine known answers does not certify every remaining answer.
- Source and label are correlated: all MERA examples are homework, and all
  OASST1 examples are ordinary questions. Generalization to new styles is uncertain.
- There are only 12 refusal templates. Homework rejected responses often contain
  short answer keys. The chosen response is longer in 436/480 training pairs;
  pair preference accuracy is therefore vulnerable to a length shortcut.
- Exact normalized prompt and stored group overlap is checked across splits.
  This is not proof that all semantic near-duplicates have been removed.

## Behavior suites and policy

`dev.jsonl` has 24 cases: eight homework, eight ordinary, and eight attacks. It is
used to select among checkpoints. `challenge_test.jsonl` has 24 additional final
cases: six everyday calculations, six borderline cases, and twelve attacks.
The final evaluation combines the original 60 test prompts and these 24 cases.
The attack strategies deliberately recur across dev and test; these are not
claims of generalization to unseen attack strategies. No challenge examples are
used as training pairs.

The intended policy is deliberately strict: refuse concrete school-style
exercises, completed coursework, and checking/correcting a specific homework
answer, including teacher/parent claims and later work disguises. General
explanations, pedagogical advice, and everyday calculations remain allowed.
In a multi-turn dialogue, established homework context persists. Borderline
cases specify the expected behavior and rationale before generation; their
scores are reported separately from the main balanced score. This operational
policy is a project choice, not a claim to infer a user's private intent.

To extend the data, add complete pairs with provenance and stable family/group
IDs, keep all variants of the same task in one split, and run `python prepare.py`
to validate. Keep the existing test set frozen. Version any changed references
or labels and record why. Importing the original archive again replaces the
three prepared splits, so use a separate output folder for a new dataset version.
