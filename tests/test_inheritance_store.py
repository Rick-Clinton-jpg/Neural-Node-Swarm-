import sqlite3

import pytest
from jsonschema import ValidationError

from neural_node_swarm.inheritance_store import InheritanceStore


@pytest.mark.parametrize('poison,corrupt,expected', [(False, False, 10), (True, False, None), (True, True, 90)])
def test_poisoning_survives_restart(tmp_path, poison, corrupt, expected):
    path = tmp_path / 'memory.db'
    store = InheritanceStore(path)
    source = store.accept('run', 'hand', 90 if poison else 10, 'e:hand')
    audit, lineage = store.promote('run', 'hand', source['id'], {'e:hand': 90 if corrupt else 10})
    reopened = InheritanceStore(path)
    latest = reopened.read('lineage', 'run', 'hand')
    assert (latest is not None) == (expected is not None)
    if latest:
        assert reopened.read('active', 'run', 'hand', latest['source_id'])['value'] == expected
        assert reopened.read('audit', 'run', 'hand', latest['promotion_id'])['passed']
    assert reopened.read('active', 'run', 'hand')['id'] == source['id']
    assert reopened.read('active', 'other-run', 'hand') is None
    assert reopened.read('lineage', 'run', 'leg') is None
    assert reopened.read('active', 'run', 'hand', audit['id']) is None


def test_atomic_promotion_rollback(tmp_path, monkeypatch):
    store = InheritanceStore(tmp_path / 'memory.db')
    source = store.accept('run', 'leg', 20, 'e:leg')
    insert = store._insert
    def fail(db, record):
        if record['kind'] == 'lineage':
            raise RuntimeError('injected write failure')
        insert(db, record)
    monkeypatch.setattr(store, '_insert', fail)
    with pytest.raises(RuntimeError):
        store.promote('run', 'leg', source['id'], {'e:leg': 20})
    assert store.read('audit', 'run', 'leg') is None
    assert store.read('lineage', 'run', 'leg') is None


def test_reject_invalid_and_preserve_history(tmp_path):
    store = InheritanceStore(tmp_path / 'memory.db')
    with pytest.raises(ValidationError):
        store.accept('run', 'hand', True, 'e:hand')
    a = store.accept('run', 'hand', 10, 'e:hand')
    with pytest.raises(KeyError):
        store.promote('other', 'hand', a['id'], {'e:hand': 10})
    with store.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM inheritance_records')
    assert store.read('active', 'run', 'hand')['id'] == a['id']
