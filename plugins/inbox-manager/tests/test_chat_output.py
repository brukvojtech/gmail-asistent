"""Synthetic approved-profile and exact scheduler prompt regressions."""
import copy
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inbox_engine as engine
import inbox_cycle as cycle
import inbox_registry as registry
import test_self_notification as fixture


class ChatOutputTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.CycleRegression('test_no_reply_intake_clears_queue_without_draft_or_send')
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        cycle.execute(self.fx.case_home, 'finish', self.fx.auth)
        self.root = self.fx.case_home
        self.home = self.root / 'mailbox'
        self.home.mkdir()
        for item in list(self.root.iterdir()):
            if item != self.home:
                item.rename(self.home / item.name)

    def current(self):
        with engine.transaction(self.home, readonly=True) as (_, box):
            return copy.deepcopy(box[0])

    def configure(self, mode, include_file=True):
        state = self.current()
        config = state['config']
        config['chat_output'] = mode
        if include_file:
            target = self.home / 'chat-preferences.md'
            target.write_text('Approved focused mode. One email at a time.\n')
            config['profile_files'][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        engine.execute(self.home, 'configure', {
            'owner_confirmed': True, 'approval_ref': 'offline-chat-output-consent',
            'base_version': state['version'], 'config': config})

    def observation(self):
        return {'provider': 'gmail', 'mailbox': fixture.MAILBOX, 'account_id': 'synthetic-account',
                'connector_ref': 'synthetic-gws', 'card_ref': 'offline-test',
                'observed_at': registry.stamp(), 'evidence_ref': 'synthetic-profile-read'}

    def scheduled_round_trip(self):
        observation = self.observation()
        registry.execute(self.root, 'register', {
            'owner_confirmed': True, 'approval_ref': 'synthetic-register',
            'home': str(self.home), 'provider_observation': observation,
            **{k: observation[k] for k in ('provider', 'mailbox', 'account_id', 'connector_ref', 'card_ref')}})
        task = {'task_id': 'synthetic-task', 'cadence': 'daily', 'timezone': 'UTC',
                'execution_location': 'local', 'host_required': True, 'kind': 'cron',
                'enabled': False, 'prompt': 'setup', 'observed_at': registry.stamp(),
                'evidence_ref': 'synthetic-task-read'}
        def bind():
            return registry.execute(self.root, 'schedule', {
                'owner_confirmed': True, 'approval_ref': 'synthetic-schedule',
                'mailbox': fixture.MAILBOX, 'grace_seconds': 60,
                **{k: task[k] for k in ('task_id', 'cadence', 'timezone', 'execution_location',
                                       'host_required', 'kind', 'enabled')},
                'verified_at': task['observed_at'], 'evidence_ref': task['evidence_ref'],
                'scheduler_readback': task})
        bound = bind()
        task.update(prompt=bound['invocation_prompt'], enabled=True)
        self.assertEqual(bind()['binding_id'], bound['binding_id'])
        selected = {'mailbox': fixture.MAILBOX, 'task_id': task['task_id'],
                    'binding_id': bound['binding_id']}
        run_id = 'synthetic-scheduled-run-' + str(self.current()['version'])
        registry.execute(self.root, 'dispatch', {**selected, 'run_id': run_id,
                         'provider_observation': self.observation(), 'scheduler_readback': task})
        registry.execute(self.root, 'complete', {**selected, 'run_id': run_id,
                         'result': 'incomplete', 'evidence_ref': 'synthetic-finish',
                         'scheduler_readback': task})
        wrong = dict(task, prompt=task['prompt'] + ' unapproved edit')
        with self.assertRaisesRegex(registry.RegistryError, 'prompt does not match'):
            registry.execute(self.root, 'dispatch', {**selected, 'run_id': 'synthetic-other-run',
                             'provider_observation': self.observation(), 'scheduler_readback': wrong})
        self.task, self.selected = task, selected
        return task['prompt']

    def test_legacy_schedule_still_dispatches_and_finishes(self):
        prompt = self.scheduled_round_trip()
        self.assertIn('Finish every run with the complete rendered inbox dashboard', prompt)
        self.assertNotIn('chat-preferences.md', prompt)

    def test_focused_approved_schedule_dispatches_and_finishes(self):
        self.configure('profile')
        prompt = self.scheduled_round_trip()
        self.assertIn('hash-bound chat-preferences.md', prompt)
        self.assertNotIn('The owner explicitly requests a periodic full report', prompt)

    def pause_existing(self):
        self.task['enabled'] = False
        registry.execute(self.root, 'pause', {
            **self.selected, 'owner_confirmed': True, 'approval_ref': 'synthetic-pause',
            'owner_receipt': {'authenticated': True,
                              'receipt_id': 'pause-' + str(self.current()['version']),
                              'observed_at': registry.stamp()},
            'scheduler_readback': self.task})

    def test_existing_schedule_adopts_profile_then_can_return_to_full(self):
        self.scheduled_round_trip()
        self.pause_existing()
        self.configure('profile')
        self.assertIn('hash-bound chat-preferences.md', self.scheduled_round_trip())
        self.pause_existing()
        self.configure('full')
        self.assertIn('complete rendered inbox dashboard', self.scheduled_round_trip())

    def test_profile_mode_requires_hashed_preference(self):
        with self.assertRaisesRegex(ValueError, 'requires approved chat-preferences'):
            self.configure('profile', include_file=False)

    def test_unapproved_file_edit_blocks_processing(self):
        self.configure('profile')
        (self.home / 'chat-preferences.md').write_text('Changed without configuration approval')
        with self.assertRaisesRegex(ValueError, 'profile file changed'):
            cycle.execute(self.home, 'begin', {'mailbox': fixture.MAILBOX})

    def test_invalid_mode_rejected(self):
        with self.assertRaisesRegex(ValueError, 'chat_output must be'):
            self.configure('silent-send')


if __name__ == '__main__':
    unittest.main()
