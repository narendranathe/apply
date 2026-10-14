"""Fresh archived-application coverage before any permitted provider request."""
from .notion import ConflictError
from .records import validate_record

CORE = ('id', 'kind', 'payload', 'status', 'hash')


def verify_application_coverage(store, exported):
    expected = {row['id']: validate_record(row) for row in exported['records'] if row['kind'] == 'applications'}
    # This is a fresh read, including normal corruption/dedup guards. An old
    # archived snapshot never substitutes for today's provider-action preflight.
    current = {row['id']: validate_record(row) for row in store.query(kind='applications')}
    for identity, source in expected.items():
        found = current.get(identity)
        if not found or any(found[key] != source[key] for key in CORE):
            raise ConflictError('Archived application history is missing or changed; reconcile before effects')
    return len(expected)
