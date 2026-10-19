#!/usr/bin/env python3
"""Synthetic offline regressions for own-draft fingerprints. No mailbox access."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inbox_snapshot as snapshot
import inbox_engine as engine
from inbox_cycle import _same_readback


def fixture():
    incoming = dict(id='m1', thread_id='t1', **{'from': 'person@example.com'},
                    to=['owner@example.com'], cc=[], bcc=[], subject='Question',
                    body='Please investigate.', headers={}, labelIds=['INBOX'])
    message = dict(incoming, id='m2', body='I will investigate.', labelIds=['DRAFT'])
    message['from'], message['to'] = 'owner@example.com', ['person@example.com']
    draft = dict(message, id='d1', message_id='m2', attachments=[])
    return dict(complete=True, thread_id='t1', trigger_id='m1', mailbox='owner@example.com',
                automated=False, observed_at=engine.stamp(), messages=[incoming, message], drafts=[draft],
                inventory_id='i1', inventory_generation=1,
                draft_inventory=dict(complete=True, inventory_id='i1', generation=1, evidence_ref='synthetic'),
                thread_completeness=dict(complete=True, evidence_ref='synthetic'))


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.packet = fixture()
        before = copy.deepcopy(self.packet)
        before['messages'] = before['messages'][:1]
        before['drafts'] = []
        self.before = snapshot.build_snapshot(before)

    def test_own_draft_preserves_conversation_but_all_full_evidence_survives(self):
        before_packet = copy.deepcopy(self.packet)
        actual = snapshot.build_snapshot(self.packet)
        self.assertEqual(actual['message_fingerprint'], self.before['message_fingerprint'])
        self.assertEqual(actual['message_ids'], ['m1', 'm2'])
        for key in ('fingerprint', 'content_fingerprint'):
            self.assertNotEqual(actual[key], self.before[key])
        self.assertEqual(self.packet, before_packet)

    def test_new_human_message_and_sent_change_context(self):
        for labels in (['INBOX'], ['SENT'], ['DRAFT', 'SENT']):
            with self.subTest(labels=labels):
                p = copy.deepcopy(self.packet)
                p['messages'][1]['labelIds'] = labels
                self.assertNotEqual(snapshot.build_snapshot(p)['message_fingerprint'], self.before['message_fingerprint'])

    def test_existing_incoming_edit_changes_context(self):
        self.packet['messages'][0]['body'] = 'A different request.'
        self.assertNotEqual(snapshot.build_snapshot(self.packet)['message_fingerprint'], self.before['message_fingerprint'])

    def test_incomplete_or_unbound_inventory_cannot_hide_draft_message(self):
        mutations = [lambda p: p.pop('draft_inventory'),
                     lambda p: p['draft_inventory'].update(complete=False),
                     lambda p: p['draft_inventory'].update(inventory_id='different'),
                     lambda p: p['draft_inventory'].update(generation=2),
                     lambda p: p['thread_completeness'].update(complete=False),
                     lambda p: p['drafts'].clear(),
                     lambda p: p['drafts'][0].pop('message_id'),
                     lambda p: p['drafts'][0].update(message_id='another'),
                     lambda p: p['drafts'].append(dict(p['drafts'][0], id='d2'))]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                p = copy.deepcopy(self.packet)
                mutate(p)
                self.assertNotEqual(snapshot.build_snapshot(p)['message_fingerprint'], self.before['message_fingerprint'])

    def test_draft_thread_read_race_fails_closed(self):
        for key in ('body', 'subject'):
            p = copy.deepcopy(self.packet)
            p['messages'][1][key] = 'Changed since draft read'
            self.assertNotEqual(snapshot.build_snapshot(p)['message_fingerprint'], self.before['message_fingerprint'])

    def test_trigger_is_never_hidden(self):
        self.packet['trigger_id'] = 'm2'
        self.assertNotEqual(snapshot.build_snapshot(self.packet)['message_fingerprint'], self.before['message_fingerprint'])

    def test_modified_draft_still_changes_full_fingerprint_and_fails_exact_readback(self):
        old = snapshot.build_snapshot(self.packet)
        d = self.packet['drafts'][0]
        saved = dict(id='candidate', mailbox=d['from'], thread_id=d['thread_id'], subject=d['subject'],
                     body=d['body'], recipients=d['to'], cc=d['cc'], bcc=d['bcc'], reply_headers=d['headers'], attachments=[])
        d['body'] = self.packet['messages'][1]['body'] = 'Owner edited the draft.'
        actual = snapshot.build_snapshot(self.packet)
        self.assertEqual(actual['message_fingerprint'], old['message_fingerprint'])
        self.assertNotEqual(actual['content_fingerprint'], old['content_fingerprint'])
        self.assertNotEqual(actual['fingerprint'], old['fingerprint'])
        with self.assertRaisesRegex(ValueError, 'does not match'):
            _same_readback(saved, d, {'drafts': {}})



if __name__ == "__main__":
    unittest.main(verbosity=2)
