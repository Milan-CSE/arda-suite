"""M2.2 loopback endpoint and fixed artifact probe (no command execution)."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import threading

from arda.core import Database
from arda.worker_http import HttpWorkerTransport, WorkerHTTPServer
from arda.worker_protocol import WorkerProtocolClient


def smoke(
    url: str,
    worker_id: str,
    token: str,
    fixture_job_id: str,
    run_id: str,
    *,
    allow_private_http: bool = False,
) -> dict:
    """Use a dedicated fixture DB; run_id must be persisted and reused after failure."""
    client = WorkerProtocolClient(
        worker_id, token, HttpWorkerTransport(url, allow_private_http=allow_private_http)
    )
    results = {}

    def request(kind, payload=None):
        result = client.request(kind, payload, request_id=f'{run_id}:{kind}')
        results[kind] = result
        return result

    request('ping')
    claim = request('claim')
    if claim['job_id'] != fixture_job_id:
        raise ValueError('claim did not return the explicit fixture job; use an isolated fixture DB')
    lease = {'job_id': fixture_job_id, 'lease_token': claim['lease_token']}
    request('start', lease)
    request('heartbeat', lease)
    content = b'{"probe":"m2.2-transport","executed_commands":0}'
    request('upload_artifact', {
        **lease, 'kind': 'transport-probe.json', 'path_or_ref': 'transport-probe.json',
        'content_b64': base64.b64encode(content).decode('ascii'),
        'sha256': hashlib.sha256(content).hexdigest(),
    })
    request('complete', {**lease, 'result': {'transport_probe': True}})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--db', required=True)
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--allow-private-http', action='store_true')
    probe = commands.add_parser('smoke')
    probe.add_argument('--url', default='http://127.0.0.1:8765')
    probe.add_argument('--worker-id', required=True)
    probe.add_argument('--fixture-job-id', required=True)
    probe.add_argument('--run-id', required=True)
    probe.add_argument('--allow-private-http', action='store_true')
    args = parser.parse_args()
    if args.command == 'smoke':
        token = os.environ.get('ARDA_WORKER_TOKEN')
        if not token:
            parser.error('ARDA_WORKER_TOKEN must be set (never pass the token in argv)')
        result = smoke(
            args.url, args.worker_id, token, args.fixture_job_id, args.run_id,
            allow_private_http=args.allow_private_http,
        )
        # Do not print lease tokens or credential envelopes.
        print(json.dumps({kind: {k: v for k, v in value.items() if k != 'lease_token'}
                          for kind, value in result.items()}, sort_keys=True))
        return
    db = Database(args.db)
    stopped = threading.Event()
    server = WorkerHTTPServer(
        db, host=args.host, port=args.port, allow_private_http=args.allow_private_http
    )
    try:
        db.migrate()
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        server.start()
        print(json.dumps({
            'endpoint': server.url,
            'mode': 'private-http' if args.allow_private_http else 'loopback-ssh-tunnel',
        }), flush=True)
        stopped.wait()
    finally:
        server.stop()
        db.close()


if __name__ == '__main__':
    main()
