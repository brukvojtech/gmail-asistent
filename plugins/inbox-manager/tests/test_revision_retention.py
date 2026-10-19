"""Offline retention checks using synthetic in-memory revision records."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inbox_engine as engine
import inbox_maintenance as maintenance

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def state_for(status, age=91):
    return {
        'config': {'learning': {'retention_days': 90}},
        'maintenance': {'collection': maintenance.COLLECTIONS.index('draft_revisions'),
                        'after': '', 'cycles': 0},
        'actions': {}, 'drafts': {},
        'draft_lineages': {'lineage': {'managed_candidate_id': 'candidate'}},
        'draft_revisions': {'revision': {
            'id': 'revision', 'lineage_id': 'lineage', 'status': status,
            'created_at': (NOW - timedelta(days=age)).isoformat(),
            'body': 'Synthetic reply.',
            'review_packet': {'messages': [{'body': 'Synthetic incoming message.'}]},
            'checked_content': {'body': 'Synthetic reply.', 'payload': {'mime': 'synthetic'}},
            'review_owner_answer': {'answer': 'Synthetic owner instruction.'},
            'draft_id': 'draft', 'hash': 'synthetic-audit-digest',
        }},
    }


class RevisionRetentionTests(unittest.TestCase):
    def run_maintenance(self, state):
        with patch.object(engine, 'utcnow', return_value=NOW):
            return maintenance.maintain(state)

    def test_expired_content_pruned_and_audit_identity_preserved(self):
        for status in ('confirmed', 'ready', 'rejected', 'retired'):
            with self.subTest(status=status):
                state = state_for(status)
                self.run_maintenance(state)
                row = state['draft_revisions']['revision']
                for key in ('body', 'review_packet', 'checked_content', 'review_owner_answer'):
                    self.assertIsNone(row[key])
                self.assertEqual(row['draft_id'], 'draft')
                self.assertEqual(row['hash'], 'synthetic-audit-digest')
                self.assertEqual(row['status'], 'expired' if status == 'ready' else status)

    def test_unresolved_revision_keeps_reconciliation_evidence(self):
        for status in ('reserved', 'attempting', 'updating', 'unknown'):
            with self.subTest(status=status):
                state = state_for(status)
                before = copy.deepcopy(state['draft_revisions'])
                self.run_maintenance(state)
                self.assertEqual(state['draft_revisions'], before)

    def test_recent_and_exact_boundary_content_preserved(self):
        for age in (0, 89, 90):
            with self.subTest(age=age):
                state = state_for('confirmed', age)
                before = copy.deepcopy(state['draft_revisions'])
                self.run_maintenance(state)
                self.assertEqual(state['draft_revisions'], before)

    def test_configured_retention_period_is_respected(self):
        state = state_for('confirmed', age=31)
        state['config']['learning']['retention_days'] = 30
        self.run_maintenance(state)
        self.assertIsNone(state['draft_revisions']['revision']['review_packet'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
