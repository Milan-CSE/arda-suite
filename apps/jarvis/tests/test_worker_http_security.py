import pytest
from arda.worker_http import HttpWorkerTransport, WorkerHTTPServer
from arda.core import Database

@pytest.mark.parametrize('url', ['http://192.168.1.2:8765', 'http://example.org', 'http://127.0.0.1@example.org'])
def test_cleartext_remote_endpoint_rejected(url):
    with pytest.raises(ValueError):
        HttpWorkerTransport(url)


def test_remote_listener_rejected(tmp_path):
    db = Database(tmp_path / 'core.sqlite3')
    try:
        with pytest.raises(ValueError):
            WorkerHTTPServer(db, host='192.168.1.2')
    finally:
        db.close()


def test_private_interface_requires_explicit_opt_in(tmp_path):
    db = Database(tmp_path / 'core.sqlite3')
    try:
        with pytest.raises(ValueError):
            WorkerHTTPServer(db, host='100.64.0.2')
        server = WorkerHTTPServer(db, host='100.64.0.2', allow_private_http=True)
        assert server.host == '100.64.0.2'
        assert HttpWorkerTransport(
            'http://100.64.0.2:8765', allow_private_http=True
        ).base_url == 'http://100.64.0.2:8765'
    finally:
        db.close()

@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1])
def test_invalid_timeouts_rejected(value):
    with pytest.raises(ValueError):
        HttpWorkerTransport('http://127.0.0.1', timeout_seconds=value)


def test_invalid_artifact_limit_rejected():
    with pytest.raises(ValueError):
        HttpWorkerTransport('http://127.0.0.1', max_artifact_bytes=0)


def test_malformed_response_preserves_transport_error(monkeypatch):
    import io
    from arda.worker_http import ProtocolTransportError
    from arda.worker_protocol import ProtocolRequest
    class Response(io.BytesIO):
        headers = {}
    transport = HttpWorkerTransport('http://127.0.0.1', max_retries=0)
    monkeypatch.setattr(transport._opener, 'open', lambda *a, **kw: Response(b'not-json'))
    with pytest.raises(ProtocolTransportError) as error:
        transport.send(ProtocolRequest.create('mac', 'token', 'ping'))
    assert error.value.status_code == 502


@pytest.mark.parametrize('host', ['0.0.0.0', '::', '224.0.0.1', '255.255.255.255'])
def test_private_opt_in_does_not_admit_wildcard_or_special_addresses(tmp_path, host):
    db = Database(tmp_path / 'core.sqlite3')
    try:
        with pytest.raises(ValueError):
            WorkerHTTPServer(db, host=host, allow_private_http=True)
    finally:
        db.close()


@pytest.mark.parametrize("location", ["envelope", "result"])
def test_duplicate_response_keys_rejected_even_with_valid_collapsed_signature(monkeypatch, location):
    import hashlib
    import hmac
    import io
    import json
    from arda.worker_http import ProtocolTransportError, _response_canonical
    from arda.worker_protocol import ProtocolRequest

    request = ProtocolRequest.create("mac", "token", "ping", request_id="response-duplicate")
    result = {"status": "ok"}
    signature = hmac.new(b"token", _response_canonical(request.request_id, "mac", result),
                         hashlib.sha256).hexdigest()
    body = json.dumps({"request_id": request.request_id, "worker_id": "mac",
                       "result": result, "signature": signature}, separators=(",", ":"))
    if location == "envelope":
        body = '{"request_id":"discarded",' + body[1:]
    else:
        body = body.replace('"result":{"status":"ok"}',
                            '"result":{"status":"bad","status":"ok"}')

    class Response(io.BytesIO):
        headers = {}

    transport = HttpWorkerTransport("http://127.0.0.1", max_retries=0)
    monkeypatch.setattr(transport._opener, "open", lambda *a, **kw: Response(body.encode()))
    with pytest.raises(ProtocolTransportError) as error:
        transport.send(request)
    assert error.value.status_code == 502
