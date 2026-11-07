"""DynDNS: keep Namecheap A records pointed at the WAN IP.

Each run:
  detect   ask public IP services, require two operators to agree
  compare  ask Namecheap's nameservers what each record serves
  write    update only the records that differ, then confirm they changed
  verify   on runs with no writes, check public resolvers and delegation

DNS is the only state. A failed write is retried on the next run because the record is still wrong.
Exit status is non-zero when DNS is wrong or can't be checked, so KubeJobFailed can alert on it.
"""
import concurrent.futures
import ipaddress
import json
import logging
import os
import random
import re
import secrets
import socket
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlencode

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver
import requests
import urllib3.util.connection

log = logging.getLogger("dyndns")

HTTP_TIMEOUT = 3
DNS_TIMEOUT = 3
DETECT_BUDGET = 30
BATCH_SIZE = 3
QUORUM = 2

UPDATE_URL = "https://dynamicdns.park-your-domain.com/update"
UPDATE_TIMEOUT = (3, 10)
CONFIRM_ATTEMPTS = 6
CONFIRM_INTERVAL = 10

AUTHORITATIVE_NS = ("dns1.registrar-servers.com", "dns2.registrar-servers.com")
DELEGATION_SUFFIX = ".registrar-servers.com"
PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")

NOTIFY_TIMEOUT = 5


class ConfigError(Exception):
    """The domain file is missing or malformed."""


class LookupFailed(Exception):
    """No nameserver gave a usable answer."""


class UpdateFailed(Exception):
    """Namecheap didn't accept an update. Messages never contain the request URL."""


@dataclass(frozen=True)
class Record:
    host: str
    domain: str
    password: str

    @property
    def name(self) -> str:
        return "%s.%s" % (self.host, self.domain)


@dataclass(frozen=True)
class Provider:
    name: str
    operator: str
    secure: bool  # False for plain HTTP and DNS, which can only agree with a TLS source
    fetch: Callable[[], str]


@dataclass
class Detection:
    ip: Optional[str]
    answers: dict  # ip -> set of provider names


def parse_public_ipv4(raw: str) -> str:
    """Return the address if raw is a public IPv4 address, otherwise raise ValueError."""
    address = ipaddress.ip_address(raw.strip().strip('"'))
    if address.version != 4 or not address.is_global:
        raise ValueError("not a public IPv4 address")
    return str(address)


def _http_get(url: str) -> str:
    response = requests.get(
        url,
        timeout=HTTP_TIMEOUT,
        allow_redirects=False,
        headers={"User-Agent": "dyndns"},
    )
    if response.status_code != 200:
        raise ValueError("HTTP %s" % response.status_code)
    return response.text


def _http_text(url: str) -> Callable[[], str]:
    return lambda: _http_get(url)


def _cloudflare_trace() -> str:
    match = re.search(r"^ip=(.+)$", _http_get("https://1.1.1.1/cdn-cgi/trace"), re.MULTILINE)
    if not match:
        raise ValueError("no ip= line")
    return match.group(1)


def _dns_answer(name: str, rdtype: str, server: str, rdclass: str = "IN") -> str:
    """Return the first A or TXT value from a direct query to one server."""
    server_ip = server if _is_ip(server) else _resolve(server)
    query = dns.message.make_query(name, rdtype, rdclass)
    response = dns.query.udp(query, server_ip, timeout=DNS_TIMEOUT)
    for rrset in response.answer:
        for rdata in rrset:
            if rrset.rdtype == dns.rdatatype.TXT:
                return b"".join(rdata.strings).decode()
            if rrset.rdtype == dns.rdatatype.A:
                return rdata.address
    raise ValueError("empty answer")


def _dns_source(name: str, rdtype: str, server: str, rdclass: str = "IN") -> Callable[[], str]:
    return lambda: _dns_answer(name, rdtype, server, rdclass)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _resolve(hostname: str) -> str:
    return dns.resolver.resolve(hostname, "A", lifetime=DNS_TIMEOUT)[0].address


