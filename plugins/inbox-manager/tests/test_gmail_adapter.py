import base64
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inbox_gmail as adapter
import inbox_snapshot as snapshot

class GmailAdapterTests(unittest.TestCase):
    def test_full_message_and_nested_headers(self):
        raw = {'id': 'm1', 'threadId': 't1', 'payload': {
            'headers': [{'name': 'From', 'value': 'a@example.com'},
                        {'name': 'To', 'value': 'b@example.com'}],
            'parts': [{'mimeType': 'multipart/alternative', 'headers': [], 'parts': [
                {'mimeType': 'text/plain', 'headers': [], 'body': {'data':
                 base64.urlsafe_b64encode('Příliš'.encode()).decode()}}]}]}}
        original = copy.deepcopy(raw)
        result = adapter.message(raw)
        self.assertEqual(result['body'], 'Příliš')
        self.assertEqual(result['cc'], [])
        self.assertEqual(result['to'], ['b@example.com'])
        self.assertEqual(raw, original)
        snapshot.normalize_item(result)
        self.assertIsInstance(result['payload']['parts'][0]['parts'][0]['headers'], dict)

    def test_empty_mutation_and_runtime_errors(self):
        self.assertEqual(adapter.cli_result(''), {})
        for response, code in [('{}', 1), ('{"errors":["failed"]}', 0),
                               ('{"error":{"message":"failed"}}', 0)]:
            with self.assertRaises(ValueError): adapter.cli_result(response, code)

    def test_ambiguous_headers_fail_closed(self):
        with self.assertRaises(ValueError):
            adapter.headers([{'name': 'To', 'value': 'a@example.com'},
                             {'name': 'to', 'value': 'b@example.com'}])

    def test_external_text_body_requires_fetch(self):
        with self.assertRaises(ValueError):
            adapter.message({'id': 'm', 'threadId': 't', 'payload': {
                'mimeType': 'text/plain', 'body': {'attachmentId': 'external'}}})

    def test_declared_charset_and_nested_attachment(self):
        result = adapter.message({'id': 'm', 'threadId': 't', 'payload': {
            'parts': [{'mimeType': 'text/plain', 'headers': [
                {'name': 'Content-Type', 'value': 'text/plain; charset=iso-8859-1'}],
                'body': {'data': base64.urlsafe_b64encode(b'caf\xe9').decode()}},
                {'parts': [{'filename': 'document.pdf', 'mimeType': 'application/pdf',
                            'body': {'attachmentId': 'file-1'}}]}]}})
        self.assertEqual(result['body'], 'café')
        self.assertEqual(result['attachments'][0]['attachment_id'], 'file-1')

    def test_repeated_transport_headers_survive(self):
        self.assertEqual(adapter.headers([
            {'name': 'Received', 'value': 'hop one'},
            {'name': 'Received', 'value': 'hop two'}]),
            {'received': ['hop one', 'hop two']})

    def test_complete_draft_revision_contract(self):
        import inbox_revisions
        result = adapter.draft({'id': 'draft-1', 'message': {
            'id': 'message-1', 'threadId': 'thread-1', 'payload': {'headers': [
                {'name': 'From', 'value': 'a@example.com'},
                {'name': 'To', 'value': 'b@example.com'}],
                'mimeType': 'text/plain', 'body': {'data': 'aGVsbG8='}}}})
        self.assertEqual(result['id'], 'draft-1')
        self.assertEqual(result['message_id'], 'message-1')
        self.assertEqual(inbox_revisions._draft_content(result)['body'], 'hello')

    def test_draft_revision_readback_round_trip(self):
        import inbox_revisions as revisions
        def resource(body):
            return {'id': 'd1', 'message': {'id': 'm1', 'threadId': 't1', 'payload': {
                'headers': [{'name': 'From', 'value': 'a@example.com'},
                            {'name': 'To', 'value': 'b@example.com'}],
                'mimeType': 'text/plain', 'body': {'data':
                    base64.urlsafe_b64encode(body.encode()).decode()}}}}
        before = revisions._draft_content(adapter.draft(resource('hello')))
        expected = copy.deepcopy(before)
        expected['body'] = 'updated'
        expected['payload'] = revisions._replacement_payload(before, expected)
        readback = revisions._draft_content(adapter.draft(resource('updated')))
        self.assertEqual(revisions._content_mismatches(expected, readback), [])
        self.assertIn('provider_mime', adapter.draft(resource('hello')))


class ComplexDraftHoldTests(unittest.TestCase):
    def resource(self, html):
        return {'id': 'd1', 'message': {'id': 'm1', 'threadId': 't1', 'payload': {
            'mimeType': 'multipart/alternative',
            'headers': [{'name': 'From', 'value': 'owner@example.test'},
                        {'name': 'To', 'value': 'client@example.test'}],
            'parts': [{'mimeType': 'text/plain', 'body': {'data': 'c2FtZQ=='}},
                      {'mimeType': 'text/html', 'body': {'data':
                       base64.urlsafe_b64encode(html.encode()).decode()}}]}}}

    def test_changed_html_cannot_be_certified_as_equal_plaintext(self):
        import inbox_revisions as revisions
        before = adapter.draft(self.resource('<a href="https://example.test/a">same</a>'))
        changed = adapter.draft(self.resource('<a href="https://example.test/b">same</a>'))
        self.assertEqual(before['body'], changed['body'])
        self.assertNotEqual(before['provider_mime'], changed['provider_mime'])
        for observed in (before, changed):
            with self.assertRaisesRegex(revisions.RevisionError, 'revision unsupported'):
                revisions._draft_content(observed)

    def test_attachment_draft_stays_observable_but_cannot_be_revised(self):
        import inbox_revisions as revisions
        raw = self.resource('same')
        raw['message']['payload']['parts'].append({
            'mimeType': 'application/pdf', 'filename': 'synthetic.pdf',
            'body': {'attachmentId': 'synthetic-attachment'}})
        observed = adapter.draft(raw)
        self.assertEqual(observed['attachments'][0]['filename'], 'synthetic.pdf')
        with self.assertRaisesRegex(revisions.RevisionError, 'revision unsupported'):
            revisions._draft_content(observed)


if __name__ == '__main__':
    unittest.main()
