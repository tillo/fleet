"""tools/repin_sha256.py moves a `*_SHA256` pin in step with the `*_VERSION` Renovate bumped.

Renovate's regex manager rewrites `ARG GH_VERSION=` and nothing else, so every bump of a
version+SHA pair failed `sha256sum -c` in the build and waited for a hand re-pin (cli/cli
!66, herdr-core !64 on 2026-11-02). The renovate job installs this script and every image
repo runs it as a postUpgradeTask after each update (see
test_renovate_repin_sha256_is_allowed); it must re-pin exactly the pair that moved and
leave everything else alone. Moved here from mdapi/jump-bastion's bin/ (infra-ik4p.1);
each image repo's own tests check that its Dockerfile's SHA pairs are re-pinnable.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent  # not resolve(): mutator runs from symlinked worker trees
_spec = importlib.util.spec_from_file_location("repin_sha256", ROOT / "tools" / "repin_sha256.py")
repin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repin)

OLD = "0" * 64
DOCKERFILE = """\
ARG GO_VERSION=1.27
# renovate: datasource=go depName=google.golang.org/grpc extractVersion=^v(?<version>.+)$
ARG GRPC_VERSION=1.85.0
# renovate: datasource=github-releases depName=cli/cli extractVersion=^v(?<version>.+)$
ARG GH_VERSION=2.103.3
ARG GH_SHA256={old}
# renovate: datasource=custom.herdr-core depName=herdr-core
ARG HERDR_CORE_VERSION=0.1.17
ARG HERDR_CORE_SHA256={old}
RUN go get google.golang.org/grpc@v${{GRPC_VERSION}}
RUN curl -fsSL "https://github.com/cli/cli/releases/download/v${{GH_VERSION}}/gh_${{GH_VERSION}}_linux_amd64.deb" -o /tmp/gh.deb && \\
    echo "${{GH_SHA256}}  /tmp/gh.deb" | sha256sum -c -
RUN curl -fsSL "https://releases.herdr.dev/core/${{HERDR_CORE_VERSION}}/herdr-core-linux-x86_64.tar.gz" -o /tmp/hc.tgz
""".format(old=OLD)


class Fetch:
    """Serves fixed bytes per URL and records what was asked for."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = bodies
        self.asked: list[str] = []

    def __call__(self, url: str) -> str:
        self.asked.append(url)
        return hashlib.sha256(self.bodies[url]).hexdigest()


GH_URL = "https://github.com/cli/cli/releases/download/v2.103.3/gh_2.103.3_linux_amd64.deb"
CORE_URL = "https://releases.herdr.dev/core/0.1.17/herdr-core-linux-x86_64.tar.gz"


def run(dep: str, text: str = DOCKERFILE, bodies: dict | None = None):
    fetch = Fetch(bodies or {GH_URL: b"gh", CORE_URL: b"core"})
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Dockerfile"
        path.write_text(text)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = repin.main([dep, "--dockerfile", str(path)], fetch=fetch)
        return rc, path.read_text(), fetch.asked, out.getvalue()


def sha_of(text: str, arg: str) -> str:
    return re.search(r"^ARG %s=(\S+)$" % arg, text, re.M).group(1)


class Repin(unittest.TestCase):
    def test_the_bumped_pair_gets_the_hash_of_its_new_download(self):
        rc, text, asked, _ = run("cli/cli")
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [GH_URL])
        self.assertEqual(sha_of(text, "GH_SHA256"), hashlib.sha256(b"gh").hexdigest())

    def test_only_the_named_pair_moves(self):
        _, text, _, _ = run("cli/cli")
        self.assertEqual(sha_of(text, "HERDR_CORE_SHA256"), OLD)
        self.assertEqual(text.replace(sha_of(text, "GH_SHA256"), OLD), DOCKERFILE)

    def test_a_pin_with_no_sha_is_left_alone_without_a_download(self):
        rc, text, asked, out = run("google.golang.org/grpc")
        self.assertEqual((rc, text, asked), (0, DOCKERFILE, []))
        self.assertIn("no GRPC_SHA256", out)

    def test_a_dependency_from_another_file_is_left_alone(self):
        rc, text, asked, out = run("@earendil-works/pi-coding-agent")
        self.assertEqual((rc, text, asked), (0, DOCKERFILE, []))
        self.assertIn("not pinned in", out)

    def test_a_maven_or_registry_port_dep_name_is_left_alone_not_refused(self):
        # horizon-ciphertrust's maven updates run the task too (`groupId:artifactId`).
        for dep in ("org.javassist:javassist", "registry.example:5000/team/image"):
            with self.subTest(dep=dep):
                rc, text, asked, out = run(dep)
                self.assertEqual((rc, text, asked), (0, DOCKERFILE, []))
                self.assertIn("nothing to do", out)

    def test_a_shell_meaningful_dep_name_is_refused(self):
        for dep in ("node;id", "$(id)", "-h"):
            with self.subTest(dep=dep), self.assertRaises(SystemExit):
                run(dep)

    def test_a_dep_name_is_matched_whole_not_as_a_prefix(self):
        rc, text, asked, _ = run("herdr")
        self.assertEqual((rc, text, asked), (0, DOCKERFILE, []))

    def test_running_twice_gives_the_same_file(self):
        _, once, _, _ = run("herdr-core")
        _, twice, _, _ = run("herdr-core", once)
        self.assertEqual(once, twice)
        self.assertEqual(sha_of(twice, "HERDR_CORE_SHA256"), hashlib.sha256(b"core").hexdigest())

    def test_two_different_urls_for_one_version_refuse(self):
        text = DOCKERFILE + 'RUN curl "https://mirror.example/gh/${GH_VERSION}.deb"\n'
        with self.assertRaisesRegex(SystemExit, "2 download URLs"):
            run("cli/cli", text)

    def test_no_url_for_a_pinned_sha_refuses(self):
        text = DOCKERFILE.replace("https://github.com/cli/cli/releases/download/v${GH_VERSION}/", "/vendor/")
        with self.assertRaisesRegex(SystemExit, "0 download URLs"):
            run("cli/cli", text)

    def test_a_repo_with_no_root_dockerfile_has_nothing_to_do(self):
        # mdapi/roundcube builds apache/Dockerfile; the task still runs on each of its updates.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Dockerfile"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = repin.main(["php", "--dockerfile", str(path)], fetch=Fetch({}))
            self.assertEqual((rc, path.exists(), sorted(Path(tmp).iterdir())), (0, False, []))
            self.assertIn("no %s; nothing to do" % path, out.getvalue())

    def test_a_url_naming_an_undeclared_arg_refuses(self):
        text = DOCKERFILE.replace("_linux_amd64", "_linux_${ARCH}")
        with self.assertRaisesRegex(SystemExit, r"names \$\{ARCH\}, which has no ARG default"):
            run("cli/cli", text)


if __name__ == "__main__":
    unittest.main()
