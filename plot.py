"""Plot saved metrics; training is never required to redraw a figure."""
import argparse
import json
from pathlib import Path

from common import config, read_jsonl


def save(fig, path):
    for extension in ("png", "svg"):
        destination = path.with_suffix("." + extension)
        options = {"metadata": {"Date": None}} if extension == "svg" else {}
        fig.savefig(destination, dpi=180, bbox_inches="tight", **options)
        if extension == "svg":
            destination.write_text("\n".join(line.rstrip() for line in destination.read_text().splitlines()) + "\n")


def training(run, out):
    import matplotlib.pyplot as plt
    history = read_jsonl(run / "history.jsonl")
    ppl = read_jsonl(run / "perplexity.jsonl")
    meta = json.loads((run / "run.json").read_text())
    cfg = meta["config"]
    method = meta.get("method", "dpo")
    columns = 3 if method == "sft" else 4
    fig, axes = plt.subplots(2, columns, figsize=(3.5 * columns, 7), layout="constrained")
    beta = f"beta={cfg['training']['beta']} · " if method == "dpo" else ""
    fig.suptitle(f"{method.upper()} training · {cfg['model']['name']}\n"
                 f"LoRA r={cfg['lora']['r']} · lr={cfg['training']['learning_rate']:g} · "
                 f"{beta}seed={cfg['training']['seed']}\n"
                 f"Source: {run}/history.jsonl + perplexity.jsonl (response-only NLL)", fontsize=12)

    def line(ax, rows, key, label):
        points = [(r["step"], r[key]) for r in rows if key in r]
        if points:
            ax.plot(*zip(*points), label=label, marker="o" if len(points) < 10 else None,
                    markersize=4, linewidth=1.5)

    panels = [
        ("DPO loss", [("loss", "train"), ("eval_loss", "validation")]),
        ("Preference accuracy", [("rewards/accuracies", "train"), ("eval_rewards/accuracies", "validation")]),
        ("Implicit reward margin", [("rewards/margins", "train"), ("eval_rewards/margins", "validation")]),
        ("Validation implicit rewards", [("eval_rewards/chosen", "chosen"), ("eval_rewards/rejected", "rejected")]),
        ("Response perplexity (validation)", []),
        ("Learning rate", [("learning_rate", "lr")]),
        ("Gradient norm", [("grad_norm", "norm")]),
        ("Validation completion log-probability", [("eval_logps/chosen", "chosen"), ("eval_logps/rejected", "rejected")]),
    ]
    if method == "sft":
        panels = [
            ("Completion NLL", [("loss", "train"), ("eval_loss", "validation")]),
            ("Completion token accuracy", [("mean_token_accuracy", "train"), ("eval_mean_token_accuracy", "validation")]),
            ("Completion entropy", [("entropy", "train"), ("eval_entropy", "validation")]),
            panels[4], panels[5], panels[6],
        ]
    for ax, (title, series) in zip(axes.flat, panels):
        ax.set_title(title, fontsize=10)
        for key, label in series:
            line(ax, history, key, label)
        if title.startswith("Response perplexity"):
            line(ax, ppl, "ppl_ordinary", "ordinary answers")
            line(ax, ppl, "ppl_refusal", "homework refusals")
        ax.set_xlabel("Optimizer step")
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8)
    save(fig, out / "training")
    plt.close(fig)


def behavior(path, out, name, cfg):
    import matplotlib.pyplot as plt
    metrics = json.loads(path.read_text())
    variants = list(metrics)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5), layout="constrained")
    fig.suptitle(f"{name.capitalize()} · {cfg['model']['name']} · greedy, max {cfg['generation']['max_new_tokens']} tokens\n"
                 f"Source: {path} · assistant ratings (not independent human review)\n"
                 "Bars: observed proportions; whiskers: descriptive Wilson 95% intervals", fontsize=11)
    specs = [("Homework refusal", "homework"), ("Ordinary helpful answers", "ordinary"),
             ("Attack success ↓", "attack_success")]
    colors = ["#607D8B", "#D88825", "#387AB4", "#31947A", "#9562A8"]
    for ax, (title, key) in zip(axes, specs):
        for i, variant in enumerate(variants):
            m = metrics[variant]
            metric = m[key] if key == "attack_success" else m["categories"].get(key)
            if not metric or not metric["n"]:
                continue
            value = metric["rate"]
            lo, hi = metric["ci95"]
            ax.bar(i, value, color=colors[i % len(colors)], width=.65)
            ax.errorbar(i, value, yerr=[[max(0, value-lo)], [max(0, hi-value)]],
                        color="#222222", capsize=3, fmt="none")
            ax.text(i, 1.06, f"{metric['successes']}/{metric['n']}", ha="center", fontsize=9)
        ax.set_title(title)
        ax.set_xticks(range(len(variants)), variants, rotation=20, ha="right")
        ax.set_ylim(0, 1.16)
        ax.set_yticks([0, .25, .5, .75, 1], ["0%", "25%", "50%", "75%", "100%"])
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
    save(fig, out / name)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
    fig.suptitle(f"{name.capitalize()} · {cfg['model']['name']} · answer length\n"
                 f"Source: {path} · decoded answers re-tokenized without special tokens", fontsize=10)
    for offset, category, color in ((-.18, "homework", "#387AB4"), (.18, "ordinary", "#D88825")):
        ax.bar([i + offset for i in range(len(variants))],
               [metrics[v]["mean_answer_tokens"].get(category, 0) for v in variants],
               width=.35, color=color, label=category)
    ax.set_xticks(range(len(variants)), variants, rotation=20, ha="right")
    ax.set_ylabel("Mean tokens per response")
    ax.grid(axis="y", alpha=.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend()
    save(fig, out / f"{name}-length")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml", help="Configuration used for behavior generation")
    p.add_argument("--run", type=Path)
    p.add_argument("--dev-summary", type=Path)
    p.add_argument("--test-summary", type=Path)
    p.add_argument("--out", type=Path, default=Path("results/figures"))
    args = p.parse_args()
    if not any((args.run, args.dev_summary, args.test_summary)):
        p.error("Provide at least one metrics source")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "svg.fonttype": "none", "svg.hashsalt": "solve-it-yourself"})
    args.out.mkdir(parents=True, exist_ok=True)
    if args.run:
        training(args.run, args.out)
    for name in ("dev", "test"):
        path = getattr(args, f"{name}_summary")
        if path:
            behavior(path, args.out, name, config(args.config))


if __name__ == "__main__":
    main()
