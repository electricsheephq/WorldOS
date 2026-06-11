"""Regressions for the pre-release adversarial audit (GitHub issues #40-#55).

Each test mirrors the issue's repro sketch so a fix is provably tied to a filed finding.
Grouped by area; the issue number is in each test name.
"""
import importlib.util
from pathlib import Path

import pytest

import combat
import content
import server
import store
from models import Character

_ROOT = Path(__file__).resolve().parents[3]


def _license_check():
    spec = importlib.util.spec_from_file_location("clawdnd_license_check", _ROOT / "scripts" / "license_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── #40/#41 engine-state: path containment + stable character id ──
def test_issue40_path_like_ids_cannot_escape_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path / "state"))
    for bad in ("../../escape", "/tmp/abs", "..", "a/b", ""):
        with pytest.raises(ValueError):
            with store.campaign_lock(bad):
                pass
    assert not (tmp_path / "escape").exists()  # no lock dir leaked outside the root
    with pytest.raises(ValueError):
        content.load_world_data("../../../etc")


def test_issue41_update_character_cannot_change_the_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("ids")["id"]
    old = server.create_character(cid, "Hero")["id"]
    server.update_character(cid, old, {"id": "visible_unusable", "armor_class": 15})
    assert server.get_state(cid)["party"][0]["id"] == old  # id stayed the stable handle
    assert server.get_character(cid, old)["name"] == "Hero"  # still usable under it
    assert server.get_character(cid, old)["armor_class"] == 15  # the rest of the patch applied


def _campaign():
    return server.create_campaign("adv")["id"]


# ── #42-#45 mechanics: conditions enforce action/save/immunity + concentration ──
def test_issue42_incapacitated_cannot_act_or_attack(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    target = server.create_character(cid, "Target", kind="monster")["id"]
    server.start_combat(cid, [actor, target])
    server.add_condition(cid, actor, "unconscious")
    assert server.use_action(cid, actor, "action")["ok"] is False
    with pytest.raises(ValueError):
        server.attack(cid, actor, target, attack_bonus=99, damage_dice="1")


def test_issue42_incapacitated_cannot_cast_a_spell(tmp_path, monkeypatch):
    # Extends #42 (review finding): casting is an action, so an incapacitated caster can't cast —
    # and the slot must NOT be spent on the refused cast.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    w = server.create_character(cid, "Wiz", kind="player", class_name="Wizard",
                                level=3, apply_srd_defaults=True)["id"]
    server.learn_spells(cid, w, ["Magic Missile"])
    server.add_condition(cid, w, "stunned")
    with pytest.raises(ValueError):
        server.cast_spell(cid, w, "Magic Missile", slot_level=1)
    assert server.get_character(cid, w)["spell_slots"]["1"]["used"] == 0  # slot preserved


def test_issue43_condition_saves_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    server.add_condition(cid, actor, "unconscious")
    out = server.saving_throw(cid, actor, "dex", 1)  # would auto-succeed on the roll
    assert out["success"] is False and "condition" in out["reason"]
    # a CON save is unaffected by these conditions (only STR/DEX auto-fail).
    assert "reason" not in server.saving_throw(cid, actor, "con", 1)


