from unittest.mock import Mock
from urllib.error import URLError
import csv
import json
import pytest
from health_monitor import check, export_csv, main, fetch_status
from demo import run_demo


def test_timing_and_safe_logs():
    events=[]
    r=check('https://example.test/?token=secret',transport=Mock(return_value=204),clock=Mock(side_effect=[0,.01,.035,.04]),emit=events.append)
    assert r.ok and r.response_ms==25 and r.total_ms==40
    assert 'secret' not in json.dumps(events)


def test_recovery_backoff():
    sleep=Mock()
    r=check('http://example.test',transport=Mock(side_effect=[TimeoutError(),URLError('secret'),200]),sleep=sleep)
    assert r.ok and r.attempts==3
    assert [c.args[0] for c in sleep.call_args_list]==[.2,.4]


def test_timeout_exhaustion():
    events=[]
    r=check('http://example.test',retries=1,transport=Mock(side_effect=TimeoutError('secret')),sleep=Mock(),emit=events.append)
    assert r.error=='timeout' and r.attempts==2 and not r.ok
    assert 'secret' not in json.dumps(events)


@pytest.mark.parametrize('status,attempts',[(503,3),(429,3),(404,1),(401,1),(302,1)])
def test_status_policy(status,attempts):
    r=check('http://example.test',transport=Mock(return_value=status),sleep=Mock())
    assert not r.ok and r.attempts==attempts and r.status==status


@pytest.mark.parametrize('url',['ftp://example.test','http://u:p@example.test','http://example.test/#secret','http://example.test:bad','http://example.test/\n'])
def test_invalid_url(url):
    with pytest.raises(ValueError):check(url)


@pytest.mark.parametrize('options',[{'retries':-1},{'retries':True},{'retries':6},{'timeout':0},{'timeout':float('nan')},{'backoff':-1}])
def test_invalid_options(options):
    with pytest.raises(ValueError):check('http://example.test',**options)


def test_http_mock(monkeypatch):
    response=Mock(status=201)
    response.__enter__=Mock(return_value=response)
    response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr('health_monitor.build_opener',Mock(return_value=opener))
    assert fetch_status('http://example.test',2)==201
    response.read.assert_not_called()
    opener.open.assert_called_once_with('http://example.test',timeout=2)


def test_csv(tmp_path):
    r=check('http://example.test',service='=fictional',transport=Mock(return_value=200))
    path=tmp_path/'report.csv';export_csv([r],str(path))
    with path.open(encoding='utf8',newline='') as f:rows=list(csv.DictReader(f))
    assert rows[0]['service']=="'=fictional" and rows[0]['status']=='200'


def test_loopback_demo():
    results=run_demo()
    assert [r.ok for r in results]==[True,True,False]
    assert [r.attempts for r in results]==[1,2,2]


def test_cli(capsys):
    assert main([])==2
    capsys.readouterr()
    assert main(['--demo'])==1
    records=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert sum(r['event']=='http_result' for r in records)==3
