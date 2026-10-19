"""Offline regressions for observation-only self-addressed Inbox Manager threads."""
import copy
import datetime as dt
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inbox_engine as engine
import inbox_cycle as cycle
import inbox_snapshot as snapshot

MAILBOX = 'owner@example.test'


def packet(*, sender=MAILBOX, automated=True, reply_to=None):
    headers = {'message-id': '<notice@example.test>'}
    if reply_to:
        headers['reply-to'] = reply_to
    return {'complete': True, 'mailbox': MAILBOX, 'thread_id': 'thread-1',
            'trigger_id': 'msg-1', 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'automated': automated, 'drafts': [], 'messages': [{
                'id': 'msg-1', 'thread_id': 'thread-1', 'from': sender, 'to': [MAILBOX],
                'cc': [], 'body': 'A notification without a request.', 'subject': 'Notification',
                'headers': headers, 'label_ids': ['INBOX', 'CATEGORY_PERSONAL']}]}


class SnapshotRegression(unittest.TestCase):
    def test_self_notification_is_observable_without_external_correspondents(self):
        value = snapshot.build_snapshot(packet())
        self.assertEqual(value['correspondents'], [])
        self.assertIs(engine.snapshot(value), value)

    def test_self_note_is_observable_without_claiming_it_is_automated(self):
        self.assertEqual(engine.snapshot(snapshot.build_snapshot(packet(automated=False)))['correspondents'], [])

    def test_reply_to_is_not_promoted_to_authorized_correspondent(self):
        value = engine.snapshot(snapshot.build_snapshot(packet(reply_to='untrusted@example.test')))
        self.assertEqual(value['correspondents'], [])

    def test_external_correspondent_is_preserved(self):
        value = engine.snapshot(snapshot.build_snapshot(packet(sender='client@example.test')))
        self.assertEqual(value['correspondents'], ['client@example.test'])

    def test_missing_or_malformed_correspondents_still_reject(self):
        for invalid in (None, '', {}, [''], ['not-an-email'], [42], ['a@example.test'] * 21):
            with self.subTest(value=invalid):
                value = snapshot.build_snapshot(packet())
                value['correspondents'] = invalid
                with self.assertRaises(ValueError):
                    engine.snapshot(value)

    def test_actual_recipient_list_still_must_be_nonempty(self):
        with self.assertRaisesRegex(ValueError, 'invalid recipients'):
            engine.addresses([])


class CycleRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inbox-self-notification-test-')
        self.case_home = Path(self.temp.name)
        for name in ('job.md', 'instructions.md', 'voice.md'):
            (self.case_home / name).write_text('Synthetic offline test. Drafts only.\n')
        engine.execute(self.case_home, 'init', {'mailbox': MAILBOX})
        config = {
            'job': 'Synthetic offline test', 'draft_first': True,
            'profile_files': {name: hashlib.sha256((self.case_home / name).read_bytes()).hexdigest()
                              for name in ('job.md', 'instructions.md', 'voice.md')},
            'rules': [], 'limits': {'per_run': 5, 'per_day': 10},
            'followups': {'enabled': False, 'max_attempts': 0, 'delay_hours': 48},
            'transport': {'mailbox': MAILBOX, 'card_ref': 'offline-test', 'send': False,
                          'drafts': True, 'independent_check': False,
                          'proven_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                          'snapshot_max_age_seconds': 120},
            'context_access': 'workspace', 'source_policy': {}, 'research_guide': [],
            'inbox_priority': {'processing_tabs': ['primary']},
            'cleanup': {'archive_on_resolution': False, 'waiting_policy': 'keep_inbox',
                        'workflow_labels': {}, 'draft_cleanup': 'preserve'},
            'learning': {'enabled': False, 'actor_mode': 'observe_only', 'retention_days': 90}}
        engine.execute(self.case_home, 'configure', {'base_version': 0, 'owner_confirmed': True,
                       'approval_ref': 'synthetic-test-consent', 'config': config})
        self.begin = cycle.execute(self.case_home, 'begin', {'mailbox': MAILBOX})
        self.auth = {k: self.begin[k] for k in ('run_id', 'token')}
        self.observation = packet()
        plan = self.begin['discovery'][0]
        page = {'page_cursor': None, 'complete': True, 'next_cursor': None,
                'entries': [{'thread_id': 'thread-1', 'trigger_id': 'msg-1',
                             'received_at': (dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat()}],
                'provider_evidence': 'synthetic-offline-page'}
        result = cycle.execute(self.case_home, 'intake', {**self.auth, **{k: plan[k] for k in ('lane','query','window_end')}, 'pages': [page]})
        self.assertEqual(result['errors'], [])

    def tearDown(self):
        self.temp.cleanup()

    def assessment(self):
        return {'case_id': 'notice', 'expected_basis': None, 'unresolved_ask': 'No request.',
                'next_action': 'No reply; preserve mail.', 'responsible': 'Inbox Manager',
                'responsibility_type': 'bot', 'state': 'handled', 'disposition': 'no_reply',
                'topic': 'Notification', 'context': 'A self-addressed notification without a request.',
                'recommendation': 'No reply required.', 'preparation_evidence': 'Read complete synthetic notice.',
                'sources_checked': [{'source_id': 'current_thread', 'status': 'checked', 'reason': 'Full notice.'}],
                'questions': [], 'no_draft_reason': 'no_reply_needed',
                'outcome': 'No reply required', 'outcome_evidence': 'The complete notice contains no request.'}

    def test_no_reply_intake_clears_queue_without_draft_or_send(self):
        item = {'packet': self.observation, 'assessment': self.assessment()}
        result = cycle.execute(self.case_home, 'intake', {**self.auth, 'threads': [item]})
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['results']), 1)
        # Replaying an accepted packet must not duplicate a case or external action.
        replay = cycle.execute(self.case_home, 'intake', {**self.auth, 'threads': [item]})
        self.assertEqual(replay['errors'], [])
        state = engine.execute(self.case_home, 'status')
        self.assertEqual(len(state['cases']), 1)
        self.assertEqual(len(state['queue']), 0)
        self.assertEqual(len(state['candidates']), 0)
        self.assertEqual(len(state['actions']), 0)
        self.assertEqual(state['cases']['notice']['snapshot']['correspondents'], [])
        self.assertEqual(state['cases']['notice']['state'], 'handled')
        result = cycle.execute(self.case_home, 'finish', self.auth)
        self.assertEqual(result['remaining']['queue'], 0)

    def test_empty_recipient_draft_rejects_atomically(self):
        assessment = self.assessment()
        for key in ('outcome', 'outcome_evidence', 'no_draft_reason'):
            assessment.pop(key)
        assessment.update(state='needs_action', disposition='draft_ready',
                          unresolved_ask='Draft requested by test only.', next_action='Prepare draft.',
                          summary='Synthetic proposed reply.',
                          candidate={'candidate_id': 'reply-1', 'body': 'Synthetic reply.',
                                     'subject': 'Re: Notification', 'recipients': [],
                                     'mode': 'draft', 'kind': 'reply', 'writer_id': 'test-writer'})
        result = cycle.execute(self.case_home, 'intake', {**self.auth, 'threads': [
            {'packet': self.observation, 'assessment': assessment}]})
        self.assertEqual(len(result['results']), 0)
        self.assertIn('invalid recipients', result['errors'][0]['message'])
        state = engine.execute(self.case_home, 'status')
        self.assertEqual(len(state['candidates']), 0)
        self.assertEqual(len(state['cases']), 0)
        self.assertEqual(len(state['queue']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
