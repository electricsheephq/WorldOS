# The Embergloom Pact

*A ClawDnD showcase campaign — SRD 5.2, levels 1 → 5, ~4 sessions.*
*100% original prose on SRD primitives. Ships under the root WorldOS license unless a separate content license is stated.*

---

## The Pitch

A grey ashfall is settling over the hill-town of **Cinderhollow** — souring wells, withering
crops, laying a creeping grey sleep over anyone who breathes it too long. The town's beloved
apothecary, **Sister Velandra Coir**, hands out cures with one hand while feeding the source of the
blight with the other: a buried entity called the **Embergloom**, an old hunger sealed beneath the
hills, which she has pledged to wake in exchange for never having to die.

She is the warmest person in town and the one murdering it. The party won't suspect her until the
clues force them to — and by then she'll be in the crypt, finishing the rite.

## The Hidden Antagonist

**Sister Velandra Coir, "the Ash-Mother."** Not a cackling villain — a grieving woman who has
buried everyone she ever loved and decided death is the only true enemy. The Embergloom's bargain
is the first honest answer she's ever been offered, and she means to be *loved by the town to the
very end*, so she works entirely through proxies and never shows her own hand until Act 3.

- **Public face:** kind healer, never sick though always among the sick. Her real cure smells of
  the very rot it cures (the central clue).
- **Lieutenant:** **Maelis Vane**, a charming recruiter who finds the desperate and offers them
  grey coin and a place in "the Mother's flock." Recurs across all three acts.
- **The reveal:** lands at the end of Act 2 — the captive acolyte **Wren** has seen the Ash-Mother's
  face but never knew it was the town apothecary. Show her a token of Velandra's and the mask falls.
- **Stats (Act 3 only):** reflavored **Wight** (AC 14, HP 82) — a half-ashen herald, Life Drain →
  "Ashen Touch."

## The Companion

**Brother Toll** (Cleric 1, `companion-default`) — a weathered road-warden of the **Ember-Watch**,
the old order that once kept vigil over the buried thing. He left his hermitage when the ash began
to fall, knowing what it means. Calm, dry, iron-steady; carries the guilt of an order that failed
once and won't fail twice. He's the party's slow-drip lore source — but he does *not* suspect
Velandra at first (she's well-liked, and he wants the order to have a friend in town). His arc
culminates at the sealing-stone, where his order's litany is the re-binding key.

## Structure — Hub & Three Spokes

The party returns to **Cinderhollow (The Hearth)** between acts to resupply, level, and slowly learn
who's poisoning the well of their trust. One site per act radiates from the hub.

| Act | Levels | Site | The Beat |
|----|--------|------|----------|
| **1 — Grey Bread, Grey Water** | 1–2 | **Hollowmere Mill** | The fouled mill: rescue the dazed miller, find Maelis's tally, fight the cult cell tending the millrace. Maelis flees, name-dropping "the Ash-Mother." |
| **2 — The Defiled Barrow** | 2–4 | **The Ashen Barrow** | A small dungeon past the dead Smolderwood: defiled seal-wards, risen barrow-dead, free the turncoat Wren (**the reveal**), and stop Maelis's work-gang breaking the first of three seals. |
| **3 — The Embergloom Pact** | 4–5 | **The Embergloom Crypt** | The mask has fallen; Velandra's gone to finish the rite. Descend to the sealing-stone, race the **rite-clock**, and break the pact before the Embergloom draws its first full breath. |

Each act exercises **exploration + social + combat**. Diplomatic and stealth resolutions earn full
XP — overcoming a challenge by talking or slipping past it counts.

## Monsters per Act (all bundled SRD stat blocks, CR-tuned)

- **Act 1 (lv 1–2):** Cultist ×3 (CR 1/8) + Skeleton ×1 (CR 1/4, a risen mill-hand). Maelis flees.
- **Act 2 (lv 2–4):** Maelis Vane = **Bandit Captain** (CR 2, mid-boss) + Cultist ×3 + Skeleton ×2
  (barrow-guardians).
- **Act 3 (lv 4–5):** *Descent:* Specter ×2 (CR 1) + Ogre ×1 (CR 2, an ash-bloated thrall).
  *Climax:* **Velandra = Wight** (CR 3) + Maelis (Bandit Captain, if he fled here) + Specter/Ogre
  support, against a **rite-clock**.

Every name resolves through `spawn_monster` / the bundled bestiary. Per-scene scaling notes tune
each fight to party size.

## The Embergloom Itself — Never Seen, Never Fought

The buried entity is the ashfall's true source and the rite's prize: a presence of grey hunger felt
as rising ash, ember-light, and mounting dread. **Its waking is the failure state; its re-sealing is
the victory.** Do *not* stat it. Velandra (the Wight) is the human anchor the party actually fights;
the Embergloom is the reason the fight matters. Two win conditions keep the finale open to any party:
**kill the anchor** *or* **re-bind the stone** (Arcana/Religion + Brother Toll's Ember-Watch litany).

## How It Ends

Dawn over a clearing sky, the ash thinning, the wells running clean. Reeve Stoke grieves the friend
who was a monster and the monster who was a friend. The party decides how the valley remembers it —
slayers of a deathless herald, or those who unmasked a desperate, grieving woman and held the line
anyway. The Embergloom is sealed for another age, **not destroyed** — a deliberate dangling thread
for a higher-tier return. *A seal is not a grave.*

---

### Running Notes

- **Leveling:** milestone recommended — level 2 after the mill, 3–4 across the barrow, 5 before/during
  the crypt climax (~2,800 XP total if you prefer encounter XP).
- **The ashfall fog** is a flavorful CON-save hazard (grey-fog → disadvantage), not a death-trap.
- **Velandra can be *reached*** at the climax (a hard DC 17 Persuasion with the faces of those she's
  killed) — she falters in the rite, or at an exceptional table re-seals the stone with her own death.
  Reserve the full turn for genuine brilliance; the default is stopping her by force + re-binding.
- **Validation:** `generate_campaign`-shaped (hidden antagonist, hook/challenge/climax beats, hub +
  one site per act). `validate_adventure()` returns `[]`; `seed_campaign()` loads cleanly with Brother
  Toll in the party.
