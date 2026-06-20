# Playtest transcript: sample_session (committed fact-fidelity fixture)

# Fictional, self-contained reference for qa/test_fact_fidelity.py — NOT a real playtest and
# NOT derived from any world content. Raw transcripts live under the gitignored /qa/transcripts/;
# this small synthetic log is committed so the regression test is CI-safe. Three tiers by design:
# OPENING facts (survive any truncation), MID facts (lost when only the opening is kept), and
# CLIMAX+RESOLUTION facts (lost the moment the back half is cut) — incl. end-session mechanics,
# matched on their CALL RESULTS, never the tool names in the tally below.

## Tool-call tally
  speak: 9
  skill_check: 5
  award_xp: 2
  adjust_reputation: 1
  add_quest: 1
  record_decision: 1
  end_session: 1

## Play log

OPENING — Greyharbor docks, a cold grey morning. The hero arrives chasing a stolen courier packet.

The hero is Sera Vey, a Half-Elf Ranger, level 4, sworn to the Wardens of the Reach.
Beside her walks Brother Tomas, a quiet cleric who has seen too many winters.
At the customs shed waits Old Hessa, the harbor archivist, who keeps a ledger of every ship.

Hessa speaks first: "The courier you want is Coren. Was Coren. They pulled him from the tide two nights past."
She slides a copper token across the counter — an old Wardens token, the kind they stopped minting years ago.
  - `skill_check(insight, dc 13)` -> roll 18, success
Sera reads the token's wear and knows it is genuine. Coren carried it. Coren is dead.

MID — the cipher and the seal.

Tomas unfolds the recovered cipher sheet. One name sits in the clear where the code should be: Lady Ashryn.
Wrapped beside it, a Silverwatch officer's seal — the kind only a records clerk would hold.
A dockside fixer named Dob, who runs errands for the Combine, steps from the fog with terms.
  - `skill_check(investigation, dc 14)` -> roll 16, success
Sera examines the wax. The seal on the packet is HER seal — the Wardens mark she carries in her coat.
Someone built this trail and put her name at the end of it. She is not the investigator. She is the frame.

CLIMAX — the warehouse, and the one who built the road.

The warehouse door opens and Veyl Marrow steps through. The architect. Tomas whispers: "This is the hand."
Veyl lays it bare: the packet hid the Ledger of Thirty-One names — every Reach survivor and the Wardens who sheltered them.
"If that ledger reaches the Spindle, every patron who profited starts burning evidence." She does the arithmetic without flinching.
  - `record_decision(...)` -> {"chosen": "Take the ledger to the Spindle quietly; let Veyl name the rest in one week."}

RESOLUTION — the close.

Sera takes the Ledger of Thirty-One. Veyl walks free with seven days to name the patrons.
  - `award_xp(Sera, 400)` -> {"xp": 400, "current_level": 4, "reason": "Traced the packet, broke the frame, secured the Thirty-One"}
  - `award_xp(Tomas, 300)` -> {"xp": 300, "current_level": 4, "reason": "Held the line; read the trap"}
  - `adjust_reputation(fac-wardens, +7)` -> {"reputation": 7, "reason": "Recovered the ledger without a body"}
  - `add_quest("The Thirty-One Names")` -> {"title": "The Thirty-One Names", "status": "active"}
  - `end_session(...)` -> {"ended": "session-greyharbor-1", "number": 1}

**Session 1 — closed.** Sera Vey came for a stolen packet. The packet was never the point. The road to the Spindle is open.
