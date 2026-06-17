"""author_companion_gauges — give a freely-recruited / live-generated companion an approval
VOCABULARY (and an optional betrayal agenda) so their relationship gauge can MOVE on the player's
choices.

The gap (the generalize investigation, 2026-06-17): a freely-recruited / generated companion is
seeded with an EMPTY approval vocabulary, so record_decision(approval_tags=...) SKIPS them and their
regard stays narrated-not-gauged — the proven golden-spine engagement only worked because CONTENT
authored these lists (Brother Toll / Sergeant Ondine). This tool lets the DM author them at recruit
time (cued every beat by the companion_gauge_unauthored obligation). RED before authoring; GREEN
after — the whole 'generated play gets gauged' bridge.

Single-process:
    uv run --directory servers/engine python -m pytest tests/test_author_companion_gauges.py -p no:xdist
"""
import server
import store


def _vocabless_companion(bg: str) -> str:
    """Create a companion the LIVE way — it gets the minimal seeded dossier (empty approval vocab)
    + the default loyalty arc, exactly like a freely-recruited one."""
    out = server.create_character(
        bg, name="Garran the Free", kind="companion", race="Human",
        class_name="Fighter", level=2, max_hp=18,
        biography="A hired blade with a careful, transactional way about him.",
    )
    return out["id"]


def test_vocabless_companion_then_gauged_after_authoring(tmp_path, monkeypatch):
    """RED->GREEN end-to-end: a freshly-created companion can't be moved by a tagged decision
    (empty vocab -> no approval_results row); after author_companion_gauges the SAME decision moves
    their attitude and reports a row, and the betrayal agenda is armed."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    comp_id = _vocabless_companion(bg)

    before = server.get_character(bg, comp_id)["attitude_value"]
    out0 = server.record_decision(bg, summary="a merciful choice", approval_tags=["mercy"])
    row0 = next((r for r in out0.get("approval_results", []) if r["id"] == comp_id), None)
    assert row0 is None, "a vocab-less companion must NOT be movable by a tagged decision (the gap)"

    res = server.author_companion_gauges(
        bg, companion_id=comp_id,
        approval_likes=["mercy", "protecting the weak"],
        approval_dislikes=["needless cruelty"],
        betrayal_threshold=-30, betrayal_decision_flag="took_the_blood_money",
    )
    assert res["approval_likes"] == ["mercy", "protecting the weak"]
    assert res["betrayal_agenda_armed"] is True

    out1 = server.record_decision(bg, summary="a merciful choice", approval_tags=["mercy"])
    row1 = next((r for r in out1.get("approval_results", []) if r["id"] == comp_id), None)
    assert row1 is not None, "after authoring, a 'mercy' decision must move the gauge"
    assert row1["new_value"] > before

    comp = store.load_campaign(bg).characters[comp_id]
    assert comp.arc is not None and comp.arc.agenda is not None
    assert comp.arc.agenda.trigger == "attitude_below" and comp.arc.agenda.value == -30
    assert comp.arc.agenda.decision_flag == "took_the_blood_money"


def test_author_is_additive_preserves_camp_prompts(tmp_path, monkeypatch):
    """Only the fields passed are written; the seeded camp_prompts and un-passed vocab survive."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    comp_id = _vocabless_companion(bg)
    seeded_prompts = list(store.load_campaign(bg).characters[comp_id].companion_dossier.camp_prompts)
    assert seeded_prompts, "the seeded dossier should carry a camp_prompt from the biography"

    server.author_companion_gauges(bg, companion_id=comp_id, approval_likes=["honor"])
    d = store.load_campaign(bg).characters[comp_id].companion_dossier
    assert d.approval_likes == ["honor"]
    assert d.camp_prompts == seeded_prompts   # preserved
    assert d.approval_dislikes == []          # not passed -> unchanged (empty)


def test_betrayal_threshold_must_be_negative(tmp_path, monkeypatch):
    """A non-negative betrayal_threshold would arm an agenda that betrays a neutral (attitude 0)
    companion immediately — reject it at the seam."""
    import pytest
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    comp_id = _vocabless_companion(bg)
    for bad in (0, 20, 100):
        with pytest.raises(ValueError, match="NEGATIVE"):
            server.author_companion_gauges(bg, companion_id=comp_id, approval_likes=["mercy"],
                                           betrayal_threshold=bad)
    # the dossier was still written for the first (pre-agenda) call? no — the raise is BEFORE the
    # lock, so nothing was written; a clean negative threshold works:
    res = server.author_companion_gauges(bg, companion_id=comp_id, approval_likes=["mercy"],
                                         betrayal_threshold=-25)
    assert res["betrayal_agenda_armed"] is True


def test_reauthor_preserves_agenda_decision_flag(tmp_path, monkeypatch):
    """Re-arming to re-tune the threshold preserves the existing agenda's decision_flag (additive)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    comp_id = _vocabless_companion(bg)
    server.author_companion_gauges(bg, companion_id=comp_id, approval_likes=["mercy"],
                                   betrayal_threshold=-30, betrayal_decision_flag="took_the_coin")
    # re-author with a new threshold but NO flag — the flag must survive
    server.author_companion_gauges(bg, companion_id=comp_id, betrayal_threshold=-20)
    ag = store.load_campaign(bg).characters[comp_id].arc.agenda
    assert ag.value == -20 and ag.decision_flag == "took_the_coin"


def test_author_without_threshold_leaves_agenda_unarmed(tmp_path, monkeypatch):
    """Omitting betrayal_threshold deepens-but-never-turns: no agenda is armed (the default seeded
    loyalty arc stays agenda-less)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    bg = server.start_world("baldurs-gate")["campaign_id"]
    comp_id = _vocabless_companion(bg)
    res = server.author_companion_gauges(bg, companion_id=comp_id, approval_likes=["mercy"])
    assert res["betrayal_agenda_armed"] is False
    comp = store.load_campaign(bg).characters[comp_id]
    assert comp.arc.agenda is None
