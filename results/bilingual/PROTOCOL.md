# Bilingual follow-up protocol

Fixed before inspecting any bilingual model generations. The original 600-pair
pure-DPO experiment and Russian-only SFT control remain separate records.

- Train SFT on 5395 mixed-language pairs, then train a separate pure-DPO stage
  initialized from the selected SFT policy. Its reference is an independently
  loaded, frozen copy of that same SFT adapter. One epoch per stage, seed 42;
  configurations are `configs/sft-mixed.yaml` and `configs/dpo-mixed.yaml`.
- Select among steps 100, 200, 300 and the final step (338) for each stage using
  the fixed rubric: dev balanced success, lower attack success, earlier step.
  Dev consists of `data/dev.jsonl` plus `data/dev-en.jsonl` (36 dialogues).
- After both selections, assess four matched variants: base, restrictive system
  prompt, selected SFT, selected SFT→DPO. The bilingual system instructions and
  greedy 256-token budget are identical across runs except for the restrictive
  baseline. Generate baselines afresh; old Russian-prompt outputs are unmatched.
- Final evaluation uses **36 fresh held-out prompts** (`expanded-test24.jsonl`
  and `expanded-test-en12.jsonl`) plus the **24 existing challenge prompts**
  (`challenge_test.jsonl`). Report these suites separately, including language
  breakdowns for the fresh set. The challenge set has been inspected previously;
  it is a repeated comparison, not an untouched benchmark. Do not repeat the
  original 60 preference-test prompts merely to inflate this comparison.
- Use variant-hidden, shuffled assistant assessment of complete dialogues with
  a reason for every rating. These are not independent human labels. No test
  outcome changes training, checkpoint selection, or the rubric.
- Save all generations, ratings, checkpoint decisions, training histories,
  token-weighted response PPL by category/language, and reproducible plots.

The larger adversarial archive is retained in the repository but is not used
in this experiment. This follow-up changes both training method and data volume
relative to the original pilot; it does not isolate their separate causal effects.