PROVIDERS = (
    Provider("icanhazip", "cloudflare", True, _http_text("https://ipv4.icanhazip.com")),
    Provider("ipify", "ipify", True, _http_text("https://api4.ipify.org")),
    Provider("amazonaws", "aws", True, _http_text("https://checkip.amazonaws.com")),
    Provider("ifconfig.me", "ifconfig.me", True, _http_text("https://ifconfig.me/ip")),
    Provider("ipinfo", "ipinfo", True, _http_text("https://ipinfo.io/ip")),
    Provider("cloudflare-trace", "cloudflare", True, _cloudflare_trace),
    Provider("akamai", "akamai", False, _http_text("http://whatismyip.akamai.com")),
    Provider("cloudflare-dns", "cloudflare", False,
             _dns_source("whoami.cloudflare", "TXT", "1.1.1.1", "CH")),
    Provider("google-dns", "google", False,
             _dns_source("o-o.myaddr.l.google.com", "TXT", "ns1.google.com")),
    Provider("opendns", "opendns", False,
             _dns_source("myip.opendns.com", "A", "resolver1.opendns.com")),
)


def plan_batches(providers, rng=random) -> list:
    """Order providers into batches. The first is 2 TLS sources and 1 DNS, all different operators."""
    pool = list(providers)
    rng.shuffle(pool)
    first = []
    operators = set()
    wanted = [lambda p: p.secure, lambda p: p.secure, lambda p: not p.secure]
    for matches in wanted:
        for provider in pool:
            if matches(provider) and provider.operator not in operators:
                first.append(provider)
                operators.add(provider.operator)
                break
    rest = [provider for provider in pool if provider not in first]
    batches = [first] if first else []
    for start in range(0, len(rest), BATCH_SIZE):
        batches.append(rest[start:start + BATCH_SIZE])
    return batches


def _ask(provider: Provider) -> Optional[str]:
    started = time.monotonic()
    try:
        ip = parse_public_ipv4(provider.fetch())
    except Exception as error:  # A provider can fail in any way. None of them stop the run.
        log.warning("provider=%s result=failed elapsed=%.2fs error=%s",
                    provider.name, time.monotonic() - started, type(error).__name__)
        return None
    log.info("provider=%s result=ok elapsed=%.2fs ip=%s",
             provider.name, time.monotonic() - started, ip)
    return ip


def _agreed(answers: dict) -> list:
    """Return IPs backed by enough different operators, at least one of them over TLS."""
    agreed = []
    for ip, sources in answers.items():
        operators = {source.operator for source in sources}
        if len(operators) >= QUORUM and any(source.secure for source in sources):
            agreed.append(ip)
    return agreed


