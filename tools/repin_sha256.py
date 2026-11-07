#!/usr/bin/env python3
"""repin-sha256 <depName>: re-pin the `*_SHA256` that belongs to a `*_VERSION` Renovate bumped.

Renovate's `customManagers:dockerfileVersions` rewrites `ARG <P>_VERSION=` and nothing
else, so a version+SHA pair (gh, node, herdr, herdr-core) failed `sha256sum -c` in the
build until someone re-pinned it by hand. An image repo's renovate.json runs this as a
postUpgradeTask on every update, after the version is written, in that repo's checkout.
It lives HERE, not in the image repos: fleet's `renovate` job installs it at
/tmp/renovate-tools/repin_sha256.py, the only path RENOVATE_ALLOWED_COMMANDS admits, so
no updated repo can swap in its own code under the bot token (infra-ik4p.1):

  * the `# renovate: ... depName=<dep>` line names the `ARG <P>_VERSION` below it;
  * no root Dockerfile, or no `ARG <P>_SHA256` (a Go module, an npm package), means
    nothing to do;
  * otherwise the one download URL that expands `${<P>_VERSION}` is rendered from the
    Dockerfile's ARG defaults, fetched, hashed, and the hash written to `<P>_SHA256`.

The hash is of what upstream serves at bump time: the same trust as the hand procedure
it replaces (download, `sha256sum`). The pin still guards every later build.
Refuses (exit != 0, Renovate marks the MR as an artifact failure) when the URL is not
unique or names an ARG with no default, instead of guessing.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

# The bot runs `python3 /tmp/renovate-tools/repin_sha256.py {{{depName}}}` through a
# shell, and RENOVATE_ALLOWED_COMMANDS admits exactly this charset after the script name.
DEP_NAME = r"^[A-Za-z0-9@][A-Za-z0-9@._/:-]*$"
URL_STOP = "\\s\"'"


class Plan(NamedTuple):
    sha_arg: str
    url: str


class Skip(Exception):
    """Nothing to re-pin for this dependency."""


def arg_defaults(text: str) -> dict[str, str]:
    return dict(re.findall(r"^ARG ([A-Za-z0-9_]+)=(\S+)$", text, re.M))


def render(url: str, args: dict[str, str]) -> str:
    def value(m: re.Match) -> str:
        if m.group(1) not in args:
            raise SystemExit("%s names ${%s}, which has no ARG default" % (url, m.group(1)))
        return args[m.group(1)]
    return re.sub(r"\$\{([A-Za-z0-9_]+)\}", value, url)


def plan(text: str, dep: str) -> Plan:
    m = re.search(r"^# renovate: .*\bdepName=%s(?:\s.*)?\nARG ([A-Z0-9_]+)_VERSION=" % re.escape(dep),
                  text, re.M)
    if not m:
        raise Skip("%s is not pinned in the Dockerfile" % dep)
    prefix = m.group(1)
    sha_arg = prefix + "_SHA256"
    if not re.search(r"^ARG %s=" % sha_arg, text, re.M):
        raise Skip("no %s next to %s_VERSION" % (sha_arg, prefix))
    pattern = r"https://[^%s]*\$\{%s_VERSION\}[^%s]*" % (URL_STOP, prefix, URL_STOP)
    urls = sorted(set(re.findall(pattern, text)))
    if len(urls) != 1:
        raise SystemExit("%s_VERSION has %d download URLs, need exactly 1: %s" % (prefix, len(urls), urls))
    return Plan(sha_arg, render(urls[0], arg_defaults(text)))


def read_dockerfile(path: Path) -> str:
    # A repo that builds from a subdirectory (mdapi/roundcube: apache/Dockerfile) has no
    # root Dockerfile, so it has no pair this task could re-pin.
    if not path.is_file():
        raise Skip("no %s" % path)
    return path.read_text(encoding="utf-8")


def repinned(text: str, sha_arg: str, digest: str) -> str:
    return re.sub(r"^ARG %s=\S+$" % sha_arg, "ARG %s=%s" % (sha_arg, digest), text, flags=re.M)


def download_sha256(url: str) -> str:
    digest = hashlib.sha256()
    # releases.herdr.dev answers urllib's default `Python-urllib/3.x` agent with 403.
    req = urllib.request.Request(url, headers={"User-Agent": "repin-sha256 (Renovate postUpgradeTask)"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None, fetch: Callable[[str], str] = download_sha256) -> int:
    ap = argparse.ArgumentParser(prog="repin-sha256")
    ap.add_argument("dep")
    ap.add_argument("--dockerfile", default="Dockerfile", type=Path)
    a = ap.parse_args(argv)
    if not re.match(DEP_NAME, a.dep):
        raise SystemExit("refusing depName %r" % a.dep)
    try:
        text = read_dockerfile(a.dockerfile)
        p = plan(text, a.dep)
    except Skip as skip:
        print("repin-sha256: %s; nothing to do" % skip)
        return 0
    digest = fetch(p.url)
    a.dockerfile.write_text(repinned(text, p.sha_arg, digest), encoding="utf-8")
    print("repin-sha256: %s=%s (%s)" % (p.sha_arg, digest, p.url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
