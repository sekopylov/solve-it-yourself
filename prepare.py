"""Import the supplied 600-pair archive, retaining its frozen group splits."""
import argparse
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from common import read_jsonl, sha256, validate_splits, write_json, write_jsonl


def prepare(archive, destination):
    destination = Path(destination)
    with ZipFile(archive) as z:
        def rows(name):
            return [json.loads(x) for x in z.read(name).decode().splitlines() if x.strip()]
        metadata = {r["id"]: r for r in rows("labeled_600.jsonl")}
        pairs = rows("dpo_pairs_600.jsonl")
        originals = []
        for row in pairs:
            meta = metadata[row["id"]]
            originals.append({**row, "group_id": meta["split_group_id"],
                              "category": "homework" if meta["label"] == "educational" else "ordinary",
                              "source": {k: meta.get(k) for k in ("source_dataset", "source_revision",
                                         "source_url", "source_license_in_card", "source_answer_kind")}})
        write_jsonl(destination / "original.jsonl", originals)
        licenses = destination / "licenses"
        licenses.mkdir(parents=True, exist_ok=True)
        for name in ("MERA.README.md", "OASST1.README.md", "OASST1.LICENSE", "RuTurboAlpaca.README.md"):
            (licenses / name).write_bytes(z.read("sources/" + name))
    corrections = {r["id"]: r for r in read_jsonl(destination / "corrections.jsonl")}
    unknown = corrections.keys() - metadata.keys()
    if unknown:
        raise ValueError(f"Unknown correction IDs: {unknown}")
    splits = {s: [] for s in ("train", "validation", "test")}
    for row in originals:
        row = json.loads(json.dumps(row))
        if row["id"] in corrections:
            correction = corrections[row["id"]]
            if row["category"] != "ordinary":
                raise ValueError("Chosen-answer corrections here apply only to ordinary questions")
            row["chosen"][0]["content"] = correction["replacement"]
            row["correction"] = row["id"]
        splits[row["split"]].append(row)
    validate_splits(splits)
    report = {"archive_sha256": sha256(archive), "corrections": len(corrections), "splits": {}}
    for name, rows in splits.items():
        write_jsonl(destination / f"{name}.jsonl", rows)
        report["splits"][name] = {"rows": len(rows), "categories": dict(Counter(r["category"] for r in rows)),
            "chosen_longer_chars": sum(len(r["chosen"][0]["content"]) > len(r["rejected"][0]["content"]) for r in rows),
            "sha256": sha256(destination / f"{name}.jsonl")}
    write_json(destination / "manifest.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", nargs="?")
    p.add_argument("--out", default="data")
    args = p.parse_args()
    if args.archive:
        prepare(args.archive, args.out)
    else:
        validate_splits({s: read_jsonl(Path(args.out) / f"{s}.jsonl")
                         for s in ("train", "validation", "test")})
        print("Valid pairs; no shared IDs, normalized prompts or groups across splits.")
