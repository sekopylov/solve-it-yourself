"""Ask the base, system-prompt baseline, or a saved DPO adapter a question."""
import argparse

from common import config, generate, load_model


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--adapter")
    p.add_argument("--system-baseline", action="store_true")
    p.add_argument("--question", help="One question, then exit; otherwise start a conversation")
    args = p.parse_args()
    if args.adapter and args.system_baseline:
        p.error("Choose the adapter or the system baseline")
    cfg = config(args.config)
    model, tokenizer = load_model(cfg, args.adapter)
    history = [{"role": "system", "content": cfg["refusal_system"] if args.system_baseline else cfg["system"]}]
    while True:
        try:
            question = args.question or input("You (/reset or /quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question == "/quit":
            break
        if question == "/reset":
            history = history[:1]
            continue
        if not question:
            continue
        history.append({"role": "user", "content": question})
        answer = generate(model, tokenizer, [history], cfg)[0]
        print(answer)
        history.append({"role": "assistant", "content": answer})
        if args.question:
            break


if __name__ == "__main__":
    main()
