"""Bounded draft preparation and reusable, freshness-bound provider evidence."""
import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path


_MAX_LOCAL_SOURCE_BYTES = 10 * 1024 * 1024
_MAX_LOCAL_REFRESH_TTL = timedelta(days=30)
_NO_DRAFT_REASONS = {
    'no_reply': {'already_resolved', 'no_reply_needed', 'another_owner'},
    'needs_owner_answer': {'decision_controls_whole_reply'},
    'blocked': {'blocker'},
}


def _refresh_expired_local_source(state, key):
    """Renew one explicitly selected expired local source from its actual bytes."""
    import inbox_engine as e
    source = state['sources'].get(key)
    policy = e.source_policy(state, key)
    if not source or not policy or source.get('status') != 'current':
        return source
    if source.get('valid_until') and e.iso(source['valid_until']) > e.utcnow():
        return source
    local_path = source.get('local_path')
    if not local_path:
        return source
    path = Path(local_path)
    try:
        if str(path.resolve()) != policy['origin'] or not path.is_file():
            return source
        before = path.stat()
        if before.st_size > _MAX_LOCAL_SOURCE_BYTES:
            raise ValueError('local source exceeds bounded refresh size')
        with path.open('rb') as handle:
            content = handle.read(_MAX_LOCAL_SOURCE_BYTES + 1)
        after = path.stat()
    except OSError:
        return source
    if len(content) > _MAX_LOCAL_SOURCE_BYTES:
        raise ValueError('local source exceeds bounded refresh size')
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('local source changed during refresh')
    content_hash = hashlib.sha256(content).hexdigest()
    prior_ttl = e.iso(source['valid_until']) - e.iso(source['verified_at'])
    ttl = min(max(prior_ttl, timedelta(minutes=1)), _MAX_LOCAL_REFRESH_TTL)
    now = e.utcnow()
    return e.update_source(state, {
        'source_id': key,
        'origin': policy['origin'],
        'version': source['version'] if content_hash == source.get('content_hash') else 'sha256:' + content_hash,
        'status': 'current',
        'verified_at': now.isoformat(),
        'valid_until': (now + ttl).isoformat(),
        'local_path': local_path,
        'content_hash': content_hash,
    })


def enabled(state):
    return bool((state.get('config') or {}).get('draft_first', False))


def basis(state, case):
    import inbox_engine as e
    return e.digest({'content': case['snapshot'].get('content_fingerprint', case['snapshot']['fingerprint']),
                     'profile': state['version'], 'obligations': case.get('obligations', {}),
                     'sources': {k: state['sources'].get(k, {}).get('version') for k in case.get('source_ids', [])},
                     'owner_answers': case.get('owner_answers', {}), 'takeover': case.get('takeover')})


def pending(state, case):
    if not enabled(state) or case.get('state') == 'handled' or case.get('takeover'):
        return False
    # A genuine correspondent wait needs observation, not another unsolicited draft.
    if case.get('state') == 'waiting_correspondent':
        return False
    item = state.get('preparations', {}).get(case['id'])
    if item and item.get('knowledge_dependencies'):
        import inbox_knowledge
        records = state['cases']
        if hasattr(records, 'path') and not inbox_knowledge.check_dependencies(records.path.parent, item['knowledge_dependencies'])['current']:
            return True
    return not item or item.get('basis') != basis(state, case)


def save_packet(state, auth, packet):
    import inbox_engine as e
    import inbox_snapshot as snapshots
    e.lease(state, auth)
    packet = snapshots.normalize_packet(packet)
    snap = snapshots.build_snapshot(packet)
    if packet.get('mailbox', '').casefold() != state['mailbox'].casefold():
        raise ValueError('packet belongs to another mailbox')
    if not e.fresh(snap['observed_at'], state['config']['transport']['snapshot_max_age_seconds']):
        raise ValueError('packet requires fresh actual provider observation')
    if packet.get('draft_inventory'):
        from inbox_retrieval import verify_packet_inventory
        verify_packet_inventory(state, packet)
    key = 'packet-' + e.digest(packet)
    state.setdefault('evidence_packets', {}).setdefault(key, {'id': key, 'case_id': None,
        'thread_id': snap['thread_id'], 'mailbox': state['mailbox'], 'created_at': e.stamp(),
        'observed_at': snap['observed_at'], 'hash': e.digest(packet), 'packet': copy.deepcopy(packet)})
    return {'packet_ref': key, 'thread_id': snap['thread_id'], 'observed_at': snap['observed_at']}


