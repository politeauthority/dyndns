"""Tests for the DynDNS job. Nothing here touches the network."""
import json
import logging
import random
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dyndns  # noqa: E402

IP = "75.166.33.209"
OLD_IP = "75.166.21.238"
OTHER_IP = "8.8.4.4"
PASSWORD = "s3cret-ddns-password"

APEX = dyndns.Record("@", "example.com", PASSWORD)
WILDCARD = dyndns.Record("*", "example.com", PASSWORD)


def provider(name, operator, answer, secure=True):
    def fetch():
        if isinstance(answer, Exception):
            raise answer
        return answer
    return dyndns.Provider(name, operator, secure, fetch)


class FakeDns:
    """Stands in for the world around run(), and records every write."""

    def __init__(self, monkeypatch, ip=IP, served=None, update_error=None, sticky=()):
        self.served = dict(served or {})
        self.writes = []
        self.notified = []
        self.update_error = update_error
        self.sticky = set(sticky)
        self.verify_problems = []
        self.verify_calls = 0
        monkeypatch.setattr(dyndns, "detect_ip",
                            lambda: dyndns.Detection(ip, {ip: {"a", "b"}} if ip else {}))
        monkeypatch.setattr(dyndns, "authoritative_a", self.lookup)
        monkeypatch.setattr(dyndns, "update_record", self.update)
        monkeypatch.setattr(dyndns, "notify", self.notified.append)
        monkeypatch.setattr(dyndns, "verify_public", self.verify)
        monkeypatch.setattr(dyndns.time, "sleep", lambda _: None)

    def _key(self, name):
        return "*" if name.startswith("dyndns-check-") else "@"

    def lookup(self, name):
        return set(self.served.get(self._key(name), {IP}))

    def update(self, record, ip):
        if self.update_error and record.host in self.update_error:
            raise dyndns.UpdateFailed("A record not Found")
        self.writes.append(record.host)
        if record.host not in self.sticky:
            self.served[record.host] = {ip}

    def verify(self, records, ip, label):
        self.verify_calls += 1
        return self.verify_problems


# Detection

@pytest.mark.parametrize("raw", ["75.166.33.209", " 75.166.33.209\n", '"75.166.33.209"'])
def test_parse_accepts_public_ipv4(raw):
    assert dyndns.parse_public_ipv4(raw) == IP


@pytest.mark.parametrize("raw", [
    "10.0.0.1", "192.168.50.1", "100.64.0.1", "127.0.0.1", "169.254.1.1",
    "2606:4700::1", "<html>503 Service Unavailable</html>", "",
])
def test_parse_rejects_anything_else(raw):
    with pytest.raises(ValueError):
        dyndns.parse_public_ipv4(raw)


def test_two_operators_reach_quorum():
    result = dyndns.detect_ip([provider("a", "one", IP), provider("b", "two", IP)])
    assert result.ip == IP


def test_same_operator_twice_isnt_a_quorum():
    result = dyndns.detect_ip([provider("a", "cloudflare", IP), provider("b", "cloudflare", IP)])
    assert result.ip is None
    assert result.answers == {IP: {"a", "b"}}


def test_quorum_needs_a_tls_source():
    result = dyndns.detect_ip([
        provider("a", "one", IP, secure=False),
        provider("b", "two", IP, secure=False),
    ])
    assert result.ip is None


def test_one_bad_answer_is_outvoted():
    result = dyndns.detect_ip([
        provider("a", "one", OTHER_IP),
        provider("b", "two", IP),
        provider("c", "three", IP),
        provider("d", "four", IP),
    ])
    assert result.ip == IP


def test_tie_between_two_ips_doesnt_pick_one(monkeypatch):
    pool = [provider("a", "one", IP), provider("b", "two", IP),
            provider("c", "three", OTHER_IP), provider("d", "four", OTHER_IP)]
    monkeypatch.setattr(dyndns, "plan_batches", lambda providers: [providers])
    assert dyndns.detect_ip(pool).ip is None


def test_all_providers_down_returns_no_ip():
    down = [provider(str(i), str(i), requests.ConnectionError()) for i in range(10)]
    result = dyndns.detect_ip(down)
    assert result.ip is None
    assert result.answers == {}


