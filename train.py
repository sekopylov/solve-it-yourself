"""SFT or DPO with TRL and LoRA; no custom training loop."""
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
    p.add_argument("--init-adapter", help="Initialize DPO policy and frozen reference from this SFT adapter")
    args = p.parse_args()
    cfg = config(args.config)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"Use a fresh output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    from datasets import Dataset
    import torch
    from peft import LoraConfig, PeftModel
    from transformers import TrainerCallback, set_seed
    from trl import DPOConfig, DPOTrainer, SFTConfig, SFTTrainer

    settings = cfg["training"]
    method = cfg.get("method", "dpo")
    if method not in ("sft", "dpo") or (args.init_adapter and method != "dpo"):
        raise ValueError("Use method sft or dpo; --init-adapter is for DPO after SFT")
    set_seed(settings["seed"])
    splits = {s: read_jsonl(path) for s, path in cfg["data"].items()}
    validate_splits(splits)
    for rows in splits.values():
        for row in rows:
            row["prompt"] = [{"role": "system", "content": cfg["system"]}] + [
                m for m in row["prompt"] if m["role"] != "system"]
    train, validation = splits["train"], splits["validation"]
    if args.smoke:
        train = [r for category in ("homework", "ordinary")
                 for r in [x for x in train if x["category"] == category][:8]]
        validation = [r for category in ("homework", "ordinary")
                      for r in [x for x in validation if x["category"] == category][:2]]
    model, tokenizer = load_model(cfg)
    lora = LoraConfig(task_type="CAUSAL_LM", bias="none", **cfg["lora"])
    reference_parameters = {}
    if args.init_adapter:
        model = PeftModel.from_pretrained(model, args.init_adapter, is_trainable=True)
        model.load_adapter(args.init_adapter, adapter_name="reference", is_trainable=False)
        model.set_adapter("default")
        reference_parameters = {n: p.detach().cpu().clone() for n, p in model.named_parameters()
                                if ".reference." in n}
        if not reference_parameters:
            raise ValueError("Missing reference adapter parameters")
        lora = None
    model.config.use_cache = False
    # Fail before optimization if TRL would truncate any training/validation completion.
    lengths = []
    for row in train + validation:
        for key in ("chosen", "rejected"):
            ids, _ = completion_tokens(tokenizer, row["prompt"], row[key], settings["max_length"])
            lengths.append(len(ids))
    metadata = {"config": cfg, "smoke": args.smoke, "max_sequence_tokens": max(lengths),
                "method": method, "initial_adapter": args.init_adapter,
                "initial_adapter_sha256": sha256(Path(args.init_adapter) / "adapter_model.safetensors")
                                          if args.init_adapter else None,
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
        if method == "sft":
            # Use the same response mask as PPL; TRL's conversational default also
            # trains the assistant role header in this pinned version.
            encoded = [completion_tokens(tokenizer, r["prompt"], r["chosen"], settings["max_length"])
                       for r in rows]
            return Dataset.from_list([{"input_ids": ids, "completion_mask": [int(y != -100) for y in labels]}
                                      for ids, labels in encoded])
        return Dataset.from_list([{k: row[k] for k in ("prompt", "chosen", "rejected")} for row in rows])
    training_args = dict(
            output_dir=str(out), seed=settings["seed"], data_seed=settings["seed"],
            learning_rate=settings["learning_rate"],
            num_train_epochs=settings["epochs"], max_steps=2 if args.smoke else -1,
            per_device_train_batch_size=settings["batch_size"],
            gradient_accumulation_steps=settings["gradient_accumulation_steps"],
            per_device_eval_batch_size=settings["eval_batch_size"],
            max_length=settings["max_length"],
            bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            warmup_ratio=settings["warmup_ratio"], lr_scheduler_type="cosine",
            eval_strategy="steps", eval_steps=1 if args.smoke else settings["eval_steps"],
            save_strategy="steps", save_steps=1 if args.smoke else settings["eval_steps"],
            logging_steps=1, report_to="none", save_total_limit=None,
            remove_unused_columns=False,
        )
    if method == "sft":
        trainer_class = SFTTrainer
        training_args = SFTConfig(**training_args, completion_only_loss=True, packing=False,
                                  eos_token=tokenizer.eos_token)
    else:
        trainer_class = DPOTrainer
        training_args = DPOConfig(**training_args, beta=settings["beta"], max_prompt_length=None,
            disable_dropout=True, model_adapter_name="default" if args.init_adapter else None,
            ref_adapter_name="reference" if args.init_adapter else None)
    trainer = trainer_class(model=model, processing_class=tokenizer, peft_config=lora,
        train_dataset=dataset(train), eval_dataset=dataset(validation),
        args=training_args, callbacks=[callback])
    if method == "sft":
        batch = trainer.data_collator([trainer.train_dataset[0]])
        _, expected = completion_tokens(tokenizer, train[0]["prompt"], train[0]["chosen"], settings["max_length"])
        if batch["labels"][0].tolist() != expected:
            raise ValueError("SFT completion masking differs from the validated response-only mask")
    # Fresh DPO uses the frozen base; warm-start DPO uses the frozen SFT adapter.
    trainer.evaluate()  # step 0 trainer metrics and response-only PPL
    initial_history = list(trainer.state.log_history)
    trainer.train()
    if trainer.state.global_step % (1 if args.smoke else settings["eval_steps"]):
        trainer.evaluate()
    trainer.save_model(str(out / "adapter"))
    tokenizer.save_pretrained(out / "adapter")
    trainer.save_state()
    if reference_parameters:
        parameters = dict(model.named_parameters())
        unchanged = all(torch.equal(value, parameters[name].detach().cpu())
                        for name, value in reference_parameters.items())
        write_json(out / "reference_check.json", {"unchanged": unchanged,
                   "parameter_tensors": len(reference_parameters),
                   "initial_adapter_sha256": metadata["initial_adapter_sha256"]})
        if not unchanged:
            raise ValueError("Frozen SFT reference changed during DPO")
    write_jsonl(out / "history.jsonl", initial_history + trainer.state.log_history)
    print(f"Saved adapter and metrics to {out}")


if __name__ == "__main__":
    main()