def resolve_packet(state, key):
    import inbox_engine as e
    row = state.get('evidence_packets', {}).get(key)
    if not row or not row.get('packet') or row['mailbox'] != state['mailbox']:
        raise ValueError('unknown, expired or wrong-mailbox packet reference')
    if e.digest(row['packet']) != row['hash']:
        raise ValueError('provider evidence hash changed')
    return copy.deepcopy(row['packet'])


def assess(state, auth, p):
    import inbox_engine as e
    import inbox_snapshot
    import inbox_reconcile
    e.lease(state, auth)
    case = e.get_case(state, p)
    snap = inbox_snapshot.build_snapshot(p['packet'])
    if p['packet'].get('mailbox', '').lower() != state['mailbox'] or snap['thread_id'] != case['thread_id']:
        raise ValueError('preparation packet belongs to another conversation')
    e.inbox_priority.require_scope(state, case, snap)
    if snap['fingerprint'] != case['snapshot']['fingerprint'] or not e.fresh(snap['observed_at'], state['config']['transport']['snapshot_max_age_seconds']):
        raise ValueError('reconcile fresh conversation before preparing')
    run = state['runs'][auth['run_id']]
    already_reconciled = run.get('reconciled_cases', {}).get(case['id']) == inbox_reconcile.basis(state, case)
    disposition = p.get('disposition')
    if disposition not in {'draft_ready', 'needs_owner_answer', 'no_reply', 'blocked'}:
        raise ValueError('preparation disposition required')
    no_draft_reason = p.get('no_draft_reason')
    if no_draft_reason is not None:
        if disposition == 'draft_ready' or no_draft_reason not in _NO_DRAFT_REASONS.get(disposition, set()):
            raise ValueError('no_draft_reason does not match preparation disposition')
    reason = e.text(p.get('evidence'), 'preparation evidence', 12000)
    checked = p.get('sources_checked', [])
    if not isinstance(checked, list) or len(checked) > 30:
        raise ValueError('bounded sources_checked required')
    for row in checked:
        if not isinstance(row, dict): raise ValueError('source check must be an object')
        e.text(row.get('reason'), 'source check evidence', 4000)
        if row.get('status') not in {'checked','unavailable','not_needed'}:
            raise ValueError('invalid source check status')
        key = row.get('source_id')
        if row['status'] == 'checked' and key != 'current_thread':
            source = state['sources'].get(key)
            prior_hash = source.get('content_hash') if source else None
            if source and source.get('status') == 'current' and source.get('valid_until') and e.iso(source['valid_until']) <= e.utcnow():
                source = _refresh_expired_local_source(state, key)
            if not e.source_policy(state, key) or not source or source.get('status') != 'current' or not source.get('valid_until') or e.iso(source['valid_until']) <= e.utcnow():
                raise ValueError('checked source needs current registered evidence')
            if source.get('local_path') and prior_hash != source.get('content_hash'):
                e.event(state, 'local_source_refreshed', case_id=case['id'], source_id=key,
                        version=source['version'])
                return {'case_id': case['id'], 'disposition': 'source_changed', 'next': 'research',
                        'source_refresh': {'source_id': key, 'version': source['version'],
                                           'local_path': source['local_path'],
                                           'content_hash': source['content_hash']},
                        'reason': 'Local source bytes changed; read the updated source before preparing a reply.'}
    if not checked:
        raise ValueError('record current thread sufficiency or the targeted sources checked')
    questions = p.get('questions', [])
    if not isinstance(questions, list) or len(questions) > 10:
        raise ValueError('bounded questions required')
    # Validate independently before changing obligations; transaction rolls back on failure.
    decisions = []
    for question in questions:
        if not isinstance(question, dict): raise ValueError('question must be an object')
        decisions.append(e.create_decision(state, case, question))
    if decisions:
        case.update(hold='owner_decision', pending_decision=True, state='waiting_owner')
    if disposition == 'needs_owner_answer' and not (decisions or e.pending_decisions(state, case)):
        raise ValueError('owner answer disposition requires a precise saved question')
    if disposition == 'draft_ready':
        selected = e.obligation_selection(case, p.get('obligation_ids'), required=bool(case.get('obligations')))
        if selected:
            for key in selected:
                row = case['obligations'][key]
                if row['state'] != 'needs_action' or row.get('responsibility_type') != 'bot':
                    raise ValueError('draft scope must identify supported bot work, separate from owner decisions')
        elif case['state'] != 'needs_action' or case.get('responsibility_type') != 'bot':
            raise ValueError('supported bot work required for draft preparation')
    else:
        selected = []
    from inbox_revisions import _knowledge_dependencies
    dependencies = _knowledge_dependencies(p.get('knowledge_dependencies'))
    item = {'id': case['id'], 'case_id': case['id'], 'disposition': disposition,
            'evidence': reason, 'sources_checked': copy.deepcopy(checked), 'decision_ids': [d['id'] for d in decisions],
            'source_versions': {row['source_id']: state['sources'][row['source_id']]['version']
                                for row in checked if row['status'] == 'checked' and row['source_id'] != 'current_thread'},
            'obligation_ids': selected, 'knowledge_dependencies': dependencies, 'created_at': e.stamp(), 'basis': basis(state, case)}
    if no_draft_reason is not None:
        item['no_draft_reason'] = no_draft_reason
    state.setdefault('preparations', {})[case['id']] = item
    if already_reconciled:
        run.setdefault('reconciled_cases', {})[case['id']] = inbox_reconcile.basis(state, case)
    e.event(state, 'preparation_assessed', case_id=case['id'], disposition=disposition)
    return {'case_id': case['id'], 'disposition': disposition, 'decision_ids': item['decision_ids'],
            'next': 'candidate' if disposition == 'draft_ready' else 'continue'}