def test_private_answers_never_count():
    result = dyndns.detect_ip([provider("a", "one", "192.168.1.1"), provider("b", "two", "192.168.1.1")])
    assert result.ip is None


def test_first_batch_is_two_tls_and_one_dns_from_different_operators():
    for seed in range(50):
        first = dyndns.plan_batches(dyndns.PROVIDERS, random.Random(seed))[0]
        assert [p.secure for p in sorted(first, key=lambda p: not p.secure)] == [True, True, False]
        assert len({p.operator for p in first}) == 3


def test_batches_cover_the_pool_once():
    batches = dyndns.plan_batches(dyndns.PROVIDERS, random.Random(1))
    names = [p.name for batch in batches for p in batch]
    assert sorted(names) == sorted(p.name for p in dyndns.PROVIDERS)


def test_cloudflare_trace_parses_ip_line(monkeypatch):
    monkeypatch.setattr(dyndns, "_http_get", lambda url: "fl=1\nh=1.1.1.1\nip=%s\nts=1\n" % IP)
    assert dyndns._cloudflare_trace() == IP


def test_redirects_arent_followed(monkeypatch):
    class Response:
        status_code = 301
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs)
        return Response()
    monkeypatch.setattr(dyndns.requests, "get", get)
    with pytest.raises(ValueError):
        dyndns._http_get("https://example.com")
    assert seen["allow_redirects"] is False
    assert seen["timeout"] == dyndns.HTTP_TIMEOUT


# Config

def test_load_records(tmp_path):
    path = tmp_path / "domains.json"
    path.write_text(json.dumps({"domains": [{"host": "@", "domain": "a.com", "password": "p"}]}))
    assert dyndns.load_records(str(path)) == [dyndns.Record("@", "a.com", "p")]


def test_config_errors_dont_leak_contents(tmp_path):
    path = tmp_path / "domains.json"
    path.write_text('{"domains": [{"password": "%s"' % PASSWORD)
    with pytest.raises(dyndns.ConfigError) as error:
        dyndns.load_records(str(path))
    assert PASSWORD not in str(error.value)


def test_missing_config_is_a_config_error(tmp_path):
    with pytest.raises(dyndns.ConfigError):
        dyndns.load_records(str(tmp_path / "nope.json"))


def test_query_names():
    assert dyndns.query_name(APEX, "label") == "example.com"
    assert dyndns.query_name(WILDCARD, "label") == "label.example.com"
    assert dyndns.query_name(dyndns.Record("ha", "example.com", "p"), "label") == "ha.example.com"


# Namecheap responses

SUCCESS = b'<?xml version="1.0" encoding="utf-16"?><interface-response>' \
          b'<IP>1.2.3.4</IP><ErrCount>0</ErrCount><Done>true</Done></interface-response>'
NOT_FOUND = b'<?xml version="1.0" encoding="utf-16"?><interface-response><ErrCount>1</ErrCount>' \
            b'<errors><Err1>No Records updated. A record not Found;</Err1></errors></interface-response>'


def test_success_response():
    assert dyndns.parse_update_response(SUCCESS) == []


def test_real_utf16_response():
    assert dyndns.parse_update_response(SUCCESS.decode().encode("utf-16")) == []


def test_http_200_with_errcount_is_a_failure():
    assert dyndns.parse_update_response(NOT_FOUND) == ["No Records updated. A record not Found;"]


def test_response_without_errcount_is_a_failure():
    assert dyndns.parse_update_response(b"<html>maintenance</html>") == ["response has no ErrCount"]


