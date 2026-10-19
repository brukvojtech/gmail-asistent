"""Indexed local records with copy-on-write views; no provider or model calls.

The header retains small configuration. Each growing collection is stored as
individual records. Old schema-1 databases migrate transactionally on first write;
a consistent pre-migration backup is created by the engine before taking its lock.
"""
from collections.abc import MutableMapping, MutableSequence, Mapping
from contextlib import contextmanager
import copy
import json
import sqlite3

VERSION = 2
COLLECTIONS = (
    'cases', 'candidates', 'checks', 'actions', 'drafts', 'observations', 'lessons',
    'runs', 'queue', 'member_sends', 'seen_messages', 'decisions', 'owner_events',
    'notifications', 'reminders', 'knowledge_gaps', 'reevaluations', 'mailbox_actions',
    'thread_scope', 'dismissals', 'draft_revisions', 'draft_lineages', 'feedback_batches',
    'report_snapshots', 'report_question_numbers', 'preparations', 'evidence_packets', 'context_sources',
)
FIELDS = {'id', 'state', 'status', 'case_id', 'thread_id', 'at', 'active'}


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, MutableSequence)):
        return [plain(item) for item in value]
    return value


def encode(value):
    return json.dumps(plain(value), separators=(',', ':'), ensure_ascii=False)


def columns(key, value):
    row = value if isinstance(value, dict) else {}
    active = row.get('state') != 'handled'
    if row.get('state') == 'handled':
        active = bool(row.get('hold') or row.get('pending_decision') or
                      row.get('cleanup', {}).get('status') == 'pending')
    return (str(key), row.get('state'), row.get('status'), row.get('case_id'),
            row.get('thread_id'), row.get('at') or row.get('updated_at') or
            row.get('created_at') or row.get('started_at') or '', int(active), encode(value))


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS im_case_links (source TEXT, target TEXT, PRIMARY KEY(source,target))')
    db.execute('CREATE INDEX IF NOT EXISTS ix_case_links_target ON im_case_links(target,source)')
    for name in (*COLLECTIONS, 'events'):
        db.execute(f'CREATE TABLE IF NOT EXISTS im_{name} (id TEXT PRIMARY KEY, state TEXT, status TEXT, case_id TEXT, thread_id TEXT, at TEXT, active INTEGER NOT NULL, data TEXT NOT NULL)')
        for fields in ('state,id', 'status,id', 'case_id,id', 'thread_id,id', 'at,id', 'active,id'):
            suffix = fields.replace(',', '_')
            db.execute(f'CREATE INDEX IF NOT EXISTS ix_{name}_{suffix} ON im_{name}({fields})')


