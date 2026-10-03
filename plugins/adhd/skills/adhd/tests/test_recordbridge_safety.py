from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_recordbridge import FakeTools
from recordbridge import BridgeError, RecordBridge, unwrap, validate_spec


class RecordBridgeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'private-state'
        self.tools = FakeTools()
        self.bridge = RecordBridge(
            self.path, 'verified-private-wiki', self.tools,
            expected_owner='owner-a', anchor_id='anchor',
        )
        self.spec = {
            'key': 'approved-project', 'kind': 'project', 'title': 'Project',
            'body': {'note': 'Only the intended owner may receive this'},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_switch_with_empty_search_cannot_transmit_new_patch(self):
        self.bridge.sync([self.spec])
        self.tools.calls.clear()
        self.tools.rows.clear()
        self.tools.owner = 'owner-b'
        with self.assertRaisesRegex(BridgeError, 'Owner identity changed'):
            self.bridge.sync([{**self.spec, 'key': 'another-project'}])
        self.assertEqual(self.tools.calls, [('read_record', {'id': 'anchor'})])
        self.assertFalse(self.tools.rows)
        state = json.loads((self.path / 'records.json').read_text())
        self.assertEqual(state['owner'], 'owner-a')
        self.assertNotIn('another-project', state['records'])

    def test_wrong_initial_owner_cannot_transmit_search_or_patch(self):
        self.tools.owner = 'owner-b'
        with self.assertRaisesRegex(BridgeError, 'Owner identity changed'):
            self.bridge.sync([self.spec])
        self.assertEqual(self.tools.calls, [('read_record', {'id': 'anchor'})])
        self.assertFalse(self.tools.rows)

    def test_changed_anchor_binding_blocks_before_any_connector_call(self):
        self.bridge.sync([self.spec])
        self.tools.calls.clear()
        other = RecordBridge(
            self.path, 'verified-private-wiki', self.tools,
            expected_owner='owner-a', anchor_id='another-anchor',
        )
        with self.assertRaisesRegex(BridgeError, 'Anchor differs'):
            other.sync([self.spec])
        self.assertFalse(self.tools.calls)

    def test_changed_expected_owner_blocks_before_any_connector_call(self):
        self.bridge.sync([self.spec])
        self.tools.calls.clear()
        other = RecordBridge(
            self.path, 'verified-private-wiki', self.tools,
            expected_owner='owner-b', anchor_id='anchor',
        )
        with self.assertRaisesRegex(BridgeError, 'Expected owner differs'):
            other.sync([self.spec])
        self.assertFalse(self.tools.calls)

    def test_missing_anchor_cannot_transmit_search_or_patch(self):
        calls = []

        def missing_anchor(tool, arguments):
            calls.append((tool, arguments))
            return {'isError': True, 'content': [{'type': 'text', 'text': '{"error":"not found"}'}]}

        self.bridge.call_tool = missing_anchor
        with self.assertRaises(BridgeError):
            self.bridge.sync([self.spec])
        self.assertEqual(calls, [('read_record', {'id': 'anchor'})])

    def test_malformed_mcp_blocks_fail_with_bridge_error(self):
        for blocks in ([None], [0], ['not an object'], [{}], [{'type': 'text'}],
                       [{'type': 'text', 'text': None}], {'type': 'text'}):
            with self.subTest(blocks=blocks):
                with self.assertRaises(BridgeError):
                    unwrap({'content': blocks})

    def test_unhashable_kind_is_rejected_before_connector_calls(self):
        for kind in ([], {}):
            with self.subTest(kind=kind):
                with self.assertRaises(BridgeError):
                    self.bridge.sync([{**self.spec, 'kind': kind}])
        self.assertFalse(self.tools.calls)

    def test_oversized_integer_is_rejected_without_overflow(self):
        with self.assertRaises(BridgeError):
            self.bridge.sync([{**self.spec, 'body': {'number': 10**400}}])
        self.assertFalse(self.tools.calls)

    def test_utf16_title_and_body_limits_match_remote(self):
        for spec in ({**self.spec, 'title': '\U0001f600' * 101},
                     {**self.spec, 'body': {'note': '\U0001f600' * 3001}}):
            with self.subTest(field='title' if spec['title'] != self.spec['title'] else 'body'):
                with self.assertRaises(BridgeError):
                    self.bridge.sync([spec])
        self.assertFalse(self.tools.calls)
        validate_spec({**self.spec, 'title': '\U0001f600' * 100,
                       'body': {'note': '\U0001f600' * 3000}})

    def test_non_http_source_schemes_are_rejected_before_connector_calls(self):
        for source in ('ftp://example.invalid/file', 'javascript:alert(1)'):
            with self.subTest(source=source):
                with self.assertRaises(BridgeError):
                    self.bridge.sync([{**self.spec, 'body': {'source': source}}])
        self.assertFalse(self.tools.calls)

    def test_python_only_event_formats_do_not_create_pending_or_write(self):
        for start, end in (
            ('2026-W40-6T10:00:00+00:00', '2026-W40-6T11:00:00+00:00'),
            ('20261003T10:00:00+00:00', '20261003T11:00:00+00:00'),
        ):
            with self.subTest(start=start):
                with self.assertRaises(BridgeError):
                    self.bridge.sync([{**self.spec, 'kind': 'event',
                                       'body': {'start': start, 'end': end}}])
        self.assertFalse([call for call in self.tools.calls
                          if call[0] in {'create_record', 'update_record'}])
        state_path = self.path / 'records.json'
        if state_path.exists():
            self.assertFalse(json.loads(state_path.read_text())['records'])

    def make_update_conflict(self):
        self.bridge.sync([self.spec])
        original = self.tools.__call__

        def concurrent_edit(tool, arguments):
            if tool == 'update_record':
                self.tools.rows[arguments['id']]['revision'] += 1
                self.tools.rows[arguments['id']]['body']['concurrent_note'] = 'Keep the newer user edit'
            return original(tool, arguments)

        self.bridge.call_tool = concurrent_edit
        changed = {**self.spec, 'body': {'state': 'done'}}
        with self.assertRaises(BridgeError):
            self.bridge.sync([changed])
        self.bridge.call_tool = self.tools
        return changed

    def test_conflict_reconciliation_archives_and_preserves_newer_fields(self):
        changed = self.make_update_conflict()
        pending = json.loads((self.path / 'records.json').read_text())['records'][self.spec['key']]['pending']
        self.tools.calls.clear()
        result = self.bridge.reconcile(self.spec['key'], 'record-1', 2, 'Reviewed the concurrent user edit')
        self.assertEqual(result['status'], 'reconciled')
        self.assertFalse(result['previous_write_verified'])
        self.assertTrue(all(tool == 'read_record' for tool, _ in self.tools.calls))
        archive = json.loads((self.path / result['archive']).read_text())
        self.assertEqual(archive['pending'], pending)
        self.assertEqual(archive['observed']['revision'], 2)
        self.assertEqual(archive['observed']['body']['concurrent_note'], 'Keep the newer user edit')
        self.assertEqual(self.bridge.sync([changed])[0]['revision'], 3)
        self.assertEqual(self.tools.rows['record-1']['body']['state'], 'done')
        self.assertEqual(self.tools.rows['record-1']['body']['concurrent_note'], 'Keep the newer user edit')
        self.assertTrue((self.path / result['archive']).exists())

    def test_stale_reconciliation_revision_retains_pending_without_write(self):
        self.make_update_conflict()
        before = json.loads((self.path / 'records.json').read_text())
        self.tools.calls.clear()
        with self.assertRaisesRegex(BridgeError, 'revision changed'):
            self.bridge.reconcile(self.spec['key'], 'record-1', 1, 'Reviewed an obsolete revision')
        self.assertEqual(json.loads((self.path / 'records.json').read_text()), before)
        self.assertFalse(list(self.path.glob('reconciled-*.json')))
        self.assertTrue(all(tool == 'read_record' for tool, _ in self.tools.calls))

    def test_reconciliation_cannot_rebind_pending_update_to_other_record(self):
        self.make_update_conflict()
        self.tools.calls.clear()
        with self.assertRaisesRegex(BridgeError, 'different record'):
            self.bridge.reconcile(self.spec['key'], 'another-record', 2, 'Attempted a different target')
        self.assertEqual(self.tools.calls, [('read_record', {'id': 'anchor'})])
        self.assertFalse(list(self.path.glob('reconciled-*.json')))

    def test_absent_uncertain_create_cannot_be_reconciled_or_replayed(self):
        self.tools.fail = 'before'
        with self.assertRaises(BridgeError):
            self.bridge.sync([self.spec])
        self.tools.fail = None
        before = json.loads((self.path / 'records.json').read_text())
        self.tools.calls.clear()
        with self.assertRaisesRegex(BridgeError, 'exactly one existing'):
            self.bridge.reconcile(self.spec['key'], 'missing-record', 1, 'No committed record was found')
        self.assertEqual(json.loads((self.path / 'records.json').read_text()), before)
        self.assertFalse(list(self.path.glob('reconciled-*.json')))
        self.assertFalse([call for call in self.tools.calls
                          if call[0] in {'create_record', 'update_record'}])

    def test_reconciliation_checks_owner_before_search_or_record_payload(self):
        self.make_update_conflict()
        self.tools.owner = 'owner-b'
        self.tools.calls.clear()
        with self.assertRaisesRegex(BridgeError, 'Owner identity changed'):
            self.bridge.reconcile(self.spec['key'], 'record-1', 2, 'Review cannot override the owner binding')
        self.assertEqual(self.tools.calls, [('read_record', {'id': 'anchor'})])
        self.assertFalse(list(self.path.glob('reconciled-*.json')))


if __name__ == '__main__':
    unittest.main()
