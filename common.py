"""Small shared helpers. All paths are relative to the repository root."""
import hashlib
import json
import math
from pathlib import Path


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def config(path):
    import yaml
    return yaml.safe_load(Path(path).read_text())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_splits(splits):
    """Fail on malformed pairs and cross-split groups/prompts; never silently drop rows."""
    seen_ids, groups, prompts = set(), {}, {}
    for split, rows in splits.items():
        if not rows:
            raise ValueError(f"Empty split: {split}")
        for row in rows:
            if row["id"] in seen_ids:
                raise ValueError(f"Duplicate id: {row['id']}")
            seen_ids.add(row["id"])
            if row["category"] not in {"homework", "ordinary"}:
                raise ValueError(f"Invalid category: {row['id']}")
            for key in ("prompt", "chosen", "rejected"):
                if not row[key] or any(not m["content"].strip() for m in row[key]):
                    raise ValueError(f"Empty {key}: {row['id']}")
            if row["prompt"][-1]["role"] != "user":
                raise ValueError(f"Prompt must end with a user turn: {row['id']}")
            for key in ("chosen", "rejected"):
                if len(row[key]) != 1 or row[key][0]["role"] != "assistant":
                    raise ValueError(f"Expected one assistant completion: {row['id']}")
            if row["chosen"] == row["rejected"]:
                raise ValueError(f"Identical completions: {row['id']}")
            normalized = " ".join(row["prompt"][-1]["content"].casefold().split())
            for index, key in ((groups, row["group_id"]), (prompts, normalized)):
                if key in index and index[key] != split:
                    raise ValueError(f"Cross-split overlap: {row['id']}")
                index[key] = split


def completion_tokens(tokenizer, prompt, completion, max_length):
    """Mask the prompt, retaining only completion tokens (including chat EOS)."""
    prefix = tokenizer.apply_chat_template(prompt, tokenize=False, add_generation_prompt=True)
    full = tokenizer.apply_chat_template(prompt + completion, tokenize=False,
                                         add_generation_prompt=False)
    prefix_ids = tokenizer(prefix, add_special_tokens=False)["input_ids"]
    ids = tokenizer(full, add_special_tokens=False)["input_ids"]
    if not full.startswith(prefix) or ids[:len(prefix_ids)] != prefix_ids:
        raise ValueError("Chat template is not prefix-compatible; adapt masking for this model")
    if len(ids) > max_length:
        raise ValueError(f"Sequence has {len(ids)} tokens, above max_length={max_length}; "
                         "increase the limit or explicitly curate the example")
    labels = [-100] * len(prefix_ids) + ids[len(prefix_ids):]
    if not any(x != -100 for x in labels[1:]):
        raise ValueError("No predicted completion tokens")
    return ids, labels


def perplexity(model, tokenizer, rows, max_length, batch_size=2):
    """Token-weighted conditional PPL; prompt and padding never enter the NLL."""
    import torch
    import torch.nn.functional as F
    totals = {key: [0.0, 0] for key in ("ordinary", "homework", "all")}
    was_training = model.training
    model.eval()
    try:
        with torch.inference_mode():
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset:offset + batch_size]
                encoded = [completion_tokens(tokenizer, r["prompt"], r["chosen"], max_length)
                           for r in batch]
                width = max(len(ids) for ids, _ in encoded)
                ids = torch.tensor([x + [tokenizer.pad_token_id] * (width - len(x))
                                    for x, _ in encoded], device=model.device)
                labels = torch.tensor([y + [-100] * (width - len(y)) for _, y in encoded],
                                      device=model.device)
                attention = torch.tensor([[1] * len(x) + [0] * (width - len(x))
                                          for x, _ in encoded], device=model.device)
                logits = model(input_ids=ids, attention_mask=attention).logits[:, :-1].float()
                shifted = labels[:, 1:]
                losses = F.cross_entropy(logits.transpose(1, 2), shifted,
                                         ignore_index=-100, reduction="none")
                for row, loss, valid in zip(batch, losses, shifted != -100):
                    nll, count = loss.sum().item(), valid.sum().item()
                    for key in (row["category"], "all"):
                        totals[key][0] += nll
                        totals[key][1] += count
    finally:
        model.train(was_training)
    result = {}
    for key, (nll, count) in totals.items():
        if count:
            name = "refusal" if key == "homework" else key
            result.update({f"ppl_{name}": math.exp(nll / count),
                           f"nll_{name}": nll / count, f"tokens_{name}": count})
    return result


def load_model(cfg, adapter=None):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    args = dict(revision=cfg["model"]["revision"])
    tokenizer = AutoTokenizer.from_pretrained(cfg["model"]["name"], **args)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        cfg["model"]["name"], torch_dtype=getattr(torch, cfg["model"]["dtype"]),
        attn_implementation="sdpa", **args).to("cuda")
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
    return model, tokenizer


def generate(model, tokenizer, conversations, cfg):
    import torch
    tokenizer.padding_side = "left"
    texts = [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
             for m in conversations]
    inputs = tokenizer(texts, return_tensors="pt", padding=True,
                       add_special_tokens=False).to(model.device)
    model.eval()
    with torch.inference_mode():
        output = model.generate(**inputs, do_sample=False,
                                max_new_tokens=cfg["generation"]["max_new_tokens"],
                                pad_token_id=tokenizer.pad_token_id)
    return tokenizer.batch_decode(output[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)