def test_issue43_restrained_gives_dex_save_disadvantage(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    server.add_condition(cid, actor, "restrained")
    assert server.saving_throw(cid, actor, "dex", 10).get("disadvantage") is True


def test_issue44_condition_immunity_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = _campaign()
    mon = server.create_character(cid, "ImpX", kind="monster")["id"]
    server.update_character(cid, mon, {"condition_immunities": ["poisoned"]})
    out = server.add_condition(cid, mon, "poisoned")
    assert out["immune"] is True and "poisoned" not in out["conditions"]


def test_issue45_temp_hp_does_not_suppress_concentration_check():
    ch = Character(name="Caster", max_hp=20, current_hp=20, temp_hp=30, concentration="Bless")
    out = combat.apply_damage(ch, 30)  # all absorbed by temp HP, but damage was still taken
    assert out["concentration_dc"] == 15


# ── #52/#53 licensing gate ──
def test_issue52_campaign_private_path_is_forbidden(monkeypatch):
    lc = _license_check()
    assert "content/campaigns/_private/" in lc.FORBIDDEN_PREFIXES
    monkeypatch.setattr(lc, "tracked_files",
                        lambda: ["content/campaigns/_private/secret/adventure.json"])
    assert lc.main() == 1  # a committed private campaign trips the gate


def test_issue53_ingested_record_without_attribution_is_caught(tmp_path):
    lc = _license_check()
    # an ingested character record with no license/attribution should be flagged.
    rec = tmp_path / "content" / "worlds" / "w" / "characters" / "bad.json"
    rec.parent.mkdir(parents=True)
    rec.write_text('{"name": "Nameless"}', encoding="utf-8")
    lc.ROOT = tmp_path
    errs = lc._check_ingested_attribution(["content/worlds/w/characters/bad.json"])
    assert errs and "license/attribution" in errs[0]
    # one that carries license + attribution passes.
    rec.write_text('{"name": "X", "license": "CC-BY-SA 4.0", "attribution": "Wiki"}', encoding="utf-8")
    assert lc._check_ingested_attribution(["content/worlds/w/characters/bad.json"]) == []


# ── #46/#48 canon-memory: ending threads projection + ledger kind filter ──
def test_issue46_ending_response_threads_match_the_ending(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    out = server.start_world("baldurs-gate", ending="gortash-tyranny")
    joined = " ".join(out["standing_threads"])
    # the base thread says Gortash is dead + the Steel Watch gone — contradicted by the ending.
    assert "with Gortash dead and the Steel Watch gone" not in joined
    assert out["standing_threads"]  # the post-overlay threads are surfaced, not an empty list


def test_issue48_ledger_kind_filter_is_pre_limit(tmp_path, monkeypatch):
    import ledger
    from store import load_campaign, save_campaign
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("noisy")["id"]
    for i in range(50):  # flood with same-matching session events
        server.log_event(cid, "narration", f"Pale Choir noisy event {i}")
    c = load_campaign(cid)
    c.lore.append("Pale Choir authoritative lore row")
    save_campaign(c)
    ledger.backfill(cid)
    hits = ledger.recall(cid, "Pale Choir", kinds=["lore"], limit=1)
    assert hits and hits[0]["kind"] == "lore"  # not starved by the 50 narration rows


# ── #47/#50/#51 content + prompt contracts ──
def test_issue47_all_baldurs_gate_regions_reachable_from_start(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data("baldurs-gate"))
    start = c.current_location_id
    seen, frontier = {start}, [start]
    while frontier:  # BFS over the (now-undirected) travel graph
        loc = c.locations.get(frontier.pop())
        for nb in (loc.connections if loc else []):
            if nb in c.locations and nb not in seen:
                seen.add(nb); frontier.append(nb)
    unreachable = [lid for lid in c.locations if lid not in seen]
    assert "loc-candlekeep" in seen  # the shipped "Candlekeep's secret" hook is reachable
    assert not unreachable, f"stranded regions: {unreachable}"


def test_issue50_play_party_prompt_uses_existing_campaign_not_start_world():
    text = (_ROOT / "scripts" / "play_party.sh").read_text(encoding="utf-8")
    # the DM-facing opening must not tell the DM to start_world after the campaign is pre-seeded.
    assert 'start_world(\\"$WORLD\\") and read the returned bible' not in text
    assert "DO NOT call start_world" in text and "campaign_id=$CAMPAIGN_ID" in text


def test_coldopen_play_party_guards_player_pc_seating():
    """Cold-open reliability: play_party.sh (the .app's path) creates the human PC live in a
    DM-stochastic turn. A forensic run minted can_act:true but the DM never seated a PC →
    viewer readiness=degraded / no_actor. Guard the miss: detect a seated player actor after
    the cold open, retry once, and FAIL LOUD rather than silently hand a no_actor session."""
    text = (_ROOT / "scripts" / "play_party.sh").read_text(encoding="utf-8")
    # (1) the prompt mandates seating the PC (kind="player", add_to_party) up front.
    assert 'create_character with kind=\\"player\\" and add_to_party=true' in text
    # (2) a snapshot-backed guard exists and matches the viewer's _action_actor notion of a
    #     seated PC (a party member whose record is kind="player"). F12-3 (#777) factored the
    #     guard into the SHARED lib (clawdnd_pc_seated) so play.sh runs the identical check —
    #     the _action_actor-matching python now lives there, called from this lane.
    assert "clawdnd_pc_seated" in text
    lib = (_ROOT / "qa" / "lib_beat_driver.sh").read_text(encoding="utf-8")
    assert "clawdnd_pc_seated()" in lib
    assert 'get("kind") == "player"' in lib
    # (3) the guard retries the cold open ONCE, then aborts loudly on a still-unseated party.
    assert "retrying the cold open ONCE" in text
    assert "COLD-OPEN SEATED NO PC" in text and "exit 1" in text


def test_coldopen_part_a_poll_window_outlasts_max_effort_coldopen():
    """Cold-open reliability: the Part-A (#356) mint poll must outlast the max-effort cold open
    (~280–400s). The old 210s window (70 × 3s) was a spurious FAIL; the deadline is now a
    420s-default, env-overridable knob."""
    text = (_ROOT / "qa" / "ui_playtest_app.sh").read_text(encoding="utf-8")
    assert 'PART_A_DEADLINE="${WOS_APP_PART_A_DEADLINE:-420}"' in text
    # the poll loop derives its iteration count from the deadline (no more hardcoded `seq 1 70`).
    assert "part_a_polls=$(( PART_A_DEADLINE / 3 ))" in text
    assert 'for i in $(seq 1 "$part_a_polls"); do' in text
    assert "for i in $(seq 1 70); do" not in text


def test_issue51_campaign_new_command_creates_one_campaign():
    text = (_ROOT / "commands" / "campaign-new.md").read_text(encoding="utf-8")
    assert "create_campaign` to get a campaign id, then" not in text  # the two-campaign instruction
    assert "start_adventure(adventure_id)" in text and "Do NOT call `create_campaign` first" in text


def test_any_skill_class_resolves_to_concrete_skills():
    # QA (optimizer crit, sweep_v7): a "choose any N skills" class (Bard's class_skills =
    # {count:3, from:['any']}) must persist CONCRETE skill proficiencies, not the literal
    # ['any'] placeholder — which matches no skill and rendered 0 proficiencies on the sheet,
    # making a min-maxer bail. Creation now expands 'any' to the real skill pool.
    cid = _campaign()
    bard = server.create_character(cid, "Lute", kind="player", class_name="Bard",
                                   level=1, apply_srd_defaults=True)["id"]
    ch = server.get_character(cid, bard)  # FULL sheet — get_state party is only a vitals summary
    skills = ch.get("skill_proficiencies") or []
    assert skills, "Bard should have default skill proficiencies, not an empty sheet"
    assert "any" not in skills, f"unresolved 'any' placeholder persisted: {skills}"
    assert len(skills) == 3, f"Bard should get 3 concrete skills, got {skills}"