def _fake_response(body, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


def test_update_keeps_literal_wildcard_and_at(monkeypatch):
    seen = []

    def get(url, **_):
        seen.append(url)
        return _fake_response(SUCCESS)
    monkeypatch.setattr(dyndns.requests, "get", get)
    dyndns.update_record(WILDCARD, IP)
    dyndns.update_record(APEX, IP)
    assert "host=*&domain=example.com" in seen[0]
    assert "host=@&domain=example.com" in seen[1]
    assert seen[0].endswith("ip=%s" % IP)


def test_update_raises_on_errcount(monkeypatch):
    monkeypatch.setattr(dyndns.requests, "get", lambda *a, **k: _fake_response(NOT_FOUND))
    with pytest.raises(dyndns.UpdateFailed, match="A record not Found"):
        dyndns.update_record(APEX, IP)


def test_update_failure_never_exposes_password(monkeypatch):
    def get(url, **_):
        raise requests.ConnectionError("Max retries exceeded with url: %s" % url)
    monkeypatch.setattr(dyndns.requests, "get", get)
    with pytest.raises(dyndns.UpdateFailed) as error:
        dyndns.update_record(APEX, IP)
    assert PASSWORD not in str(error.value)
    assert error.value.__suppress_context__
    assert error.value.__cause__ is None


# Whole runs

def test_nothing_stale_writes_nothing_and_verifies(monkeypatch):
    fake = FakeDns(monkeypatch)
    assert dyndns.run([APEX, WILDCARD]) == 0
    assert fake.writes == []
    assert fake.verify_calls == 1
    assert fake.notified == []


def test_only_stale_records_are_written(monkeypatch):
    fake = FakeDns(monkeypatch, served={"*": {OLD_IP}})
    assert dyndns.run([APEX, WILDCARD]) == 0
    assert fake.writes == ["*"]
    assert len(fake.notified) == 1
    assert "*.example.com" in fake.notified[0]
    assert fake.verify_calls == 0


def test_missing_record_counts_as_stale(monkeypatch):
    fake = FakeDns(monkeypatch, served={"@": set()})
    assert dyndns.run([APEX]) == 0
    assert fake.writes == ["@"]


def test_extra_values_count_as_stale(monkeypatch):
    fake = FakeDns(monkeypatch, served={"@": {IP, OLD_IP}})
    assert dyndns.run([APEX]) == 0
    assert fake.writes == ["@"]


def test_partial_write_failure_fails_the_run_and_keeps_going(monkeypatch):
    fake = FakeDns(monkeypatch, served={"@": {OLD_IP}, "*": {OLD_IP}}, update_error={"@"})
    assert dyndns.run([APEX, WILDCARD]) == 1
    assert fake.writes == ["*"]
    assert "*.example.com" in fake.notified[0]
    assert "@.example.com" not in fake.notified[0]


def test_failed_write_is_retried_next_run(monkeypatch):
    fake = FakeDns(monkeypatch, served={"@": {OLD_IP}}, update_error={"@"})
    assert dyndns.run([APEX]) == 1
    fake.update_error = None
    assert dyndns.run([APEX]) == 0
    assert fake.writes == ["@"]


def test_accepted_update_that_changes_nothing_fails(monkeypatch):
    """The 2026-09-22 *.squid-ink.us case: ErrCount 0, and the served record never moved."""
    fake = FakeDns(monkeypatch, served={"*": {OLD_IP}}, sticky={"*"})
    assert dyndns.run([APEX, WILDCARD]) == 1
    assert fake.writes == ["*"]
    assert fake.notified == []


def test_no_quorum_never_writes(monkeypatch):
    fake = FakeDns(monkeypatch, ip=None, served={"@": {OLD_IP}})
    assert dyndns.run([APEX]) == 1
    assert fake.writes == []


def test_lone_answer_confirms_no_change(monkeypatch):
    fake = FakeDns(monkeypatch)
    monkeypatch.setattr(dyndns, "detect_ip", lambda: dyndns.Detection(None, {IP: {"a"}}))
    assert dyndns.run([APEX]) == 0
    assert fake.writes == []


def test_lone_different_answer_doesnt_trigger_a_write(monkeypatch):
    fake = FakeDns(monkeypatch)
    monkeypatch.setattr(dyndns, "detect_ip", lambda: dyndns.Detection(None, {OTHER_IP: {"a"}}))
    assert dyndns.run([APEX]) == 1
    assert fake.writes == []


def test_unreadable_dns_never_writes(monkeypatch):
    fake = FakeDns(monkeypatch)

    def fail(_):
        raise dyndns.LookupFailed("timeout")
    monkeypatch.setattr(dyndns, "authoritative_a", fail)
    assert dyndns.run([APEX]) == 1
    assert fake.writes == []


def test_one_unreadable_domain_doesnt_block_the_others(monkeypatch):
    """A lapsed domain answers REFUSED. The live domains still have to update."""
    fake = FakeDns(monkeypatch, served={"@": {OLD_IP}})
    dead = dyndns.Record("@", "lapsed.example", PASSWORD)
    lookup = fake.lookup

    def authoritative(name):
        if name == "lapsed.example":
            raise dyndns.LookupFailed("REFUSED")
        return lookup(name)
    monkeypatch.setattr(dyndns, "authoritative_a", authoritative)
    assert dyndns.run([dead, APEX]) == 1
    assert fake.writes == ["@"]


def test_public_problem_fails_the_run(monkeypatch):
    fake = FakeDns(monkeypatch)
    fake.verify_problems = ["delegation of example.com"]
    assert dyndns.run([APEX]) == 1


def test_dry_run_never_writes_or_notifies(monkeypatch):
    fake = FakeDns(monkeypatch, served={"@": {OLD_IP}})
    assert dyndns.run([APEX], dry_run=True) == 0
    assert fake.writes == []
    assert fake.notified == []


def test_dry_run_always_exits_zero(monkeypatch):
    FakeDns(monkeypatch, ip=None, served={"@": {OLD_IP}})
    assert dyndns.run([APEX], dry_run=True) == 0


def test_run_logs_never_contain_the_password(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    FakeDns(monkeypatch, served={"@": {OLD_IP}, "*": {OLD_IP}}, update_error={"@"})
    dyndns.run([APEX, WILDCARD])
    assert PASSWORD not in caplog.text


# Verification against public resolvers

def test_verify_clean(monkeypatch):
    monkeypatch.setattr(dyndns, "check_delegation", lambda domain, server: True)
    monkeypatch.setattr(dyndns, "_query_a", lambda name, server, recursive: {IP})
    assert dyndns.verify_public([APEX], IP, "label") == []


def test_verify_catches_parked_domain(monkeypatch):
    """The 2026-08-04 alix.lol case: expired, and sent to parking nameservers."""
    monkeypatch.setattr(dyndns, "check_delegation", lambda domain, server: False)
    monkeypatch.setattr(dyndns, "_query_a", lambda name, server, recursive: {"72.251.7.22"})
    problems = dyndns.verify_public([APEX], IP, "label")
    assert "delegation of example.com" in problems
    assert "public answer for @.example.com" in problems


def test_verify_tolerates_one_stale_resolver(monkeypatch):
    monkeypatch.setattr(dyndns, "check_delegation", lambda domain, server: True)
    monkeypatch.setattr(dyndns, "_query_a",
                        lambda name, server, recursive: {IP} if server == "8.8.8.8" else {OLD_IP})
    assert dyndns.verify_public([APEX], IP, "label") == []


def test_verify_treats_silent_resolvers_as_no_evidence(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("network unreachable")
    monkeypatch.setattr(dyndns, "check_delegation", fail)
    monkeypatch.setattr(dyndns, "_query_a", fail)
    assert dyndns.verify_public([APEX], IP, "label") == []


# Notifications

def test_notify_unconfigured_is_quiet(monkeypatch):
    for name in ("QUIGLEY_URL", "QUIGLEY_USER", "QUIGLEY_KEY"):
        monkeypatch.delenv(name, raising=False)

    def post(*_args, **_kwargs):
        raise AssertionError("shouldn't post")
    monkeypatch.setattr(dyndns.requests, "post", post)
    dyndns.notify("hello")


def test_notify_failure_doesnt_raise(monkeypatch):
    monkeypatch.setenv("QUIGLEY_URL", "http://quigley")
    monkeypatch.setenv("QUIGLEY_USER", "u")
    monkeypatch.setenv("QUIGLEY_KEY", "k")

    def post(*_args, **_kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr(dyndns.requests, "post", post)
    dyndns.notify("hello")


def test_notify_posts_to_the_notify_route(monkeypatch):
    monkeypatch.setenv("QUIGLEY_URL", "http://quigley/")
    monkeypatch.setenv("QUIGLEY_USER", "u")
    monkeypatch.setenv("QUIGLEY_KEY", "k")
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _fake_response(b"{}")
    monkeypatch.setattr(dyndns.requests, "post", post)
    dyndns.notify("hello")
    assert seen["url"] == "http://quigley/notify"
    assert seen["json"] == {"room_id": "default", "message": "hello"}
    assert seen["auth"] == ("u", "k")


def test_main_returns_one_on_bad_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DOMAIN_FILE", str(tmp_path / "missing.json"))
    assert dyndns.main() == 1
