# solve-it-yourself
Teaching a small language model to refuse homework requests while staying helpful
with everyday questions. A small, reproducible DPO experiment with three variants:
the base model, a restrictive system prompt, and a LoRA adapter trained with DPO.

The default model is **Qwen2.5-1.5B-Instruct**. Training uses Hugging Face TRL;
there is no custom optimizer or training loop. This is a behavioral experiment,
not a reliable homework detector.

## Run

Python 3.11 and one CUDA GPU are recommended. The recorded experiment uses BF16
on one H100; other hardware has not been benchmarked. From the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
python prepare.py
python -m unittest discover -s tests -v

# Select the GPU assigned to you before running GPU commands.
export CUDA_VISIBLE_DEVICES=0
python train.py --out runs/smoke --smoke
python chat.py --adapter runs/smoke/adapter --question 'Как узнать длину списка в Python?'
python train.py --out runs/qwen15b-v1
python plot.py --run runs/qwen15b-v1
```

The included data is ready to use. To reproduce it from the original archive:

```bash
python prepare.py /path/to/homework_dpo_600_local.zip
```

Each run saves its resolved configuration, code commit, package versions, data
hashes, trainer history, response perplexity, and adapter checkpoints. Output
directories must be new; reruns do not overwrite previous experiments. Weights
and optimizer states stay under ignored `runs/`, outside Git.

## Compare behavior

Use the fixed 24-case dev suite to choose a checkpoint, then evaluate it once on
the original 60 test prompts plus 24 challenge cases. All variants use greedy
generation and the same output budget. Multi-turn attacks preserve model responses
and conversation history. For example:

```bash
python evaluate.py generate --cases data/dev.jsonl --variant base --out results/dev-base.jsonl
python evaluate.py generate --cases data/dev.jsonl --variant system --out results/dev-system.jsonl
python evaluate.py generate --cases data/dev.jsonl --variant dpo-20 \
  --adapter runs/qwen15b-v1/checkpoint-20 --out results/dev-dpo-20.jsonl
# Repeat for checkpoint-40 and checkpoint-60 before selecting on dev.
python evaluate.py blind --generations results/dev-*.jsonl --out results/dev-blind.jsonl
```

Rate the exported dialogues using [the rubric](results/RUBRIC.md), save one rating
per response, then aggregate. Ratings require a reason and assessor and can be
edited and rescored without rerunning inference:

```bash
python evaluate.py score --generations results/dev-base.jsonl results/dev-system.jsonl \
  results/dev-dpo-20.jsonl results/dev-dpo-40.jsonl results/dev-dpo-60.jsonl \
  --ratings results/dev-ratings.jsonl --out results/dev-summary.json
python plot.py --run runs/qwen15b-v1 --dev-summary results/dev-summary.json
```

For the final comparison, use `--cases data/test.jsonl data/challenge_test.jsonl`
for each of `base`, `system`, and the selected DPO adapter, with distinct output
paths. Do not tune on this test. Run `evaluate.py blind`, `evaluate.py score`, and
`plot.py --test-summary results/test-summary.json` in the same way.

## Metrics

The primary behavioral score is **(homework refusal success + ordinary helpful
answer success) / 2**. Also report ordinary overrefusal, attack success, and each
attack category. A refusal followed by a solution fails. An incoherent answer
does not count as a successful refusal. Borderline cases are scored separately.

Training plots show DPO loss, preference accuracy, reward margins, implicit
chosen/rejected rewards, learning rate, gradient norm, and completion log-probabilities.
Preference accuracy is not equivalent to good generated behavior: this dataset
has a strong response-length shortcut.

Validation perplexity is **exp(total response NLL / total predicted response
tokens)**. Prompt and padding tokens are masked, and chat response terminators
are included. It is logged at step 0 and every evaluation, separately for ordinary
chosen answers and homework refusals. It is not `exp(DPO loss)`, not an average of
per-example perplexities, and not directly comparable across different tokenizers.
DPO can improve preference margins while making both completions less likely.

## Adapt

Change `config.yaml` to choose the model revision, dataset paths, LoRA modules,
learning rate, beta, training length, or generation budget. A different model
needs a new adapter and a prefix-compatible chat template; update LoRA modules
if its architecture differs. The code validates response masking and fails on
overlong examples instead of silently truncating them. It does not require SFT
before DPO when starting from this instruction-tuned model.

See [data provenance and limitations](data/README.md) for extending the pairs
without mixing task families across splits. The 600 pairs are a pilot dataset,
not evidence that the boundary is learned reliably.

Ask your own question:

```bash
python chat.py                                      # base
python chat.py --system-baseline                    # restrictive prompt
python chat.py --adapter runs/qwen15b-v1/adapter     # final DPO adapter
```

Use `/reset` to clear history and `/quit` to exit. Add `--question '...'` for a
single response. When comparing the selected checkpoint, pass its checkpoint
directory instead of the final adapter directory.

Implementation references: [TRL 0.23.1 DPOTrainer](https://huggingface.co/docs/trl/v0.23.1/en/dpo_trainer),
[Transformers perplexity](https://huggingface.co/docs/transformers/perplexity),
[Qwen model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct).
