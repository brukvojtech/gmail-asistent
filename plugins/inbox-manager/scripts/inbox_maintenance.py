"""Small resumable retention batches. Never calls a provider or a model."""
from datetime import timedelta
import inbox_store

COLLECTIONS = ('candidates','drafts','observations','evidence_packets','runs','mailbox_actions','owner_events','draft_revisions','draft_lineages','report_snapshots','events')


def maintain(state, limit=100):
    import inbox_engine as e
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('maintenance limit must be 1-500')
    config = state.get('config') or {}
    days = config.get('learning', {}).get('retention_days', 90)
    cutoff = e.utcnow() - timedelta(days=days)
    historical = e.utcnow() - timedelta(days=365)
    cursor = state.setdefault('maintenance', {'collection':0, 'after':'', 'cycles':0})
    used = removed = compacted = 0
    protected = {a.get('candidate_id') for a in inbox_store.selected(state, 'actions', status__in={'attempting','unknown'})}
    protected.update(d.get('id') for d in inbox_store.selected(state,'drafts',status__in={'creating','unknown'}))
    for revision in inbox_store.selected(state,'draft_revisions',status__in={'reserved','attempting','updating','unknown'}):
        protected.add(revision.get('id'))
        lineage = state.get('draft_lineages', {}).get(revision.get('lineage_id'), {})
        protected.add(lineage.get('managed_candidate_id'))
    name = COLLECTIONS[cursor['collection']]
    collection = state.get(name, {})
    records = collection.records if isinstance(collection, inbox_store.Events) else collection
    if isinstance(records, inbox_store.Records):
        keys = sorted(({r[0] for r in records.query(f'SELECT id FROM im_{name} WHERE id>? ORDER BY id LIMIT ?', (cursor['after'],limit))} | {k for k in records.cache if k > cursor['after']}) - records.deleted)[:limit]
    elif isinstance(records, list):
        keys = [str(i) for i in range(len(records)) if str(i) > cursor['after']][:limit]
    else:
        keys = sorted(k for k in records if k > cursor['after'])[:limit]
    for key in keys:
        row = records[int(key)] if isinstance(records,list) else records[key]
        used += 1
        at = row.get('created_at') or row.get('at') or row.get('started_at')
        if not at or e.iso(at) >= cutoff or row.get('id') in protected:
            continue
        if name in {'candidates','drafts'}:
            if row.get('status') in {'attempting','creating','unknown','draft_reserved'}: continue
            for field in ('body','latest_bot_body','evidence'):
                if row.get(field) is not None:
                    row[field] = None; removed += 1
            if row.get('status') == 'ready': row['status'] = 'expired'
        elif name == 'draft_revisions':
            if row.get('status') in {'attempting','unknown'}: continue
            for field in ('body','latest_bot_body','base_content','current_content','observed_content','requested_content','proposed_content','merged_content','provider_patch','requested_changes','evidence','prepare_proof','review_packet','checked_content','review_owner_answer'):
                if field in row: row[field] = None; removed += 1
            if row.get('status') == 'ready': row['status'] = 'expired'
        elif name == 'draft_lineages':
            if row.get('managed_candidate_id') in protected: continue
            if row.get('latest_bot_body') is not None: row['latest_bot_body'] = None; removed += 1
        elif name == 'report_snapshots':
            # Immutable mappings stay valid for late replies; prune after a year only.
            if e.iso(at) < historical and key != state.get('latest_report_id'):
                del records[key]; compacted += 1
        elif name == 'observations':
            for field in ('observation','result'):
                row.get(field, {}).pop('final_body', None)
        elif name == 'evidence_packets':
            # Action reservations retain their own exact pre-action evidence.
            if row.get('packet') is not None:
                row['packet'] = None; removed += 1
        elif name == 'owner_events':
            if row.get('status') in {'pending','stale','ambiguous'}: continue
            if row.get('answer'):
                row.setdefault('answer_hash', e.digest(row['answer']))
                row.pop('answer'); removed += 1
        elif name == 'runs':
            if row.get('finished_at'):
                row.pop('cycle_result', None)
                if e.iso(at) < historical:
                    for field in ('reconciled_cases','cleanup_steps','reconcile_required'):
                        if field in row: row.pop(field); compacted += 1
        elif name == 'mailbox_actions' and e.iso(at) < historical and row.get('status') in {'verified','failed'}:
            # Preserve identity, scope, receipts and undo evidence; drop redundant raw copies only.
            for field in ('packet','provider_packet','raw_response'):
                if field in row: row.pop(field); compacted += 1
        elif name == 'events' and e.iso(at) < historical and not isinstance(records,list):
            del records[key]; removed += 1
    if keys: cursor['after'] = keys[-1]
    if len(keys) < limit:
        cursor['collection'] = (cursor['collection'] + 1) % len(COLLECTIONS)
        cursor['after'] = ''
        if cursor['collection'] == 0: cursor['cycles'] += 1
    return {'examined': used, 'bodies_or_events_removed':removed, 'records_compacted':compacted,
            'body_retention_days':days, 'history_days':365, 'cursor':dict(cursor)}