def _dependencies_current(root, state, sources, dependencies):
    """Carry reviewed prose only while its original evidence still exists."""
    import inbox_engine as e
    if root is None:
        return False
    try:
        e.validate_profile(root, state['config']['profile_files'])
        for key, version in sources.items():
            source = state['sources'].get(key)
            if (not source or not e.source_policy(state, key) or source.get('version') != version or
                    source.get('status') != 'current' or not source.get('valid_until') or
                    e.iso(source['valid_until']) <= e.utcnow() or
                    e.iso(source['verified_at']) > e.utcnow() + timedelta(seconds=5)):
                return False
            if source.get('local_path'):
                path = Path(source['local_path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.get('content_hash'):
                    return False
        if dependencies:
            import inbox_knowledge
            if not inbox_knowledge.check_dependencies(root, dependencies)['current']:
                return False
    except (ValueError, OSError, KeyError, TypeError):
        return False
    return True


def _record_draft_review(state, auth, case, snap, draft_id, selected, item):
    import inbox_engine as e
    import inbox_reconcile
    for key in selected:
        row = case['obligations'][key]
        row.update(state='waiting_owner', responsibility_type='owner', responsible='owner',
                   draft_review_id=draft_id, draft_status='present',
                   next_action='Review the prepared Gmail draft.', version=row['version'] + 1,
                   updated_at=e.stamp())
    record = next((row for row in state['drafts'].values()
                   if row.get('case_id') == case['id'] and row.get('draft_id') == draft_id), None)
    if record is not None:
        record.setdefault('review_obligation_versions', {}).update(
            {key: case['obligations'][key]['version'] for key in selected})
    case['snapshot'] = snap
    case['state'] = 'waiting_owner'
    case['pending_decision'] = True
    # This records an artifact awaiting review, never a completed customer request.
    if not any(row['state'] == 'needs_action' and row.get('responsibility_type') == 'bot'
               for row in case.get('obligations', {}).values()):
        item.update(disposition='blocked', no_draft_reason='blocker', obligation_ids=[],
                    evidence='Verified Gmail draft awaits owner review.', basis=basis(state, case))
    state['preparations'][case['id']] = item
    state['runs'][auth['run_id']].setdefault('reconciled_cases', {})[case['id']] = inbox_reconcile.basis(state, case)
    e.event(state, 'draft_review_observed', case_id=case['id'], draft_id=draft_id,
            obligation_ids=selected)


def _observe_own_draft(root, state, auth, packet, case, snap):
    import inbox_engine as e
    import inbox_reconcile
    from inbox_cycle import _same_readback
    record = state['drafts'].get(case.get('pending_candidate_id'), {})
    proof = record.get('own_draft_observation', {})
    item = state.get('preparations', {}).get(case['id'])
    if (not proof or record.get('status') != 'pending' or not record.get('readback_verified_at') or
            record.get('latest_revision_id') or not item or e.digest(item) != proof.get('preparation_hash') or
            proof.get('case_basis') != inbox_reconcile.basis(state, case) or
            case.get('takeover') or case.get('hold') not in {None, 'owner_decision'} or
            case['id'] in state.get('reevaluations', {})):
        return False
    if (e.iso(snap['observed_at']) < e.iso(record['readback_verified_at']) or
            snap['draft_ids'] != [record.get('draft_id')] or
            snap.get('message_fingerprint') != case['snapshot'].get('message_fingerprint') or
            any(snap.get(key) != case['snapshot'].get(key) for key in ('trigger_id', 'automated', 'categories'))):
        return False
    draft = next(row for row in packet['drafts'] if row['id'] == record['draft_id'])
    try:
        _same_readback(record, draft, state)
    except (ValueError, KeyError, TypeError):
        return False
    if (not _dependencies_current(root, state, record['source_versions'], record.get('knowledge_dependencies', [])) or
            not _dependencies_current(root, state, proof['source_versions'], item.get('knowledge_dependencies', [])) or
            any(row.get('case_id') == case['id'] and row.get('status') in {'attempting', 'unknown'}
                for row in state.get('actions', {}).values())):
        return False
    selected = record.get('obligation_ids', [])
    if any(case.get('obligations', {}).get(key, {}).get('state') != 'needs_action' or
           case['obligations'][key].get('responsibility_type') != 'bot' for key in selected):
        return False
    _record_draft_review(state, auth, case, snap, record['draft_id'], selected, copy.deepcopy(item))
    record.pop('own_draft_observation', None)
    return True


def observe(state, auth, packet, case_id, root=None):
    """Same-evidence fast path; changed content remains a model assessment."""
    import inbox_engine as e
    import inbox_snapshot
    import inbox_reconcile
    e.lease(state, auth)
    packet = inbox_snapshot.normalize_packet(packet)
    case = e.get_case(state, {'case_id':case_id})
    snap = inbox_snapshot.build_snapshot(packet)
    if packet.get('mailbox', '').lower() != state['mailbox'] or snap['thread_id'] != case['thread_id']:
        raise ValueError('observation belongs to another conversation')
    e.inbox_priority.require_scope(state, case, snap)
    if not e.fresh(snap['observed_at'], state['config']['transport']['snapshot_max_age_seconds']):
        raise ValueError('fresh observation required')
    cached = save_packet(state, auth, packet)
    reconcile_summaries(state, case, packet)
    old = case['snapshot']
    changed = old.get('content_fingerprint', old['fingerprint']) != snap.get('content_fingerprint', snap['fingerprint'])
    due = any(x['case_id'] == case_id for x in e.due({'cases':{case_id:case}, **{k:v for k,v in state.items() if k!='cases'}}))
    sources_changed = any(state['sources'].get(k, {}).get('status') != 'current' or
        e.iso(state['sources'][k]['valid_until']) <= e.utcnow() for k in case.get('source_ids', []))
    if not due and not sources_changed and _observe_own_draft(root, state, auth, packet, case, snap):
        return {**cached, 'case_id': case_id, 'assessment_required': False, 'reason': 'verified_own_draft'}
    if changed or due or sources_changed or pending(state, case) or case_id in state.get('reevaluations', {}):
        return {**cached, 'case_id': case_id, 'assessment_required': True, 'case_basis': inbox_reconcile.basis(state, case),
                'case': e.summary_case(state, case)}
    case['snapshot'] = snap
    if packet.get('draft_inventory'):
        from inbox_drafts import reconcile_presence
        reconcile_presence(state, case, packet)
    state['runs'][auth['run_id']].setdefault('reconciled_cases', {})[case_id] = inbox_reconcile.basis(state, case)
    return {**cached, 'case_id': case_id, 'assessment_required': False, 'reason': 'unchanged_evidence'}


def reconcile_summaries(state, case, packet):
    """Suppress checked summaries on any observed content or revision drift."""
    from inbox_cycle import _same_readback
    for record in state['drafts'].values():
        if record.get('case_id') != case['id'] or not record.get('summary'):
            continue
        record['summary_content_current'] = False
        if record.get('latest_revision_id'):
            continue
        draft = next((d for d in packet.get('drafts', []) if d['id'] == record.get('draft_id')), None)
        if draft is None:
            continue
        try:
            _same_readback(record, draft, state)
        except (ValueError, KeyError, TypeError):
            continue
        record['summary_content_current'] = True
