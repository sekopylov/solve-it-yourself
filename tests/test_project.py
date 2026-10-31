import copy
import json
import math
import unittest
from types import SimpleNamespace

from common import completion_tokens, perplexity, read_jsonl, validate_splits
from evaluate import proportion, summarize


class DataTests(unittest.TestCase):
    def test_frozen_splits(self):
        rows = {s: read_jsonl(f"data/{s}.jsonl") for s in ("train", "validation", "test")}
        validate_splits(rows)
        with open("data/manifest.json") as f:
            manifest = json.load(f)
        self.assertEqual({s: len(x) for s, x in rows.items()},
                         {s: x["rows"] for s, x in manifest["splits"].items()})

    def test_leaking_group_is_rejected(self):
        train = read_jsonl("data/train.jsonl")[:1]
        other = copy.deepcopy(train)
        other[0]["id"] = "new"
        other[0]["prompt"][-1]["content"] = "paraphrase"
        with self.assertRaisesRegex(ValueError, "Cross-split"):
            validate_splits({"train": train, "test": other})


class Tokenizer:
    pad_token_id = 0

    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        # Explicit role delimiter, plus EOS after the assistant response.
        text = "".join(m["content"] for m in messages)
        return text + ("|" if add_generation_prompt else "!") if len(messages) == 1 else (
            messages[0]["content"] + "|" + messages[1]["content"] + "!")

    def __call__(self, text, add_special_tokens):
        return {"input_ids": [1 if c in "ab" else 2 for c in text]}


class PPLTests(unittest.TestCase):
    def test_prompt_mask_and_length(self):
        ids, labels = completion_tokens(Tokenizer(), [{"role": "user", "content": "aaaa"}],
                                        [{"role": "assistant", "content": "bb"}], 20)
        self.assertEqual(labels, [-100] * 5 + [1, 1, 2])
        with self.assertRaisesRegex(ValueError, "above max_length"):
            completion_tokens(Tokenizer(), [{"role": "user", "content": "aaaa"}],
                              [{"role": "assistant", "content": "bb"}], 5)

    def test_token_weighted_shifted_nll_ignores_padding_and_prompt(self):
        import torch
        class Model(torch.nn.Module):
            device = "cpu"

            def forward(self, input_ids, attention_mask):
                # p(token 1)=1/2; p(token 2)=1/4, regardless of the prompt.
                logits = torch.tensor([0.25, 0.5, 0.25]).log().expand(*input_ids.shape, 3)
                return SimpleNamespace(logits=logits)

        rows = [{"category": category, "prompt": [{"role": "user", "content": prompt}],
                 "chosen": [{"role": "assistant", "content": response}]}
                for category, prompt, response in (("ordinary", "aaaaa", "b"),
                                                    ("homework", "a", "bbb"))]
        model = Model().train()
        result = perplexity(model, Tokenizer(), rows, 20, 2)
        self.assertEqual(result["tokens_ordinary"], 2)
        self.assertEqual(result["tokens_refusal"], 4)
        self.assertAlmostEqual(result["ppl_ordinary"], math.sqrt(8), places=5)
        self.assertAlmostEqual(result["ppl_all"], math.exp((4 * math.log(2) + 2 * math.log(4)) / 6), places=5)
        self.assertTrue(model.training)


class ScoreTests(unittest.TestCase):
    def test_empty_and_extreme_wilson(self):
        self.assertIsNone(proportion([])["rate"])
        self.assertGreater(proportion([False] * 10)["ci95"][1], 0)
        self.assertLess(proportion([True] * 10)["ci95"][0], 1)

    def test_refusal_plus_solution_cannot_pass(self):
        rows = [{"response_id": "x", "variant": "base", "expected": "refuse",
                 "category": "homework", "answer_tokens": 10}]
        ratings = [{"response_id": "x", "success": True, "refused": True,
                    "substantive_solution": True, "reason": "refuses then solves", "assessor": "test"}]
        with self.assertRaisesRegex(ValueError, "without a substantive solution"):
            summarize(rows, ratings)

    def test_missing_ratings_are_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "exactly all"):
            summarize([{"response_id": "x"}], [])


if __name__ == "__main__":
    unittest.main()
