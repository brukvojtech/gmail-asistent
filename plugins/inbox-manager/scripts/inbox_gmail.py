"""Portable, offline Gmail decoding helpers. No provider I/O or credentials."""
import base64
import copy
import email.utils
import json


def cli_result(stdout, returncode=0):
    """Accept an empty successful mutation response; never swallow runtime errors."""
    if returncode:
        raise ValueError('CLI failed; inspect the privately saved stderr')
    value = json.loads(stdout) if stdout.strip() else {}
    if isinstance(value, dict) and (value.get('error') or value.get('errors')):
        raise ValueError('CLI returned an error; inspect the private response')
    return value


def headers(value):
    if isinstance(value, dict):
        return copy.deepcopy(value)
    result = {}
    for item in value:
        name, content = item['name'].lower(), item['value']
        if name in result:
            if name in {'from', 'to', 'cc', 'bcc', 'subject', 'message-id',
                        'in-reply-to', 'references', 'content-type'}:
                raise ValueError('Repeated identity/content header requires explicit review')
            prior = result[name]
            result[name] = (prior if isinstance(prior, list) else [prior]) + [content]
        else:
            result[name] = content
    return result


def message(raw):
    """Decode one Gmail format=full message, retaining original MIME evidence."""
    result = copy.deepcopy(raw)
    payload = result.get('payload', {})
    top = {k.lower(): v for k, v in headers(payload.get('headers', [])).items()}
    texts, attachments = [], []

    def walk(part):
        part['headers'] = headers(part.get('headers', []))
        mime = part.get('mimeType', '')
        body = part.get('body', {})
        if not part.get('filename') and mime in ('text/plain', 'text/html') and body.get('attachmentId'):
            raise ValueError('Fetch external text body before decoding a complete packet')
        if part.get('filename') or body.get('attachmentId'):
            attachments.append({'filename': part.get('filename', ''),
                                'mime_type': mime,
                                'attachment_id': body.get('attachmentId')})
        elif mime in ('text/plain', 'text/html') and body.get('data'):
            data = body['data']
            decoded = base64.b64decode(data + '=' * (-len(data) % 4),
                                       altchars=b'-_', validate=True)
            content_type = next((v for k, v in part['headers'].items()
                                 if k.lower() == 'content-type'), mime)
            from email.message import Message
            descriptor = Message()
            descriptor['Content-Type'] = content_type
            texts.append({'mime_type': mime,
                          'text': decoded.decode(descriptor.get_content_charset() or 'utf-8')})
        for child in part.get('parts', []):
            walk(child)

    walk(payload)
    plain = [item['text'] for item in texts if item['mime_type'] == 'text/plain']
    html = [item['text'] for item in texts if item['mime_type'] == 'text/html']
    result.update(id=raw['id'], thread_id=raw['threadId'], headers=top,
                  subject=top.get('subject', ''), body='\n'.join(plain or html),
                  decoded_mime=texts, attachments=attachments)
    result['from'] = email.utils.parseaddr(top.get('from', ''))[1]
    for name in ('to', 'cc', 'bcc'):
        result[name] = [address for _, address in
                        email.utils.getaddresses([top.get(name, '')]) if address.strip()]
    return result


def draft(raw):
    """Normalize a Gmail Draft resource for observation and revision contracts."""
    result = message(raw['message'])
    result['message_id'] = result['id']
    result['id'] = raw['id']
    mime = result.pop('payload')
    result['payload'] = {key: copy.deepcopy(result[key]) for key in
                         ('thread_id', 'body', 'subject', 'to', 'cc', 'bcc',
                          'headers', 'attachments')}
    result['provider_mime'] = mime
    # Observation remains available, but this writer has no lossless serializer
    # for alternative/HTML bodies or attachments. Never certify their revision.
    if (mime.get('mimeType') != 'text/plain' or mime.get('parts') or
            result['attachments']):
        result['revision_unsupported'] = 'Gmail revisions require one plain-text part without attachments'
    return result
