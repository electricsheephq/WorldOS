#!/usr/bin/env python3
"""Tests for qa/triage_failure.py — turn an app FAILURE BUCKET into an actionable triage.

Run (single-process):
    uv run --directory servers/engine python -m pytest ../../qa/test_triage_failure.py -q -p no:xdist
    uv run --directory /Users/lume/worldos-qa-p2b/servers/engine python -m pytest \
        /Users/lume/worldos-qa-p2b/qa/test_triage_failure.py -q -p no:xdist

triage_failure.py is a PURE READER: given a bucket name (and optionally a read-only run dir),
it emits likely cause(s), a recommended next diagnostic + retry env vars, and an INFRA/MEASUREMENT
vs PRODUCT classification. It writes nothing and never touches committed QA artifacts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import triage_failure as tf  # noqa: E402
from app_failure_buckets import APP_FAILURE_BUCKETS  # noqa: E402

SCRIPT = QA_DIR / "triage_failure.py"

# The two stable classes the tool routes a bucket into.
CLASSES = {tf.CLASS_INFRA, tf.CLASS_PRODUCT}


# --------------------------------------------------------------------------- #
# Core contract: every canonical bucket yields a non-empty, well-formed triage
# --------------------------------------------------------------------------- #
class TestEveryCanonicalBucket:
    def test_reuses_canonical_bucket_tuple(self):
        # The tool must IMPORT the canonical tuple, not re-declare its own.
        assert tf.APP_FAILURE_BUCKETS == APP_FAILURE_BUCKETS

    @pytest.mark.parametrize("bucket", APP_FAILURE_BUCKETS)
    def test_bucket_yields_nonempty_cause_and_classification(self, bucket):
        rep = tf.triage(bucket)
        assert rep["bucket"] == bucket
        # Likely cause(s): non-empty list of non-empty strings.
        assert isinstance(rep["likely_causes"], list)
        assert rep["likely_causes"], f"{bucket} produced no likely causes"
        assert all(isinstance(c, str) and c.strip() for c in rep["likely_causes"])
        # Classification is one of the two stable classes.
        assert rep["classification"] in CLASSES
        # Recommended next diagnostic is a non-empty string.
        assert isinstance(rep["next_diagnostic"], str) and rep["next_diagnostic"].strip()
        # Retry env vars is a dict (may be empty for pure-product defects).
        assert isinstance(rep["retry_env"], dict)
        # known flag is True for canonical buckets.
        assert rep["known"] is True

    @pytest.mark.parametrize("bucket", APP_FAILURE_BUCKETS)
    def test_each_canonical_bucket_has_a_diagnostic(self, bucket):
        rep = tf.triage(bucket)
        assert rep["next_diagnostic"]


# --------------------------------------------------------------------------- #
# INFRA/MEASUREMENT vs PRODUCT routing — the load-bearing distinction
# --------------------------------------------------------------------------- #
class TestClassification:
    def test_no_provider_is_infra(self):
        assert tf.triage("no_provider")["classification"] == tf.CLASS_INFRA

    def test_permission_prompt_is_infra(self):
        assert tf.triage("permission_prompt")["classification"] == tf.CLASS_INFRA

    def test_no_narration_is_product(self):
        assert tf.triage("no_narration")["classification"] == tf.CLASS_PRODUCT

    def test_move_rejected_is_product(self):
        assert tf.triage("move_rejected")["classification"] == tf.CLASS_PRODUCT

    def test_every_bucket_is_classified_exactly_once(self):
        # No canonical bucket may be left unclassified or double-claimed.
        infra = set(tf.INFRA_BUCKETS)
        product = set(tf.PRODUCT_BUCKETS)
        assert infra.isdisjoint(product)
        assert infra | product == set(APP_FAILURE_BUCKETS)


# --------------------------------------------------------------------------- #
# Unknown bucket degrades gracefully (no crash, flagged not-known)
# --------------------------------------------------------------------------- #
class TestUnknownBucket:
    def test_unknown_bucket_does_not_raise(self):
        rep = tf.triage("totally_made_up_bucket")
        assert rep["bucket"] == "totally_made_up_bucket"
        assert rep["known"] is False

    def test_unknown_bucket_still_has_cause_and_classification(self):
        rep = tf.triage("totally_made_up_bucket")
        assert rep["likely_causes"], "unknown bucket should still offer a generic cause"
        assert rep["classification"] in CLASSES
        assert rep["next_diagnostic"].strip()

    def test_empty_bucket_handled(self):
        rep = tf.triage("")
        assert rep["known"] is False
        assert rep["likely_causes"]


# --------------------------------------------------------------------------- #
# Read-only run-dir enrichment — optional, never mutates, tolerant of absence
# --------------------------------------------------------------------------- #
class TestRunDirEnrichment:
    def test_missing_run_dir_is_tolerated(self, tmp_path):
        rep = tf.triage("no_provider", run_dir=tmp_path / "does_not_exist")
        assert rep["bucket"] == "no_provider"
        assert rep["likely_causes"]
        # run_dir evidence absent -> empty/neutral, never a crash.
        assert isinstance(rep.get("evidence"), list)

    def test_run_dir_is_not_written(self, tmp_path):
        # A pure reader must leave the run dir byte-identical.
        run = tmp_path / "run"
        run.mkdir()
        (run / "app-status.final.json").write_text(
            json.dumps({"ok": True, "readiness": {"failure_bucket": "no_provider"}}),
            encoding="utf-8",
        )
        before = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in run.iterdir())
        tf.triage("no_provider", run_dir=run)
        after = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in run.iterdir())
        assert before == after, "triage must not write into the run dir"

    def test_run_dir_evidence_surfaces_app_status_detail(self, tmp_path):
        run = tmp_path / "run"
        run.mkdir()
        (run / "app-status.final.json").write_text(
            json.dumps(
                {
                    "ok": False,
                    "readiness": {
                        "failure_bucket": "no_provider",
                        "failure_detail": "no minted live provider viewer reported can_act:true",
                    },
                }
            ),
            encoding="utf-8",
        )
        rep = tf.triage("no_provider", run_dir=run)
        # The recorded detail is surfaced as evidence so the agent sees the artifact's own words.
        joined = " ".join(rep["evidence"])
        assert "can_act" in joined or "provider" in joined


# --------------------------------------------------------------------------- #
# CLI surface — --json and human output, exit code 0, never touches defaults
# --------------------------------------------------------------------------- #
class TestCli:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_json_for_canonical_bucket(self):
        proc = self._run("--bucket", "no_provider", "--json")
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["bucket"] == "no_provider"
        assert payload["classification"] == tf.CLASS_INFRA
        assert payload["likely_causes"]

    def test_cli_human_output_is_nonempty(self):
        proc = self._run("--bucket", "no_narration")
        assert proc.returncode == 0, proc.stderr
        assert "no_narration" in proc.stdout
        assert proc.stdout.strip()

    def test_cli_unknown_bucket_exit_zero(self):
        # An unknown bucket is a degraded report, not a CLI error.
        proc = self._run("--bucket", "made_up", "--json")
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["known"] is False

    def test_cli_with_run_dir(self, tmp_path):
        run = tmp_path / "run"
        run.mkdir()
        (run / "app-status.final.json").write_text(
            json.dumps({"ok": False, "readiness": {"failure_bucket": "permission_prompt"}}),
            encoding="utf-8",
        )
        proc = self._run("--bucket", "permission_prompt", "--run", str(run), "--json")
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["classification"] == tf.CLASS_INFRA


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:xdist"]))
