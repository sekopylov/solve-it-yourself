"""Import the public 6000-pair archive, preserving holdouts and documented corrections."""
import argparse
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from common import read_jsonl, sha256, validate_splits, write_json, write_jsonl


def prepare(archive, destination, language="ru"):
    with ZipFile(archive) as z:
        def rows(name):
            return [json.loads(x) for x in z.read('expanded_v4/' + name).decode().splitlines() if x.strip()]
        metadata = {r['id']: r for r in rows('labeled_6000.jsonl')}
        pairs = rows('dpo_pairs_6000.jsonl')
    original = {r['id']: r for r in read_jsonl('data/original.jsonl')}
    corrections = {r['id']: r for r in read_jsonl('data/corrections.jsonl')}
    exclusions = {r['id']: r for r in read_jsonl('data/expanded-review/exclusions.jsonl')}
    if not exclusions.keys() <= metadata.keys():
        raise ValueError('Unknown exclusion IDs')
    splits = {s: [] for s in ('train', 'validation', 'test')}
    core_ids = set()
    for row in pairs:
        meta = metadata[row['id']]
        if meta['core600']:
            core_ids.add(row['id'])
            for key in ('prompt', 'chosen', 'rejected', 'split'):
                if row[key] != original[row['id']][key]:
                    raise ValueError(f'Original core changed: {row["id"]} / {key}')
        if row['id'] in exclusions:
            if row['split'] != 'train' or meta['core600']:
                raise ValueError('Review exclusions must not change frozen core or holdouts')
            continue
        if language != "all" and meta['language'] != language:
            continue
        row.update(group_id=meta['split_group_id_v4'], language=meta['language'],
                   core600=meta['core600'],
                   category='homework' if meta['label'] == 'educational' else 'ordinary',
                   source={k: meta.get(k) for k in ('source_dataset', 'source_revision', 'source_url')})
        row['source']['license'] = meta.get('source_license') or meta.get('source_license_in_card')
        if row['id'] in corrections:
            row['chosen'][0]['content'] = corrections[row['id']]['replacement']
            row['correction'] = row['id']
        splits[row['split']].append(row)
    if core_ids != original.keys():
        raise ValueError('Original 600 IDs were not preserved')
    validate_splits(splits)
    normalize = lambda text: ' '.join(text.casefold().split())
    holdout = {normalize(t) for name in ('dev', 'challenge_test')
               for r in read_jsonl(f'data/{name}.jsonl') for t in r['turns']}
    if any(normalize(r['prompt'][-1]['content']) in holdout for r in splits['train']):
        raise ValueError('Training prompt overlaps the frozen behavior suites')
    destination = Path(destination)
    report = {'archive_sha256': sha256(archive), 'input_pairs': len(pairs), 'language': language,
              'corrections': list(corrections), 'exclusions': list(exclusions.values()), 'splits': {}}
    for name, records in splits.items():
        path = destination / f'{name}.jsonl'
        write_jsonl(path, records)
        report['splits'][name] = {'rows': len(records), 'sha256': sha256(path),
            'categories': dict(Counter(r['category'] for r in records)),
            'languages': dict(Counter(r['language'] for r in records))}
    write_json(destination / 'manifest.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive', nargs='?', default='datasets/homework_dpo_6000_local.zip')
    p.add_argument('--out', default='data/expanded')
    p.add_argument('--language', choices=['ru', 'all'], default='ru')
    args = p.parse_args()
    prepare(args.archive, args.out, args.language)
