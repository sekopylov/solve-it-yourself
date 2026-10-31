"""DPO with TRL and LoRA; no custom training loop."""
import argparse
import importlib.metadata
import json
import subprocess
from pathlib import Path

from common import (completion_tokens, config, load_model, perplexity, read_jsonl,
                    sha256, validate_splits, write_json, write_jsonl)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--out", required=True)
    p.add_argument("--smoke", action="store_true", help="Two optimizer steps on 16 training rows")
    args = p.parse_args()
    cfg = config(args.config)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"Use a fresh output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    from datasets import Dataset
    from peft import LoraConfig
    from transformers import TrainerCallback, set_seed
    from trl import DPOConfig, DPOTrainer

    settings = cfg["training"]
    set_seed(settings["seed"])
    splits = {s: read_jsonl(path) for s, path in cfg["data"].items()}
    validate_splits(splits)
    for rows in splits.values():
        for row in rows:
            row["prompt"] = [{"role": "system", "content": cfg["system"]}] + [
                m for m in row["prompt"] if m["role"] != "system"]
    train, validation = splits["train"], splits["validation"]
    if args.smoke:
        train = train[:8] + train[-8:]
        validation = validation[:2] + validation[-2:]
    model, tokenizer = load_model(cfg)
    model.config.use_cache = False
    # Fail before optimization if TRL would truncate any training/validation completion.
    lengths = []
    for row in train + validation:
        for key in ("chosen", "rejected"):
            ids, _ = completion_tokens(tokenizer, row["prompt"], row[key], settings["max_length"])
            lengths.append(len(ids))
    metadata = {"config": cfg, "smoke": args.smoke, "max_sequence_tokens": max(lengths),
                "data_sha256": {s: sha256(path) for s, path in cfg["data"].items()},
                "packages": {name: importlib.metadata.version(name) for name in
                             ("torch", "transformers", "trl", "peft", "datasets", "accelerate")},
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True))}
    write_json(out / "run.json", metadata)

    class PPLCallback(TrainerCallback):
        def measure(self, model, step):
            result = {"step": step, **perplexity(model, tokenizer, validation,
                      settings["max_length"], settings["eval_batch_size"])}
            with (out / "perplexity.jsonl").open("a") as f:
                f.write(json.dumps(result) + "\n")
            print("PPL", json.dumps(result), flush=True)

        def on_evaluate(self, args, state, control, model=None, **kwargs):
            self.measure(model, state.global_step)

    callback = PPLCallback()
    def dataset(rows):
        return Dataset.from_list([{k: row[k] for k in ("prompt", "chosen", "rejected")} for row in rows])
    trainer = DPOTrainer(
        model=model, processing_class=tokenizer,
        train_dataset=dataset(train), eval_dataset=dataset(validation),
        peft_config=LoraConfig(task_type="CAUSAL_LM", bias="none", **cfg["lora"]),
        args=DPOConfig(
            output_dir=str(out), seed=settings["seed"], data_seed=settings["seed"],
            learning_rate=settings["learning_rate"], beta=settings["beta"],
            num_train_epochs=settings["epochs"], max_steps=2 if args.smoke else -1,
            per_device_train_batch_size=settings["batch_size"],
            gradient_accumulation_steps=settings["gradient_accumulation_steps"],
            per_device_eval_batch_size=settings["eval_batch_size"],
            max_length=settings["max_length"], max_prompt_length=None,
            bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            warmup_ratio=settings["warmup_ratio"], lr_scheduler_type="cosine",
            eval_strategy="steps", eval_steps=1 if args.smoke else settings["eval_steps"],
            save_strategy="steps", save_steps=1 if args.smoke else settings["eval_steps"],
            logging_steps=1, report_to="none", save_total_limit=None,
            remove_unused_columns=False, disable_dropout=True,
        ), callbacks=[callback])
    # Default PEFT reference is the frozen base with the adapter disabled.
    trainer.evaluate()  # step 0 DPO metrics and response-only PPL
    trainer.train()
    if trainer.state.global_step % (1 if args.smoke else settings["eval_steps"]):
        trainer.evaluate()
    trainer.save_model(str(out / "adapter"))
    tokenizer.save_pretrained(out / "adapter")
    trainer.save_state()
    write_jsonl(out / "history.jsonl", trainer.state.log_history)
    print(f"Saved adapter and metrics to {out}")


if __name__ == "__main__":
    main()
