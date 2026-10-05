"""Exercise the workflow's real mirror loop without accessing either registry."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


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
copied = set(json.loads(state.read_text())) if state.exists() else set()
digest = "sha256:" + "a" * 64

def blocked():
    status = os.environ.get("TEST_STATUS", "401")
    reason = {"401": "Unauthorized", "403": "Forbidden", "429": "Too Many Requests"}[status]
    print("Error: unexpected status code " + status + " " + reason, file=sys.stderr)
    raise SystemExit(1)

if args[0] == "ls":
    print("latest\n1.0.0\nsha-excluded\nnot-a-release")
elif args[0] == "digest":
    ref = args[1]
    if ref.startswith("ghcr.io/"):
        print(digest)
    elif scenario == "blocked_digest":
        blocked()
    elif scenario == "identical":
        print(digest)
    elif ref in copied:
        if scenario == "blocked_verification":
            blocked()
        print("sha256:" + "b" * 64 if scenario == "mismatch" else digest)
    else:
        print("Error: MANIFEST_UNKNOWN: tag is absent", file=sys.stderr)
        raise SystemExit(1)
elif args[0] == "copy":
    if scenario == "blocked_copy":
        blocked()
    if scenario == "transient_copy_failure":
        print("Error: unexpected status code 503 Service Unavailable", file=sys.stderr)
        raise SystemExit(1)
    copied.add(args[2])
    state.write_text(json.dumps(sorted(copied)))
else:
    raise SystemExit("unexpected crane command: " + repr(args))
'''


def mirror_script():
    """Read the inline job rather than maintaining a separate test implementation."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Mirror release tags and verify digests\n", 1)[1]
    body = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(body)


class MirrorContractTests(unittest.TestCase):
    def run_case(self, scenario, status="401"):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            crane = cwd / "crane"
            crane.write_text(FAKE_CRANE, encoding="utf-8")
            crane.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", mirror_script()],
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
                timeout=20,
            )
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

    def test_identical_destinations_do_not_copy(self):
        result, calls, receipt = self.run_case("identical")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call for call in calls if call[0] == "copy"], [])
        self.assertEqual(receipt["summary"], {"ALREADY_IDENTICAL": 4})

    def test_digest_mismatches_still_fail(self):
        result, calls, receipt = self.run_case("mismatch")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["summary"], {"DIGEST_MISMATCH": 4})


if __name__ == "__main__":
    unittest.main()