class Records(MutableMapping):
    def __init__(self, db, path, name):
        self.db, self.path, self.name = db, path, name
        self.cache, self.original, self.deleted = {}, {}, set()

    def __deepcopy__(self, memo):
        result = type(self)(self.db, self.path, self.name)
        memo[id(self)] = result
        result.cache = copy.deepcopy(self.cache, memo)
        result.original = dict(self.original)
        result.deleted = set(self.deleted)
        return result

    def query(self, sql, args=()):
        try:
            return self.db.execute(sql, args).fetchall()
        except sqlite3.ProgrammingError as error:
            if 'closed' not in str(error):
                raise
            with sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True) as db:
                return db.execute(sql, args).fetchall()

    def __getitem__(self, key):
        key = str(key)
        if key in self.deleted:
            raise KeyError(key)
        if key not in self.cache:
            rows = self.query(f'SELECT data FROM im_{self.name} WHERE id=?', (key,))
            if not rows:
                raise KeyError(key)
            self.original[key] = rows[0][0]
            self.cache[key] = json.loads(rows[0][0])
        return self.cache[key]

    def __setitem__(self, key, value):
        key = str(key)
        if key not in self.original:
            rows = self.query(f'SELECT data FROM im_{self.name} WHERE id=?', (key,))
            self.original[key] = rows[0][0] if rows else None
        self.deleted.discard(key)
        self.cache[key] = value

    def __delitem__(self, key):
        self[key]
        self.deleted.add(str(key))
        self.cache.pop(str(key), None)

    def __iter__(self):
        keys = {r[0] for r in self.query(f'SELECT id FROM im_{self.name}')}
        yield from sorted((keys | self.cache.keys()) - self.deleted)

    def __len__(self):
        total = self.query(f'SELECT count(*) FROM im_{self.name}')[0][0]
        return total + sum(v is None and k not in self.deleted for k, v in self.original.items()) - sum(self.original.get(k) is not None for k in self.deleted)

    def select(self, _with_keys=False, **filters):
        """Indexed equality/IN/range selection, including uncommitted changes."""
        clauses, args = [], []
        def matches(key, value):
            vals = dict(zip(('id','state','status','case_id','thread_id','at','active','data'), columns(key, value)))
            for name, expected in filters.items():
                field, _, op = name.partition('__')
                actual = vals[field]
                if op == 'in' and actual not in expected: return False
                if op == 'ne' and actual == expected: return False
                if op == 'ge' and (actual is None or actual < expected): return False
                if op == 'lt' and (actual is None or actual >= expected): return False
                if not op and actual != expected: return False
            return True
        for name, value in filters.items():
            field, _, op = name.partition('__')
            if field not in FIELDS or op not in {'', 'in', 'ne', 'ge', 'lt'}:
                raise ValueError('unsupported record filter')
            if op == 'in':
                values = list(value)
                clauses.append(field + ' IN (' + ','.join('?' for _ in values) + ')')
                args.extend(values)
            else:
                symbol = {'': '=', 'ne': 'IS NOT', 'ge': '>=', 'lt': '<'}[op]
                clauses.append(field + ' ' + symbol + ' ?'); args.append(value)
        sql = f'SELECT id FROM im_{self.name}' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        keys = {r[0] for r in self.query(sql, args)} | self.cache.keys()
        for key in sorted(keys - self.deleted):
            value = self[key]
            if matches(key, value):
                yield (key, value) if _with_keys else value

    def flush(self):
        for key in self.deleted:
            self.db.execute(f'DELETE FROM im_{self.name} WHERE id=?', (key,))
            if self.name == 'cases': self.db.execute('DELETE FROM im_case_links WHERE source=?', (key,))
        for key, value in self.cache.items():
            if key not in self.deleted:
                row = columns(key, value)
                if self.original.get(key) != row[-1]:
                    self.db.execute(f'INSERT OR REPLACE INTO im_{self.name} VALUES (?,?,?,?,?,?,?,?)', row)
                    if self.name == 'cases': save_links(self.db, key, value)


class Events(MutableSequence):
    def __init__(self, records): self.records = records
    def __len__(self): return len(self.records)
    def __iter__(self):
        for key in self.records: yield self.records[key]
    def __getitem__(self, index):
        keys = list(self.records)
        if isinstance(index, slice): return [self.records[key] for key in keys[index]]
        return self.records[keys[index]]
    def __setitem__(self, index, value): self.records[list(self.records)[index]] = value
    def __delitem__(self, index): del self.records[list(self.records)[index]]
    def insert(self, index, value):
        if index != len(self): raise ValueError('events are append-only')
        self.append(value)
    def append(self, value):
        rows = self.records.query('SELECT max(id) FROM im_events')
        base = int(rows[0][0] or 0)
        base = max([base, *(int(k) for k in self.records.cache)])
        self.records[f'{base+1:020d}'] = value
    def __deepcopy__(self, memo): return Events(copy.deepcopy(self.records, memo))
    def since(self, at): return self.records.select(at__ge=at)


def load(db, path, header):
    if header is None: return None
    if header.get('storage_version') != VERSION: return header
    state = dict(header)
    for name in COLLECTIONS: state[name] = Records(db, path, name)
    state['events'] = Events(Records(db, path, 'events'))
    return state


