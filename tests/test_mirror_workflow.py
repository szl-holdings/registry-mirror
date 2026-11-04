"""Exercise the workflow's real mirror loop without accessing either registry."""

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest import mock


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/mirror-dockerhub.yml"
FAKE_CRANE = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open("crane-calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
scenario = os.environ["TEST_SCENARIO"]
state = Path("copied.json")
copied = json.loads(state.read_text()) if state.exists() else {}
digest = "sha256:" + "a" * 64

def blocked():
    status = os.environ.get("TEST_STATUS", "401")
    reason = {"401": "Unauthorized", "403": "Forbidden", "429": "Too Many Requests"}[status]
    print("Error: unexpected status code " + status + " " + reason, file=sys.stderr)
    raise SystemExit(1)

if args[0] == "ls":
    if scenario == "blocked_source_list":
        print("latest")  # An error may arrive after partial stdout.
        blocked()
    if scenario == "transient_source_list" and args[1].endswith("/first"):
        print("latest")
        print("Error: unexpected status code 503 Service Unavailable", file=sys.stderr)
        raise SystemExit(1)
    if scenario == "no_release_tags":
        print("sha-excluded\nnot-a-release")
        raise SystemExit(0)
    if scenario == "empty_source_list":
        raise SystemExit(0)
    print("latest\n1.0.0\nsha-excluded\nnot-a-release")
elif args[0] == "digest":
    ref = args[1]
    if ref.startswith("ghcr.io/"):
        if scenario == "blocked_source_digest":
            blocked()
        if scenario == "transient_source_digest" and ref.endswith("/first:latest"):
            print("Error: unexpected status code 503 Service Unavailable", file=sys.stderr)
            raise SystemExit(1)
        print(digest)
    elif scenario == "blocked_digest":
        blocked()
    elif scenario in {"transient_destination_digest", "transport_destination_digest", "mixed_destination_digest"} and ref.endswith("/first:latest"):
        if scenario == "transport_destination_digest":
            print("Error: connection reset by peer", file=sys.stderr)
        else:
            if scenario == "mixed_destination_digest":
                print("HEAD request failed, falling back on GET: unexpected status code 404 Not Found", file=sys.stderr)
            print("Error: unexpected status code 503 Service Unavailable", file=sys.stderr)
        raise SystemExit(1)
    elif scenario == "identical":
        print(digest)
    elif ref in copied:
        if scenario == "blocked_verification":
            blocked()
        print("sha256:" + "b" * 64 if scenario == "mismatch" else copied[ref])
    else:
        missing = {
            "missing_destination_404": "unexpected status code 404 Not Found",
            "missing_destination_name": "NAME_UNKNOWN: repository is absent",
            "missing_destination_get": "GET https://index.docker.io/v2/example/image/manifests/latest: unexpected status code 404 Not Found",
            "missing_destination_manifest": "fetching manifest docker.io/example/image:latest: GET https://index.docker.io/v2/example/image/manifests/latest: MANIFEST_UNKNOWN: manifest unknown; unknown tag=latest",
            "mixed_503_name": "unexpected status code 503 Service Unavailable: NAME_UNKNOWN: upstream unavailable",
            "mixed_head_get": "HEAD unexpected status code 404 Not Found; GET request failed: context deadline exceeded",
            "mixed_500_manifest": "unexpected status code 500 Internal Server Error: MANIFEST_UNKNOWN",
        }.get(scenario, "MANIFEST_UNKNOWN: tag is absent")
        print("Error: " + missing, file=sys.stderr)
        raise SystemExit(1)
elif args[0] == "copy":
    if scenario == "blocked_copy":
        blocked()
    if scenario == "transient_copy_failure":
        print("Error: unexpected status code 503 Service Unavailable", file=sys.stderr)
        raise SystemExit(1)
    # A mutable tag can change after the source digest has been inspected.
    copied[args[2]] = ("sha256:" + "b" * 64
                       if scenario == "moving_source_tag" and "@" not in args[1]
                       else digest)
    state.write_text(json.dumps(copied, sort_keys=True))
else:
    raise SystemExit("unexpected crane command: " + repr(args))
'''

# Stand-ins for the Authenticate step: they record argv and stdin so tests can prove
# that exactly one token request is made and that secrets never appear in argv.
FAKE_AUTH_CRANE = r'''#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
with open("crane-calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "stdin": sys.stdin.read()}) + "\n")
if args[:2] != ["auth", "login"]:
    raise SystemExit("unexpected crane command: " + repr(args))
'''
FAKE_CURL = r'''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open("curl-calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "stdin": sys.stdin.read()}) + "\n")
scenario = os.environ["TEST_HTTP"]
body = {
    "200": '{"token":"fake-bearer","access_token":"fake-bearer","expires_in":300}',
    "200-no-token": '{"expires_in":300}',
    "401": '{"details":"incorrect username or password"}',
    "429": '{"details":"too many failed login attempts for username or IP address"}',
    "403": '{"details":"access denied"}',
    "503": "<html>service unavailable</html>",
}.get(scenario, "")
with open(args[args.index("--output") + 1], "w", encoding="utf-8") as out:
    out.write(body)
if scenario == "000":
    sys.stdout.write("000")
    print("curl: (28) Operation timed out after 30000 milliseconds", file=sys.stderr)
    raise SystemExit(28)
sys.stdout.write(scenario.split("-")[0])
'''


def step_script(name):
    """Read one step's inline run script from the workflow."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: " + name + "\n", 1)[1]
    body = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(body)


def auth_script():
    """Read the actual credential check from the workflow."""
    return step_script("Authenticate (GHCR read, Docker Hub write)")


def mirror_script():
    """Read the inline job rather than maintaining a separate test implementation."""
    return step_script("Mirror release tags and verify digests")


def finalize_script():
    """Read the actual receipt finalizer from the workflow."""
    return step_script("Finalize receipt for setup and preflight failures")


class MirrorContractTests(unittest.TestCase):
    def run_case(self, scenario, status="401"):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            crane = cwd / "crane"
            crane.write_bytes(FAKE_CRANE.encode("utf-8"))
            crane.chmod(0o755)
            result = subprocess.run(
                [os.environ.get("BASH", "bash"), "-c", mirror_script()],
                cwd=cwd,
                env={
                    "PATH": os.environ["PATH"],
                    "TEST_SCENARIO": scenario,
                    "TEST_STATUS": status,
                    "SOURCE_ORG": "ghcr.io/example",
                    "DEST_ORG": "docker.io/example",
                    "IMAGES": "first second",
                    "GITHUB_RUN_ID": "offline-test",
                },
                text=True,
                capture_output=True,
                timeout=60 if os.name == "nt" else 20,
            )
            self.assertTrue((cwd / "crane-calls.jsonl").exists(), result.stdout + result.stderr)
            calls = [
                json.loads(line)
                for line in (cwd / "crane-calls.jsonl").read_text().splitlines()
            ]
            receipt_path = cwd / "mirror-receipt.json"
            self.assertTrue(receipt_path.exists(), result.stdout + result.stderr)
            receipt = json.loads(receipt_path.read_text())
            return result, calls, receipt

    def test_destination_access_failure_stops_before_copy(self):
        for status in ("401", "403", "429"):
            with self.subTest(status=status):
                result, calls, receipt = self.run_case("blocked_digest", status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([call for call in calls if call[0] == "copy"], [])
                self.assertEqual(len([call for call in calls if call[0] == "ls"]), 1)
                self.assertEqual(len(receipt["entries"]), 1)
                self.assertEqual(receipt["entries"][0]["result"], "DESTINATION_ACCESS_BLOCKED")
                self.assertIn(status, result.stderr)

    def test_unknown_destination_state_fails_without_copying_that_tag(self):
        for scenario in ("transient_destination_digest", "transport_destination_digest", "mixed_destination_digest"):
            with self.subTest(scenario=scenario):
                result, calls, receipt = self.run_case(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(receipt["summary"], {"DESTINATION_DIGEST_FAILED": 1, "MIRRORED_VERIFIED": 3})
                copied = [call[2] for call in calls if call[0] == "copy"]
                self.assertNotIn("docker.io/example/first:latest", copied)
                self.assertEqual(len(copied), 3)

    def test_explicit_missing_destination_still_allows_first_copy(self):
        for scenario in ("success", "missing_destination_404", "missing_destination_name", "missing_destination_get", "missing_destination_manifest"):
            with self.subTest(scenario=scenario):
                result, calls, receipt = self.run_case(scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(receipt["summary"], {"MIRRORED_VERIFIED": 4})
                self.assertEqual(len([call for call in calls if call[0] == "copy"]), 4)

    def test_compound_destination_errors_never_permit_copy(self):
        for scenario in ("mixed_503_name", "mixed_head_get", "mixed_500_manifest"):
            with self.subTest(scenario=scenario):
                result, calls, receipt = self.run_case(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([call for call in calls if call[0] == "copy"], [])
                self.assertEqual(receipt["summary"], {"DESTINATION_DIGEST_FAILED": 4})

    def test_source_list_access_failure_does_not_claim_empty_or_use_partial_tags(self):
        for status in ("401", "403", "429"):
            with self.subTest(status=status):
                result, calls, receipt = self.run_case("blocked_source_list", status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [["ls", "ghcr.io/example/first"]])
                self.assertEqual(receipt["summary"], {"SOURCE_TAG_LIST_FAILED": 1})
                self.assertIn(status, result.stderr)

    def test_source_list_transport_failure_preserves_failure_and_next_image(self):
        result, calls, receipt = self.run_case("transient_source_list")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["summary"], {"SOURCE_TAG_LIST_FAILED": 1, "MIRRORED_VERIFIED": 2})
        copied = [call for call in calls if call[0] == "copy"]
        self.assertEqual(len(copied), 2)
        self.assertTrue(all("/second@sha256:" in call[1] for call in copied))

    def test_successful_source_listing_without_release_tags_remains_empty(self):
        result, calls, receipt = self.run_case("no_release_tags")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["summary"], {"NO_RELEASE_TAGS": 2})
        self.assertTrue(all(call[0] == "ls" for call in calls))

    def test_successful_empty_source_listing_remains_empty(self):
        result, calls, receipt = self.run_case("empty_source_list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["summary"], {"NO_RELEASE_TAGS": 2})
        self.assertTrue(all(call[0] == "ls" for call in calls))

    def test_source_digest_access_failure_keeps_receipt_and_stops(self):
        for status in ("401", "403", "429"):
            with self.subTest(status=status):
                result, calls, receipt = self.run_case("blocked_source_digest", status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(receipt["summary"], {"SOURCE_DIGEST_FAILED": 1})
                self.assertEqual(calls, [
                    ["ls", "ghcr.io/example/first"],
                    ["digest", "ghcr.io/example/first:latest"],
                ])
                self.assertIn(status, result.stderr)

    def test_other_source_digest_failures_preserve_complete_receipts(self):
        result, calls, receipt = self.run_case("transient_source_digest")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["summary"], {"SOURCE_DIGEST_FAILED": 1, "MIRRORED_VERIFIED": 3})
        copied = [call for call in calls if call[0] == "copy"]
        self.assertEqual(len(copied), 3)
        self.assertNotIn("docker.io/example/first:latest", [call[2] for call in copied])

    def test_copy_access_failure_stops_remaining_tags_and_images(self):
        for status in ("401", "403", "429"):
            with self.subTest(status=status):
                result, calls, receipt = self.run_case("blocked_copy", status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len([call for call in calls if call[0] == "copy"]), 1)
                self.assertEqual(len([call for call in calls if call[0] == "ls"]), 1)
                self.assertEqual(len(receipt["entries"]), 1)
                self.assertEqual(receipt["entries"][0]["result"], "COPY_FAILED")
                self.assertIn(status, result.stderr)

    def test_verification_access_failure_keeps_receipt_and_stops(self):
        result, calls, receipt = self.run_case("blocked_verification")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([call for call in calls if call[0] == "copy"]), 1)
        self.assertEqual(len(receipt["entries"]), 1)
        self.assertEqual(receipt["entries"][0]["result"], "DESTINATION_DIGEST_FAILED")

    def test_other_copy_failures_remain_failures_with_complete_receipts(self):
        result, calls, receipt = self.run_case("transient_copy_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([call for call in calls if call[0] == "copy"]), 4)
        self.assertEqual(receipt["summary"], {"COPY_FAILED": 4})

    def test_verified_mirrors_succeed_and_filter_nonrelease_tags(self):
        result, calls, receipt = self.run_case("success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["summary"], {"MIRRORED_VERIFIED": 4})
        self.assertEqual({entry["tag"] for entry in receipt["entries"]}, {"latest", "1.0.0"})

    def test_source_tag_movement_copies_the_digest_that_was_observed(self):
        result, calls, receipt = self.run_case("moving_source_tag")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["summary"], {"MIRRORED_VERIFIED": 4})
        sources = {call[1] for call in calls if call[0] == "copy"}
        self.assertEqual(sources, {
            "ghcr.io/example/first@sha256:" + "a" * 64,
            "ghcr.io/example/second@sha256:" + "a" * 64,
        })

    def test_identical_destinations_do_not_copy(self):
        result, calls, receipt = self.run_case("identical")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call for call in calls if call[0] == "copy"], [])
        self.assertEqual(receipt["summary"], {"ALREADY_IDENTICAL": 4})

    def test_digest_mismatches_still_fail(self):
        result, calls, receipt = self.run_case("mismatch")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["summary"], {"DIGEST_MISMATCH": 4})


class ReceiptFinalizerTests(unittest.TestCase):
    def run_case(self, *, harden="success", creds="READY", install="success",
                 auth="success", mirror="success", prior_lines=None, aggregate=None,
                 auth_state="", auth_http_status="", auth_secret_defects=""):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            if prior_lines is not None:
                (cwd / "receipt.jsonl").write_text(
                    "\n".join(json.dumps(entry) for entry in prior_lines) + "\n",
                    encoding="utf-8",
                )
            if aggregate is not None:
                (cwd / "mirror-receipt.json").write_text(aggregate, encoding="utf-8")
            result = subprocess.run(
                [os.environ.get("BASH", "bash"), "-c", finalize_script()],
                cwd=cwd,
                env={
                    "PATH": os.environ["PATH"],
                    "HARDEN_OUTCOME": harden,
                    "CREDS_STATE": creds,
                    "INSTALL_OUTCOME": install,
                    "AUTH_OUTCOME": auth,
                    "AUTH_STATE": auth_state,
                    "AUTH_HTTP_STATUS": auth_http_status,
                    "AUTH_SECRET_DEFECTS": auth_secret_defects,
                    "MIRROR_OUTCOME": mirror,
                    "GITHUB_RUN_ID": "offline-finalizer-test",
                    "SOURCE_ORG": "ghcr.io/example",
                    "DEST_ORG": "docker.io/example",
                },
                text=True,
                capture_output=True,
                timeout=20,
            )
            receipt_path = cwd / "mirror-receipt.json"
            self.assertTrue(receipt_path.exists(), result.stdout + result.stderr)
            return result, json.loads(receipt_path.read_text(encoding="utf-8"))

    def test_setup_and_preflight_failures_emit_sanitized_receipts(self):
        cases = (
            ({"harden": "failure"}, "HARDEN_RUNNER_FAILED"),
            ({"creds": "SETUP_REQUIRED", "install": "skipped", "auth": "skipped", "mirror": "skipped"}, "SETUP_REQUIRED"),
            ({"creds": "", "install": "skipped", "auth": "skipped", "mirror": "skipped"}, "CREDENTIAL_CHECK_FAILED"),
            ({"install": "failure", "auth": "skipped", "mirror": "skipped"}, "CRANE_INSTALL_FAILED"),
            # An auth step that failed before classifying Docker Hub's answer has not
            # verified anything; REGISTRY_AUTH_FAILED is reserved for an observed 401.
            ({"auth": "failure", "mirror": "skipped"}, "REGISTRY_AUTH_UNVERIFIED"),
        )
        for settings, expected in cases:
            with self.subTest(expected=expected):
                result, receipt = self.run_case(**settings)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(receipt["summary"], {expected: 1})
                self.assertEqual(receipt["entries"], [{"result": expected}])

    def test_classified_docker_hub_auth_failures_are_receipted(self):
        cases = (
            ("REGISTRY_AUTH_FAILED", "401", "", {"registry": "docker.io", "http_status": 401}),
            ("REGISTRY_AUTH_LOCKED_OUT", "429", "USERNAME_LOOKS_LIKE_EMAIL,TOKEN_LEADING_OR_TRAILING_WHITESPACE",
             {"registry": "docker.io", "http_status": 429,
              "secret_defects": ["USERNAME_LOOKS_LIKE_EMAIL", "TOKEN_LEADING_OR_TRAILING_WHITESPACE"]}),
            ("REGISTRY_AUTH_UNVERIFIED", "503", "", {"registry": "docker.io", "http_status": 503}),
            # 000: no HTTP response, so no status code is claimed.
            ("REGISTRY_AUTH_UNVERIFIED", "000", "", {"registry": "docker.io"}),
            # Only known classifications pass through; anything else is unverified.
            ("SOMETHING_ELSE", "", "", {"registry": "docker.io"}),
        )
        for state, status, defects, detail in cases:
            with self.subTest(state=state, status=status):
                result, receipt = self.run_case(
                    auth="failure", mirror="skipped", auth_state=state,
                    auth_http_status=status, auth_secret_defects=defects,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = state if state.startswith("REGISTRY_AUTH_") else "REGISTRY_AUTH_UNVERIFIED"
                self.assertEqual(receipt["summary"], {expected: 1})
                self.assertEqual(receipt["entries"], [dict(detail, result=expected)])

    def test_unexpected_mirror_failure_retains_partial_entries(self):
        result, receipt = self.run_case(
            mirror="failure", prior_lines=[{"image": "first", "result": "MIRRORED_VERIFIED"}]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt["summary"], {"MIRRORED_VERIFIED": 1, "MIRROR_STEP_FAILED": 1})

    def test_valid_mirror_receipt_is_not_rewritten(self):
        original = json.dumps({
            "run": "offline-finalizer-test", "entries": [{"result": "MIRRORED_VERIFIED"}],
            "summary": {"MIRRORED_VERIFIED": 1},
        })
        result, receipt = self.run_case(aggregate=original)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(receipt, json.loads(original))

    def test_missing_or_invalid_success_receipt_fails_closed(self):
        for aggregate, expected in ((None, "MIRROR_RECEIPT_MISSING"), ("{}", "MIRROR_RECEIPT_INVALID")):
            with self.subTest(expected=expected):
                result, receipt = self.run_case(aggregate=aggregate)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(receipt["summary"], {expected: 1})


class DockerHubCredentialCheckTests(unittest.TestCase):
    USERNAME = "szlmirrorbot"
    TOKEN = "dckr_pat_" + "Q" * 27

    def run_case(self, http, username=USERNAME, token=TOKEN):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            bin_dir = cwd / "bin"
            bin_dir.mkdir()
            for path, source in ((cwd / "crane", FAKE_AUTH_CRANE), (bin_dir / "curl", FAKE_CURL)):
                path.write_bytes(source.encode("utf-8"))
                path.chmod(0o755)
            output = cwd / "github-output"
            result = subprocess.run(
                [os.environ.get("BASH", "bash"), "-c", auth_script()],
                cwd=cwd,
                env={
                    "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "TEST_HTTP": http,
                    "DOCKERHUB_USERNAME": username,
                    "DOCKERHUB_TOKEN": token,
                    "GHCR_TOKEN": "ghcr-token-value",
                    "GITHUB_ACTOR": "octocat",
                    "GITHUB_OUTPUT": str(output),
                    "DEST_ORG": "docker.io/example",
                    "IMAGES": "first second",
                },
                text=True,
                capture_output=True,
                timeout=20,
            )
            outputs = dict(
                line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
            ) if output.exists() else {}

            def calls(name):
                path = cwd / name
                if not path.exists():
                    return []
                return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

            return result, outputs, calls("curl-calls.jsonl"), calls("crane-calls.jsonl")

    def assert_secrets_not_exposed(self, result, curl_calls, crane_calls, username, token):
        logs = result.stdout + result.stderr
        basic = base64.b64encode((username + ":" + token).encode("utf-8")).decode("ascii")
        for secret in (token, token.strip(), basic):
            self.assertNotIn(secret, logs)
            for call in curl_calls + crane_calls:
                self.assertFalse(any(secret in arg for arg in call["args"]))
        if "@" in username or username != username.strip():
            self.assertNotIn(username.strip(), logs)

    def test_rejected_or_unverified_credentials_stop_after_one_token_request(self):
        cases = (
            ("401", "REGISTRY_AUTH_FAILED", "401"),
            ("429", "REGISTRY_AUTH_LOCKED_OUT", "429"),
            ("403", "REGISTRY_AUTH_UNVERIFIED", "403"),
            ("503", "REGISTRY_AUTH_UNVERIFIED", "503"),
            ("000", "REGISTRY_AUTH_UNVERIFIED", "000"),
            ("200-no-token", "REGISTRY_AUTH_UNVERIFIED", "200"),
        )
        for http, state, status in cases:
            with self.subTest(http=http):
                result, outputs, curl_calls, crane_calls = self.run_case(http)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs["state"], state)
                self.assertEqual(outputs["http_status"], status)
                self.assertEqual(outputs["secret_defects"], "")
                self.assertEqual(len(curl_calls), 1)
                self.assertIn("::error title=" + state + "::", result.stdout)
                # Docker Hub credentials are never written for the mirror loop to reuse.
                self.assertEqual([call["args"][2] for call in crane_calls], ["ghcr.io"])
                self.assert_secrets_not_exposed(result, curl_calls, crane_calls, self.USERNAME, self.TOKEN)

    def test_provider_error_bodies_are_never_logged(self):
        # All data is synthetic. Simulate a provider echo of the submitted token
        # and a marker that GitHub secret masking would not know to redact.
        marker = "provider-body-must-not-be-logged"
        for field in ("details", "message"):
            for http, state in (("401", "REGISTRY_AUTH_FAILED"),
                                ("429", "REGISTRY_AUTH_LOCKED_OUT"),
                                ("403", "REGISTRY_AUTH_UNVERIFIED"),
                                ("503", "REGISTRY_AUTH_UNVERIFIED")):
                with self.subTest(field=field, http=http):
                    fake = FAKE_CURL.replace(
                        'with open(args[args.index("--output") + 1]',
                        "body = json.dumps({" + repr(field) +
                        ": " + repr(marker + ": ") +
                        ' + os.environ["DOCKERHUB_TOKEN"]})\n' +
                        'with open(args[args.index("--output") + 1]',
                    )
                    with mock.patch(__name__ + ".FAKE_CURL", fake):
                        result, outputs, curl_calls, crane_calls = self.run_case(http)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(outputs["state"], state)
                    self.assertEqual(outputs["http_status"], http)
                    self.assertEqual(len(curl_calls), 1)
                    self.assertEqual([c["args"][2] for c in crane_calls], ["ghcr.io"])
                    self.assertNotIn(marker, result.stdout + result.stderr)
                    self.assert_secrets_not_exposed(
                        result, curl_calls, crane_calls, self.USERNAME, self.TOKEN
                    )

    def test_verified_credentials_are_configured_once(self):
        result, outputs, curl_calls, crane_calls = self.run_case("200")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(outputs, {
            "state": "REGISTRY_AUTH_VERIFIED", "http_status": "200", "secret_defects": "",
        })
        self.assertEqual(len(curl_calls), 1)
        self.assertEqual([call["args"] for call in crane_calls], [
            ["auth", "login", "ghcr.io", "-u", "octocat", "--password-stdin"],
            ["auth", "login", "docker.io", "-u", self.USERNAME, "--password-stdin"],
        ])
        self.assertEqual(crane_calls[1]["stdin"], self.TOKEN)
        self.assertNotIn("fake-bearer", result.stdout + result.stderr)
        self.assert_secrets_not_exposed(result, curl_calls, crane_calls, self.USERNAME, self.TOKEN)

    def test_token_request_is_single_scoped_and_reads_credentials_from_stdin(self):
        result, outputs, curl_calls, crane_calls = self.run_case("401")
        (call,) = curl_calls
        args = call["args"]
        self.assertEqual(args[-1], "https://auth.docker.io/token")
        self.assertIn("service=registry.docker.io", args)
        self.assertIn("scope=repository:example/first:pull,push", args)
        self.assertEqual(args[args.index("--header") + 1], "@-")
        self.assertEqual(args[args.index("--proto") + 1], "=https")
        for flag in ("--retry", "--location", "-L", "--user", "-u", "--netrc"):
            self.assertNotIn(flag, args)
        basic = base64.b64encode((self.USERNAME + ":" + self.TOKEN).encode("utf-8")).decode("ascii")
        self.assertEqual(call["stdin"], "Authorization: Basic " + basic + "\n")

    def test_secret_defects_are_reported_by_category_only(self):
        USERNAME, TOKEN = self.USERNAME, self.TOKEN
        cases = (
            ("someone@example.com", TOKEN + "\n",
             ["USERNAME_LOOKS_LIKE_EMAIL", "TOKEN_LEADING_OR_TRAILING_WHITESPACE"]),
            (USERNAME + "\r\n", TOKEN, ["USERNAME_LEADING_OR_TRAILING_WHITESPACE"]),
            (" " + USERNAME, '"' + TOKEN + '"',
             ["USERNAME_LEADING_OR_TRAILING_WHITESPACE", "TOKEN_SURROUNDING_QUOTES"]),
            ("<docker hub username>", TOKEN,
             ["USERNAME_EMBEDDED_WHITESPACE", "USERNAME_UNEXPECTED_CHARACTERS"]),
            ("docker.io/" + USERNAME, TOKEN, ["USERNAME_UNEXPECTED_CHARACTERS"]),
            (TOKEN, USERNAME, ["USERNAME_LOOKS_LIKE_ACCESS_TOKEN"]),
            (USERNAME, "\ufeff" + TOKEN, ["TOKEN_NON_ASCII_OR_CONTROL_CHARACTERS"]),
            (USERNAME, TOKEN[:12] + " " + TOKEN[12:], ["TOKEN_EMBEDDED_WHITESPACE"]),
        )
        for username, token, expected in cases:
            with self.subTest(expected=expected):
                result, outputs, curl_calls, crane_calls = self.run_case("401", username, token)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs["state"], "REGISTRY_AUTH_FAILED")
                self.assertEqual(outputs["secret_defects"].split(","), expected)
                self.assertEqual(len(curl_calls), 1)
                for defect in expected:
                    self.assertIn("::warning title=DOCKERHUB_SECRET_DEFECT::" + defect, result.stdout)
                self.assert_secrets_not_exposed(result, curl_calls, crane_calls, username, token)

    def test_defects_do_not_block_credentials_docker_hub_accepts(self):
        result, outputs, curl_calls, _ = self.run_case("200", self.USERNAME + "\n", self.TOKEN)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(outputs["state"], "REGISTRY_AUTH_VERIFIED")
        self.assertEqual(outputs["secret_defects"], "USERNAME_LEADING_OR_TRAILING_WHITESPACE")
        self.assertEqual(len(curl_calls), 1)

    def test_mirror_loop_requires_verified_docker_hub_credentials(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split("      - name: Mirror release tags and verify digests\n", 1)[1]
        condition = step.split("        if: ", 1)[1].split("\n", 1)[0]
        self.assertIn("steps.auth.outputs.state == 'REGISTRY_AUTH_VERIFIED'", condition)


if __name__ == "__main__":
    unittest.main()

