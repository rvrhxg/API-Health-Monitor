"""Small synchronous HTTP availability probe. No external runtime dependencies."""
from __future__ import annotations
import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Result:
    service: str
    ok: bool
    status: int | None
    error: str | None
    attempts: int
    response_ms: float
    total_ms: float
    checked_at: str


def validate_url(url: str) -> str:
    value = urlsplit(url)
    if value.scheme not in {'http', 'https'} or not value.hostname or value.username or value.password or value.fragment:
        raise ValueError('Use an HTTP(S) URL without credentials or fragment')
    try:
        value.port
    except ValueError:
        raise ValueError('Invalid port') from None
    if any(ord(c) < 33 for c in url):
        raise ValueError('URL contains whitespace or control characters')
    return url


def fetch_status(url: str, timeout: float) -> int:
    # Headers only: do not read potentially large or sensitive response bodies.
    try:
        with build_opener(NoRedirect()).open(url, timeout=timeout) as response:
            return response.status
    except HTTPError as error:
        status = error.code
        error.close()
        return status


def check(url: str, *, service: str = 'service', timeout: float = 3,
          retries: int = 2, backoff: float = .2, transport=fetch_status,
          sleep=time.sleep, clock=time.perf_counter, emit=None) -> Result:
    validate_url(url)
    if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= 5:
        raise ValueError('Retries must be 0..5')
    if not math.isfinite(timeout) or not 0 < timeout <= 60 or not math.isfinite(backoff) or not 0 <= backoff <= 5:
        raise ValueError('Invalid timeout/backoff')
    if not service or len(service) > 80 or any(ord(c) < 32 for c in service):
        raise ValueError('Invalid service label')
    begin = clock()
    for attempt in range(1, retries + 2):
        start = clock()
        status, error = None, None
        try:
            status = transport(url, timeout)
        except (TimeoutError, socket.timeout):
            error = 'timeout'
        except URLError as failure:
            error = 'timeout' if isinstance(failure.reason, (TimeoutError, socket.timeout)) else 'connection_error'
        except OSError:
            error = 'connection_error'
        elapsed = round(max(0, clock()-start)*1000, 3)
        ok = status is not None and 200 <= status < 300
        if status is not None and not ok:
            error = 'http_status'
        if emit:
            emit({'event': 'http_attempt', 'service': service, 'attempt': attempt,
                  'status': status, 'error': error, 'response_ms': elapsed})
        retryable = error in {'timeout', 'connection_error'} or status in {429, 500, 502, 503, 504}
        if ok or not retryable or attempt > retries:
            return Result(service, ok, status, error, attempt, elapsed,
                          round(max(0, clock()-begin)*1000, 3), datetime.now(timezone.utc).isoformat())
        sleep(min(backoff * 2**(attempt-1), 5))
    raise AssertionError('unreachable')


def export_csv(results: list[Result], path: str) -> None:
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(Result.__dataclass_fields__))
        writer.writeheader()
        for result in results:
            row = asdict(result)
            # Spreadsheet formula injection protection for user-provided labels.
            if row['service'].lstrip().startswith(('=', '+', '-', '@')):
                row['service'] = "'" + row['service']
            writer.writerow(row)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='One-shot HTTP availability diagnostics')
    parser.add_argument('--url', action='append', default=[])
    parser.add_argument('--timeout', type=float, default=3)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--csv')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args(argv)
    emit = lambda record: print(json.dumps(record, ensure_ascii=False))
    try:
        if args.demo:
            if args.url:
                raise ValueError('Demo cannot be combined with external targets')
            from demo import run_demo
            results = run_demo(emit)
        else:
            if not args.url or len(args.url) > 100:
                raise ValueError('Provide 1..100 --url targets')
            for url in args.url:
                validate_url(url)
            results = [check(url, service=f'service-{i}', timeout=args.timeout, retries=args.retries, emit=emit) for i, url in enumerate(args.url, 1)]
        for result in results:
            emit({'event': 'http_result', **asdict(result)})
        if args.csv:
            export_csv(results, args.csv)
        return 0 if all(result.ok for result in results) else 1
    except (ValueError, OSError):
        print(json.dumps({'event': 'configuration_error', 'message': 'Invalid configuration or output path'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
