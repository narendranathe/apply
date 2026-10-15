"""Stable in-memory writer shared by imports and the hosted module entrypoint."""


class Blocked(RuntimeError):
    pass


class ShadowStore:
    """Reads live state, stages writes in memory; shadow cannot compete with locals."""
    def __init__(self, base=None):
        self.base = base
        self.records = {}

    def get(self, identity):
        return self.records.get(identity) or (self.base.get(identity) if self.base else None)

    def put(self, value):
        old = self.get(value['id'])
        if old and any(old.get(k) != value.get(k) for k in ('hash', 'kind', 'status', 'payload')):
            raise Blocked('shadow_identity_conflict')
        self.records[value['id']] = value
        return value

    def query(self, kind=None):
        merged = {x['id']: x for x in (self.base.query(kind=kind) if self.base else [])}
        merged.update({k: v for k, v in self.records.items() if kind is None or v['kind'] == kind})
        return list(merged.values())

    def current_contact_observations(self):
        return self.base.current_contact_observations() if self.base and hasattr(self.base, 'current_contact_observations') else []

    def index_source_records(self):
        from cloud.agent_index import GENERATED_KINDS, SCHEMA
        if self.base and hasattr(self.base, 'index_source_records'):
            rows = self.base.index_source_records()
        else:
            rows = self.base.query() if self.base else []
        merged = {row['id']: row for row in rows}
        merged.update(self.records)
        return [row for row in merged.values() if row['kind'] not in GENERATED_KINDS
                and not (row['kind'] == 'agent_tasks' and row['payload'].get('schema') == SCHEMA)]

    def latest_index_manifest(self):
        staged = [row for row in self.records.values() if row['kind'] == 'index_manifests']
        if self.base and hasattr(self.base, 'latest_index_manifest'):
            previous = self.base.latest_index_manifest()
        else:
            previous = max((row for row in (self.base.query(kind='index_manifests') if self.base else [])),
                           key=lambda row: (row['payload'].get('generated_at', ''), row['id']), default=None)
        if previous is not None:
            staged.append(previous)
        return max(staged, key=lambda row: (row['payload'].get('generated_at', ''), row['id']), default=None)
