"""native_gate build-SHA scoping (RRI 2026-06-09): the release verdict judges the canonical five
personas + the Mac handoff at ONE SHA. Extra DIAGNOSTIC personas (opus-high / lean variants) may
run at other SHAs without invalidating the release — they must NOT trip native_gate's build-SHA
gates. (The Tuesday sweep falsely failed native_gate because 3 variants stamped stale SHAs while
the 5 release personas were all at the candidate SHA.)

Stdlib + pytest. Run:
    uv run --directory servers/engine python -m pytest qa/test_release_readiness_scope.py -q -p no:xdist
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_readiness as rr  # noqa: E402

REL = rr.REQUIRED_RELEASE_PERSONAS  # newbie / veteran / adversarial / narrative / optimizer


def _score(persona, sha):
    return {"persona": persona, "run_build_sha": sha}


def _kinds(gaps):
    return {g["missing"] for g in gaps}


def test_extra_variant_at_stale_sha_does_not_fail_native_gate():
    # The EXACT Tuesday situation: the 5 release personas at the candidate SHA, 3 diagnostic
    # variants stamped at stale SHAs. The variants must be ignored for the build-SHA contract.
    scores = [_score(p, "033e4ba") for p in REL] + [
        _score("opushi-narr", "8afed3c"),
        _score("opuslean-narr", "f89ce94"),
        _score("opuslean-narr2", "eabf2a3"),
    ]
    gaps = rr.build_sha_evidence_gaps(scores, "033e4ba", REL)
    assert gaps == [], f"diagnostic variants must not trip native_gate build-sha gates: {gaps}"


def test_clean_release_set_has_no_build_sha_gaps():
    scores = [_score(p, "033e4ba") for p in REL]
    assert rr.build_sha_evidence_gaps(scores, "033e4ba", REL) == []


def test_a_release_persona_at_a_different_sha_still_fails():
    scores = [_score(p, "033e4ba") for p in REL[:-1]] + [_score(REL[-1], "deadbee")]
    kinds = _kinds(rr.build_sha_evidence_gaps(scores, "033e4ba", REL))
    assert "same-build persona evidence" in kinds or "single build_sha" in kinds, kinds


def test_missing_build_sha_on_a_release_persona_flags():
    scores = [_score(p, "033e4ba") for p in REL[:-1]] + [{"persona": REL[-1], "run_build_sha": ""}]
    assert "per-run build_sha" in _kinds(rr.build_sha_evidence_gaps(scores, "033e4ba", REL))


def test_no_build_sha_arg_flags():
    scores = [_score(p, "033e4ba") for p in REL]
    assert "--build-sha" in _kinds(rr.build_sha_evidence_gaps(scores, "", REL))


def test_only_variants_present_yields_no_buildsha_gaps_but_release_set_caught_elsewhere():
    # If ONLY diagnostic variants ran (no canonical persona), build-sha gaps are empty here — the
    # MISSING canonical personas are caught by the separate missing_release_personas check, not this
    # one. So scoping never hides an absent release persona.
    scores = [_score("opushi-narr", "8afed3c"), _score("opuslean-narr", "f89ce94")]
    assert rr.build_sha_evidence_gaps(scores, "033e4ba", REL) == []
