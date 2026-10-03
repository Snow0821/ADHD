from __future__ import annotations
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from recordbridge import BridgeError, RecordBridge, unwrap, validate_spec


class FakeTools:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail = None
        self.read_change = False
        self.owner = 'owner-a'

    def __call__(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == 'read_record' and args['id'] == 'anchor':
            return {'id': 'anchor', 'owner': self.owner, 'kind': 'decision', 'title': 'Existing user record', 'revision': 1, 'body': {'status': 'confirmed'}}
        if tool == 'search_records':
            data = [copy.deepcopy(r) for r in self.rows.values() if args['query'] in json.dumps(r)]
        elif tool == 'read_record':
            data = copy.deepcopy(self.rows[args['id']])
            if self.read_change:
                data['revision'] += 1
        else:
            if self.fail == 'before':
                raise BridgeError('Disconnected before result')
            if tool == 'create_record':
                identity = f'record-{len(self.rows) + 1}'
                revision = 1
            else:
                identity = args['id']
                if self.rows[identity]['revision'] != args['revision']:
                    return {'isError': True, 'content': [{'type': 'text', 'text': '{"error":"conflict"}'}]}
                revision = args['revision'] + 1
            data = {**copy.deepcopy(args), 'id': identity, 'revision': revision, 'owner': self.owner, 'updated_at': '2026-10-03T00:00:00Z'}
            self.rows[identity] = data
            if self.fail == 'after':
                raise BridgeError('Result lost after commit')
            if self.fail == 'badresult':
                return {'content': [{'type': 'text', 'text': '{}'}]}
        return {'content': [{'type': 'text', 'text': json.dumps(data)}], 'isError': False}


class RecordBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'private-state'
        self.tools = FakeTools()
        self.bridge = RecordBridge(self.path, 'verified-private-wiki', self.tools, expected_owner="owner-a", anchor_id="anchor")
        self.spec = {'key': 'project-example', 'kind': 'project', 'title': 'Example', 'body': {'state': 'waiting', 'source': 'User-approved project update'}}

    def tearDown(self):
        self.tmp.cleanup()

    def writes(self):
        return [c for c in self.tools.calls if c[0] in {'create_record', 'update_record'}]

    def test_create_requires_fresh_read(self):
        result = self.bridge.sync([self.spec])
        self.assertEqual(result[0]['action'], 'created')
        self.assertEqual([c[0] for c in self.tools.calls], ['read_record', 'search_records', 'create_record', 'read_record'])
        state = json.loads((self.path / 'records.json').read_text())
        self.assertEqual(state['records']['project-example']['status'], 'verified')

    def test_repeat_is_noop(self):
        self.bridge.sync([self.spec])
        result = self.bridge.sync([self.spec])
        self.assertEqual(result[0]['action'], 'unchanged')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.tools.rows), 1)

    def test_patch_preserves_unmentioned_fields(self):
        self.bridge.sync([self.spec])
        self.tools.rows['record-1']['body']['user_note'] = 'Keep this'
        changed = {**self.spec, 'body': {'state': 'done'}}
        result = self.bridge.sync([changed])
        body = self.tools.rows['record-1']['body']
        self.assertEqual(result[0]['revision'], 2)
        self.assertEqual(body['user_note'], 'Keep this')
        self.assertEqual(body['source'], self.spec['body']['source'])
        self.assertEqual(body['state'], 'done')

    def test_known_id_can_adopt_existing_record(self):
        self.tools.rows['old'] = {'id': 'old', 'owner': 'owner-a', 'kind': 'project', 'title': 'Example', 'revision': 4, 'body': {'note': 'Retain'}}
        result = self.bridge.sync([{**self.spec, 'id': 'old'}])
        self.assertEqual(result[0]['revision'], 5)
        self.assertEqual(self.tools.rows['old']['body']['note'], 'Retain')

    def test_two_input_keys_cannot_target_same_id(self):
        second = {**self.spec, 'key': 'other', 'id': 'old'}
        with self.assertRaises(BridgeError):
            self.bridge.sync([{**self.spec, 'id': 'old'}, second])
        self.assertFalse(self.tools.calls)

    def test_create_lost_response_reconciles_without_retry(self):
        self.tools.fail = 'after'
        with self.assertRaises(BridgeError):
            self.bridge.sync([self.spec])
        self.tools.fail = None
        result = self.bridge.sync([self.spec])
        self.assertEqual(result[0]['action'], 'recovered')
        self.assertEqual(len(self.writes()), 1)

    def test_create_not_found_does_not_replay(self):
        self.tools.fail = 'before'
        with self.assertRaises(BridgeError):
            self.bridge.sync([self.spec])
        self.tools.fail = None
        with self.assertRaisesRegex(BridgeError, 'never replay'):
            self.bridge.sync([self.spec])
        self.assertEqual(len(self.writes()), 1)

    def test_update_lost_response_reconciles_without_retry(self):
        self.bridge.sync([self.spec])
        changed = {**self.spec, 'body': {'state': 'done'}}
        self.tools.fail = 'after'
        with self.assertRaises(BridgeError):
            self.bridge.sync([changed])
        self.tools.fail = None
        self.assertEqual(self.bridge.sync([changed])[0]['action'], 'recovered')
        self.assertEqual(len(self.writes()), 2)

    def test_pending_different_request_is_blocked(self):
        self.tools.fail = 'before'
        with self.assertRaises(BridgeError):
            self.bridge.sync([self.spec])
        self.tools.fail = None
        with self.assertRaisesRegex(BridgeError, 'reconciliation'):
            self.bridge.sync([{**self.spec, 'title': 'Different'}])
        self.assertEqual(len(self.writes()), 1)

    def test_duplicate_remote_keys_are_blocked(self):
        self.bridge.sync([self.spec])
        self.tools.rows['record-2'] = {**copy.deepcopy(self.tools.rows['record-1']), 'id': 'record-2'}
        other = RecordBridge(Path(self.tmp.name) / 'other-state', 'verified-private-wiki', self.tools, expected_owner="owner-a", anchor_id="anchor")
        with self.assertRaisesRegex(BridgeError, 'Duplicate bridge keys'):
            other.sync([self.spec])
        self.assertEqual(len(self.writes()), 1)

    def test_truncated_search_blocks_create(self):
        row = {'id': 'a', 'owner': 'owner-a', 'kind': 'project', 'title': 'X', 'revision': 1, 'body': {}}
        original = self.tools.__call__
        self.bridge.call_tool = lambda tool, args: [row] * 200 if tool == 'search_records' else original(tool, args)
        with self.assertRaisesRegex(BridgeError, 'truncated'):
            self.bridge.sync([self.spec])
        self.assertFalse(self.writes())

    def test_changed_owner_blocks(self):
        self.bridge.sync([self.spec])
        self.tools.rows['record-1']['owner'] = 'owner-b'
        with self.assertRaisesRegex(BridgeError, 'Owner identity changed'):
            self.bridge.sync([self.spec])
        self.assertEqual(len(self.writes()), 1)

    def test_changed_destination_blocks_before_read(self):
        self.bridge.sync([self.spec])
        count = len(self.tools.calls)
        other = RecordBridge(self.path, 'different-private-wiki', self.tools, expected_owner="owner-a", anchor_id="anchor")
        with self.assertRaisesRegex(BridgeError, 'Destination differs'):
            other.sync([self.spec])
        self.assertEqual(len(self.tools.calls), count)

    def test_concurrent_readback_change_is_not_verified(self):
        self.tools.read_change = True
        with self.assertRaisesRegex(BridgeError, 'changed during read-back'):
            self.bridge.sync([self.spec])
        state = json.loads((self.path / 'records.json').read_text())
        self.assertEqual(state['records']['project-example']['status'], 'pending')

    def test_stale_revision_is_not_overwritten(self):
        self.bridge.sync([self.spec])
        original = self.tools.__call__
        def race(tool, args):
            if tool == 'update_record':
                self.tools.rows['record-1']['revision'] += 1
                self.tools.rows['record-1']['body']['user_note'] = 'Concurrent edit'
            return original(tool, args)
        self.bridge.call_tool = race
        with self.assertRaises(BridgeError):
            self.bridge.sync([{**self.spec, 'body': {'state': 'done'}}])
        self.assertEqual(self.tools.rows['record-1']['body']['state'], 'waiting')
        self.assertEqual(self.tools.rows['record-1']['body']['user_note'], 'Concurrent edit')

    def test_wrong_record_kind_cannot_be_changed(self):
        self.bridge.sync([self.spec])
        with self.assertRaisesRegex(BridgeError, 'kind'):
            self.bridge.sync([{**self.spec, 'kind': 'task', 'body': {'status': 'todo'}}])
        self.assertEqual(len(self.writes()), 1)

    def test_decision_requires_explicit_status(self):
        with self.assertRaisesRegex(BridgeError, 'proposed or confirmed'):
            self.bridge.sync([{**self.spec, 'kind': 'decision'}])
        self.assertFalse(self.writes())

    def test_event_requires_timezone_and_order(self):
        for start, end in [('2026-10-03T10:00:00', '2026-10-03T11:00:00'), ('2026-10-03T12:00:00+09:00', '2026-10-03T11:00:00+09:00')]:
            with self.assertRaises(BridgeError):
                self.bridge.sync([{**self.spec, 'kind': 'event', 'body': {'start': start, 'end': end}}])
        self.assertFalse(self.writes())

    def test_reserves_metadata_and_rejects_nested_or_nonfinite_body(self):
        for body in ({'bridge_key': 'spoof'}, {'nested': []}, {'number': float('nan')}, {'number': 2**60}, {'long': 'x'*6001}):
            with self.assertRaises(BridgeError):
                validate_spec({**self.spec, 'body': body})

    def test_native_and_mcp_results_and_error_envelopes(self):
        self.assertEqual(unwrap([1]), [1])
        self.assertEqual(unwrap({'content': [{'type': 'text', 'text': '[1]'}]}), [1])
        for value in ({'isError': True}, {'error': 'blocked'}, {'content': []}, {'content': [{'type': 'text', 'text': 'bad'}]}):
            with self.assertRaises(BridgeError):
                unwrap(value)

    def test_malformed_write_result_keeps_pending(self):
        self.tools.fail = 'badresult'
        with self.assertRaises(BridgeError):
            self.bridge.sync([self.spec])
        state = json.loads((self.path / 'records.json').read_text())
        self.assertIn('pending', state['records']['project-example'])

    def test_separate_runtime_does_not_touch_core_stores(self):
        core = Path(self.tmp.name) / '.adhd'
        core.mkdir()
        sentinel = core / 'state.yaml'
        sentinel.write_text('schema: 2\nactive: 17\n')
        self.bridge.sync([self.spec])
        self.assertEqual(sentinel.read_text(), 'schema: 2\nactive: 17\n')


if __name__ == '__main__':
    unittest.main()
