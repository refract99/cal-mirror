import os

from cal_mirror.crypto import generate_key
from cal_mirror.web import app as app_module
from cal_mirror.web.app import CERT_CHECK_SECONDS, cert_changed, cert_stamp, watch_cert

DSN = "postgresql://cal-mirror@localhost/cal_mirror"


def test_cert_check_seconds():
    assert CERT_CHECK_SECONDS == 60


def test_cert_stamp_stable_when_files_do_not_change(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    s = cert_stamp([str(cert), str(key)])
    s2 = cert_stamp([str(cert), str(key)])
    assert isinstance(s, tuple)
    assert len(s) == 2
    assert s[0] == (str(cert), os.stat(cert).st_mtime_ns, 4)
    assert s == s2
    assert cert_changed(s, s2) is False


def test_cert_changed_when_contents_change(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    s = cert_stamp([str(cert), str(key)])
    cert.write_text("renewed-cert")
    assert cert_changed(s, cert_stamp([str(cert), str(key)])) is True


def test_cert_changed_when_only_mtime_moves(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    s = cert_stamp([str(cert), str(key)])
    t = os.stat(key).st_mtime_ns
    os.utime(key, ns=(t, t + 1_000_000_000))
    assert cert_changed(s, cert_stamp([str(cert), str(key)])) is True


def test_cert_stamp_missing_file_and_removed_key(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    gone = tmp_path / "gone.pem"
    assert cert_stamp([str(gone)]) == ((str(gone), None, None),)
    before = cert_stamp([str(cert), str(key)])
    key.unlink()
    assert cert_changed(before, cert_stamp([str(cert), str(key)])) is True


def test_watch_cert_exits_when_certificate_is_renewed(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    server = type("Server", (), {"should_exit": False})()
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) == 2:
            cert.write_text("renewed-cert")

    watch_cert(server, [str(cert), str(key)], sleep=sleep)
    assert server.should_exit is True
    assert calls == [60, 60]


def test_watch_cert_returns_when_server_already_exiting(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    server = type("Server", (), {"should_exit": False})()
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        server.should_exit = True

    watch_cert(server, [str(cert), str(key)], sleep=sleep)
    assert calls == [60]
    assert server.should_exit is True


def test_main_starts_cert_watch_thread_before_server(monkeypatch, tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    monkeypatch.setenv("CAL_MIRROR_TLS_CERT", str(cert))
    monkeypatch.setenv("CAL_MIRROR_TLS_KEY", str(key))
    monkeypatch.setenv("CAL_MIRROR_DSN", DSN)
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    events = []
    servers = []
    threads = []

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            servers.append(self)

        def run(self):
            events.append("run")

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            threads.append(self)

        def start(self):
            events.append("start")

    class FakePg:
        def __init__(self, dsn, box):
            return None

    monkeypatch.setattr(app_module.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app_module, "PgStore", FakePg)
    app_module.main()
    assert len(threads) == 1
    assert threads[0].target is app_module.watch_cert
    assert threads[0].args == (servers[0], (str(cert), str(key)))
    assert threads[0].daemon is True
    assert events == ["start", "run"]