def detect_ip(providers=PROVIDERS, budget: float = DETECT_BUDGET) -> Detection:
    """Ask providers in batches until exactly one IP has a quorum, or the pool or budget runs out."""
    deadline = time.monotonic() + budget
    answers = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE)
    try:
        for batch in plan_batches(providers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.error("detection budget of %ss used up", budget)
                break
            futures = {executor.submit(_ask, provider): provider for provider in batch}
            done, _ = concurrent.futures.wait(futures, timeout=remaining)
            for future in done:
                ip = future.result()
                if ip:
                    answers.setdefault(ip, set()).add(futures[future])
            agreed = _agreed(answers)
            if len(agreed) == 1:
                return Detection(agreed[0], _names(answers))
            if len(agreed) > 1:
                log.error("providers agree on more than one IP: %s", sorted(agreed))
                return Detection(None, _names(answers))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return Detection(None, _names(answers))


def _names(answers: dict) -> dict:
    return {ip: {source.name for source in sources} for ip, sources in answers.items()}


def load_records(path: str) -> list:
    """Read the domain file. Errors never include file contents, which hold passwords."""
    try:
        with open(path) as handle:
            entries = json.load(handle)["domains"]
        return [Record(e["host"], e["domain"], e["password"]) for e in entries]
    except OSError as error:
        raise ConfigError("can't read %s: %s" % (path, error.strerror))
    except json.JSONDecodeError as error:
        raise ConfigError("%s isn't valid JSON at line %s" % (path, error.lineno))
    except (KeyError, TypeError):
        raise ConfigError("%s needs domains[] entries with host, domain and password" % path)


def query_name(record: Record, wildcard_label: str) -> str:
    """Return the name to look up. A wildcard is tested with a label that has no record of its own."""
    if record.host == "@":
        return record.domain
    if record.host == "*":
        return "%s.%s" % (wildcard_label, record.domain)
    return "%s.%s" % (record.host, record.domain)


def _query_a(name: str, server_ip: str, recursive: bool) -> set:
    """Return the A values one server gives for a name. An empty set means no such record."""
    query = dns.message.make_query(name, "A")
    if not recursive:
        query.flags &= ~dns.flags.RD
    response, _ = dns.query.udp_with_fallback(query, server_ip, timeout=DNS_TIMEOUT)
    rcode = response.rcode()
    if rcode == dns.rcode.NXDOMAIN:
        return set()
    if rcode != dns.rcode.NOERROR:
        raise LookupFailed("%s answered %s" % (server_ip, dns.rcode.to_text(rcode)))
    values = set()
    for rrset in response.answer:
        if rrset.rdtype == dns.rdatatype.A:
            values.update(rdata.address for rdata in rrset)
    return values


def authoritative_a(name: str) -> set:
    """Ask Namecheap's nameservers for a name, falling back to the second one."""
    last_error = None
    for nameserver in AUTHORITATIVE_NS:
        try:
            return _query_a(name, _resolve(nameserver), recursive=False)
        except LookupFailed as error:
            last_error = str(error)
        except (dns.exception.DNSException, OSError) as error:
            last_error = type(error).__name__
    raise LookupFailed("no authoritative answer for %s (%s)" % (name, last_error))


def parse_update_response(body: bytes) -> list:
    """Return Namecheap's error messages, empty on success.

    Namecheap's XML declares UTF-16 and strict parsers reject it, so this reads the tags directly.
    Dropping NUL bytes handles the body being real UTF-16 too.
    """
    text = body.replace(b"\x00", b"").decode("ascii", errors="ignore")
    count = re.search(r"<ErrCount>\s*(\d+)\s*</ErrCount>", text)
    if not count:
        return ["response has no ErrCount"]
    if int(count.group(1)) == 0:
        return []
    errors = re.findall(r"<Err\d+>(.*?)</Err\d+>", text, re.DOTALL)
    return [error.strip()[:200] for error in errors] or ["ErrCount %s" % count.group(1)]


def update_record(record: Record, ip: str) -> None:
    """Point one record at ip. Namecheap only supports GET, so the password has to be in the URL."""
    query = urlencode(
        {"host": record.host, "domain": record.domain, "password": record.password, "ip": ip},
        safe="@*",
    )
    try:
        response = requests.get("%s?%s" % (UPDATE_URL, query), timeout=UPDATE_TIMEOUT)
    except requests.RequestException as error:
        # str(error) includes the URL and password. Only the type is safe to report.
        raise UpdateFailed(type(error).__name__) from None
    if response.status_code != 200:
        raise UpdateFailed("HTTP %s" % response.status_code)
    errors = parse_update_response(response.content)
    if errors:
        raise UpdateFailed("; ".join(errors))


def confirm_served(names: dict, ip: str, attempts: int = CONFIRM_ATTEMPTS,
                   interval: float = CONFIRM_INTERVAL) -> list:
    """Wait for the nameservers to serve ip. Returns the record names still wrong.

    An update can return ErrCount 0 and change nothing, as *.squid-ink.us did on 2026-09-22.
    """
    waiting = dict(names)
    for attempt in range(attempts):
        for record_name, lookup in list(waiting.items()):
            try:
                if authoritative_a(lookup) == {ip}:
                    del waiting[record_name]
            except LookupFailed as error:
                log.warning("confirm record=%s error=%s", record_name, error)
        if not waiting or attempt == attempts - 1:
            break
        time.sleep(interval)
    return sorted(waiting)


def check_delegation(domain: str, resolver_ip: str) -> bool:
    """Return whether a public resolver still sends the domain to Namecheap's nameservers."""
    query = dns.message.make_query(domain, "NS")
    response, _ = dns.query.udp_with_fallback(query, resolver_ip, timeout=DNS_TIMEOUT)
    nameservers = [
        str(rdata.target).rstrip(".").lower()
        for rrset in response.answer if rrset.rdtype == dns.rdatatype.NS
        for rdata in rrset
    ]
    return any(ns.endswith(DELEGATION_SUFFIX) for ns in nameservers)


def verify_public(records: list, ip: str, wildcard_label: str,
                  resolvers=PUBLIC_RESOLVERS) -> list:
    """Return problems every responding public resolver agrees on.

    One resolver can hold a stale cache, so a problem has to show on all that answered. A resolver
    that doesn't answer says nothing about DNS, so it's only a warning.
    """
    checks = {}
    for domain in sorted({record.domain for record in records}):
        checks["delegation of %s" % domain] = (
            lambda server, domain=domain: check_delegation(domain, server))
    for record in records:
        name = query_name(record, wildcard_label)
        checks["public answer for %s" % record.name] = (
            lambda server, name=name: _query_a(name, server, recursive=True) == {ip})

    problems = []
    for label, check in checks.items():
        results = []
        for resolver_ip in resolvers:
            try:
                results.append(check(resolver_ip))
            except (dns.exception.DNSException, OSError, LookupFailed) as error:
                log.warning("verify check=%r resolver=%s error=%s",
                            label, resolver_ip, type(error).__name__)
        if results and not any(results):
            problems.append(label)
    return problems


def notify(message: str) -> None:
    """Post to Matrix through quigley-api. A failure here must never fail the job."""
    url = os.environ.get("QUIGLEY_URL")
    user = os.environ.get("QUIGLEY_USER")
    key = os.environ.get("QUIGLEY_KEY")
    if not (url and user and key):
        log.info("notifications aren't configured, skipping")
        return
    try:
        response = requests.post(
            "%s/notify" % url.rstrip("/"),
            json={"room_id": os.environ.get("QUIGLEY_ROOM", "default"), "message": message},
            auth=(user, key),
            timeout=NOTIFY_TIMEOUT,
        )
        if response.status_code != 200:
            log.error("notification failed: HTTP %s", response.status_code)
    except requests.RequestException as error:
        log.error("notification failed: %s", type(error).__name__)


def run(records: list, dry_run: bool = False) -> int:
    """Run once and return the exit status. Dry runs log what would happen and always return 0."""
    status = _run(records, dry_run)
    if dry_run:
        log.info("dry run finished, real exit status would be %s", status)
        return 0
    return status


def _read_served(records: list, label: str) -> tuple:
    """Look up each record. One unreadable domain must not hold up updates to the others."""
    served = {}
    unreadable = []
    for record in records:
        try:
            served[record] = authoritative_a(query_name(record, label))
        except LookupFailed as error:
            log.error("can't read current DNS for record=%s, not writing it: %s",
                      record.name, error)
            unreadable.append(record)
    return served, unreadable


def _run(records: list, dry_run: bool) -> int:
    label = "dyndns-check-%s" % secrets.token_hex(6)
    detection = detect_ip()
    served, unreadable = _read_served(records, label)

    if detection.ip is None:
        return _handle_no_quorum(detection, served, unreadable)

    ip = detection.ip
    stale = [record for record, values in served.items() if values != {ip}]
    log.info("wan_ip=%s records=%s stale=%s unreadable=%s",
             ip, len(records), len(stale), len(unreadable))

    if not stale:
        problems = verify_public(list(served), ip, label) if served else []
        for problem in problems:
            log.error("verify failed: %s", problem)
        return 1 if problems or unreadable else 0

    failed = bool(unreadable)
    written = {}
    for record in stale:
        log.warning("record=%s serves=%s want=%s", record.name, sorted(served[record]), ip)
        if dry_run:
            log.info("dry run: would update record=%s", record.name)
            continue
        try:
            update_record(record, ip)
        except UpdateFailed as error:
            log.error("update failed record=%s error=%s", record.name, error)
            failed = True
            continue
        log.info("update accepted record=%s ip=%s", record.name, ip)
        written[record.name] = query_name(record, label)

    if dry_run:
        return 1 if failed else 0

    still_wrong = confirm_served(written, ip)
    for name in still_wrong:
        log.error("update accepted but record=%s still doesn't serve %s", name, ip)

    updated = sorted(set(written) - set(still_wrong))
    if updated:
        notify("DynDNS updated %s to %s" % (", ".join(updated), ip))
    return 1 if failed or still_wrong else 0


def _handle_no_quorum(detection: Detection, served: dict, unreadable: list) -> int:
    """Without a quorum nothing is written. A lone answer may still confirm nothing changed."""
    if len(detection.answers) == 1 and served and not unreadable:
        ip = next(iter(detection.answers))
        if all(values == {ip} for values in served.values()):
            log.warning("no quorum, but the only answer %s matches DNS", ip)
            return 0
    log.error("no quorum on the WAN IP, not writing: %s",
              {ip: sorted(names) for ip, names in detection.answers.items()})
    return 1


def main() -> int:
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] - %(message)s")
    # Namecheap only takes A records, and an IPv6 path would make IP services answer with IPv6.
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    log.info("starting dry_run=%s", dry_run)
    try:
        records = load_records(os.environ.get("DOMAIN_FILE", "/config/domains.json"))
    except ConfigError as error:
        log.critical("%s", error)
        return 1
    return run(records, dry_run)


if __name__ == "__main__":
    sys.exit(main())