def save(db, path, state):
    changes_before = db.total_changes
    if state.get("storage_version") != VERSION:
        initialize(db)
    header = {key: value for key, value in state.items() if key not in COLLECTIONS and key != 'events'}
    header['storage_version'] = VERSION
    for name in COLLECTIONS:
        rows = state.get(name, {})
        if isinstance(rows, Records):
            rows.flush()
        else:
            db.execute(f'DELETE FROM im_{name}')
            for key, value in rows.items():
                db.execute(f'INSERT INTO im_{name} VALUES (?,?,?,?,?,?,?,?)', columns(key, value))
                if name == 'cases': save_links(db, key, value)
    events = state.get('events', [])
    if isinstance(events, Events): events.records.flush()
    else:
        db.execute('DELETE FROM im_events')
        for index, value in enumerate(events, 1):
            db.execute('INSERT INTO im_events VALUES (?,?,?,?,?,?,?,?)', columns(f'{index:020d}', value))
    encoded = encode(header)
    prior = db.execute('SELECT data FROM engine WHERE id=1').fetchone()
    if not prior or prior[0] != encoded or db.total_changes != changes_before:
        header['engine_revision'] = (json.loads(prior[0]).get('engine_revision', 0) if prior else 0) + 1
        encoded = encode(header)
        state['engine_revision'] = header['engine_revision']
        db.execute('INSERT OR REPLACE INTO engine VALUES (1, ?)', (encoded,))


def selected(state, name, **filters):
    rows = state.get(name, {})
    if isinstance(rows, Records): return rows.select(**filters)
    def matches(value):
        for key, expected in filters.items():
            field, _, op = key.partition('__')
            actual = int(value.get('state') != 'handled' or bool(value.get('hold') or value.get('pending_decision') or value.get('cleanup', {}).get('status') == 'pending')) if field == 'active' else value.get(field)
            if op == 'in' and actual not in expected: return False
            if op == 'ne' and actual == expected: return False
            if op == 'ge' and (actual is None or actual < expected): return False
            if op == 'lt' and (actual is None or actual >= expected): return False
            if not op and actual != expected: return False
        return True
    return (value for value in rows.values() if matches(value))


def hot_state(state):
    """Working projection excludes closed history, includes current scan records."""
    if not isinstance(state.get('cases'), Records): return state
    result = dict(state)
    cases = {c['id']: c for c in selected(state, 'cases', active=1)}
    run = state.get('runs', {}).get((state.get('lease') or {}).get('run_id'), {})
    for key in run.get('case_ids') or []:
        if key in state['cases']: cases[key] = state['cases'][key]
    for scan in state.get('inbox_scans', {}).values():
        for mid in scan.get('message_ids', []):
            tid = state.get('seen_messages', {}).get(mid)
            if tid:
                for c in selected(state, 'cases', thread_id=tid): cases[c['id']] = c
    for case in list(cases.values()):
        for linked in linked_cases(state, case):
            cases[linked['id']] = linked
    result['cases'] = cases
    # Case-dependent records stay linked; state writes always use the original.
    for name in ('actions','drafts','decisions','reevaluations','mailbox_actions'):
        result[name] = dict(state[name].select(_with_keys=True, case_id__in=cases))
    return result


def save_links(db, key, value):
    db.execute('DELETE FROM im_case_links WHERE source=?', (key,))
    db.executemany('INSERT OR IGNORE INTO im_case_links VALUES (?,?)', [(key,target) for target in value.get('linked_cases',[])])


def linked_cases(state, case):
    records = state['cases']
    keys, pending = set(), [case['id']]
    while pending:
        key = pending.pop()
        if key in keys: continue
        keys.add(key)
        current = records[key]
        neighbours = set(current.get('linked_cases', []))
        if isinstance(records, Records):
            reverse = {r[0] for r in records.query('SELECT source FROM im_case_links WHERE target=?',(key,))}
            reverse |= set(records.cache)
            neighbours.update(k for k in reverse if k in records and key in records[k].get('linked_cases', []))
        else:
            neighbours.update(k for k, row in records.items() if key in row.get('linked_cases', []))
        pending.extend(neighbours - keys)
    return [records[key] for key in sorted(keys)]
