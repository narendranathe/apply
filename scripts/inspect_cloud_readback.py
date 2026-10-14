"""Read-only aggregate probe for uncertain Notion creations; no payload logging."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.notion import CORE_KEYS, NotionStore, _managed_hash, _text
from cloud.records import canonical_json, display_title, validate_record


def inspect(store, records):
    source = {row['id']: validate_record(row) for row in records}
    rows = store._pages({'property': 'Cloud ID', 'rich_text': {'is_not_empty': True}})
    checks = []
    for page in rows:
        page = store._complete_page(page)
        props = page['properties']
        value = validate_record(json.loads(_text(props.get('Cloud Envelope'))))
        expected = source.get(value['id'])
        readback = store.get(value['id'])
        blocks = store._blocks(page['id'])
        code = [b['code'] for b in blocks if b.get('type') == 'code']
        checks.append({
            'verified_get_matches_source': bool(expected and readback and all(readback[k] == expected[k] for k in CORE_KEYS)),
            'source_core_matches': bool(expected and all(value[k] == expected[k] for k in CORE_KEYS)),
            'name_matches': _text(props.get('Name')) == display_title(value),
            'body_text_matches_envelope': len(code) == 1 and _text({'rich_text': code[0].get('rich_text', [])}) == canonical_json(value),
            'managed_hash_matches': _text(props.get('Cloud Managed Hash')) == _managed_hash(props, blocks),
            'body_types': [b.get('type') if b.get('type') in ('code', 'paragraph') else 'other' for b in blocks],
            'code_text_lengths': [[len(x.get('text', {}).get('content', '')) for x in b.get('rich_text', [])] for b in code],
            'code_keys': [sorted(b) for b in code],
            'envelope_chunks': len(props.get('Cloud Envelope', {}).get('rich_text', [])),
        })
    return {'operation': 'verify_uncertain_readback', 'cloud_rows': len(rows), 'checks': checks}


if __name__ == '__main__':
    try:
        exported = json.loads(Path(sys.argv[1]).read_text())
        print(json.dumps(inspect(NotionStore.from_env(), exported['records']), sort_keys=True))
    except Exception as error:
        from cloud.notion import NotionAPIError
        result = {'status': 'blocked', 'error_type': type(error).__name__, 'operation': 'verify_uncertain_readback'}
        if isinstance(error, NotionAPIError):
            result['notion'] = error.diagnostics()
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(2)
