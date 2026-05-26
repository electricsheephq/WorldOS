# Dice Parser Decision

Date: 2026-05-26

## Decision

Keep the ClawDnD engine on its native internal dice roller for now. Treat Avrae/d20
as reference material only, not as a runtime dependency.

The current engine dice contract is small but load-bearing:

- `DiceRoll` is the public result shape used by engine callers and MCP tools.
- `seed` must keep rolls deterministic for tests, replay, and audit trails.
- Advantage and disadvantage must follow the D&D 5e cancellation rule.
- Crit/fumble state belongs to the first single d20 test die only.
- Parser bounds must reject pathological public-tool input before it can allocate
  large roll lists or spend unbounded time parsing.

Avrae/d20 remains useful as a comparison point for notation ideas, but adopting it
would require proving that deterministic seeded behavior, result shape, first-d20
semantics, and denial-of-service bounds all survive unchanged. Until that proof
exists, adding the dependency increases integration risk without solving a current
engine bug.

## Current Boundaries

The internal parser supports:

- constants, such as `5`
- standard dice terms, such as `1d20`, `2d6`, and `d%`
- signed modifiers and multiple terms, such as `2d6+1d4+2`
- keep-highest/keep-lowest suffixes, such as `4d6kh3`
- advantage/disadvantage on the first single d20 term

The parser intentionally rejects:

- empty expressions
- zero dice
- keeping more dice than were rolled
- more than 1000 dice in one term
- die sides outside `1..1000`
- normalized expressions longer than 4096 characters

These bounds are intentionally above normal D&D use. They are not game-balance
limits; they protect the public `roll` lane from pathological inputs.

## Validation Evidence

Focused regression coverage lives in `servers/engine/tests/test_dice.py` and locks:

- deterministic seeding
- advantage/disadvantage cancellation
- first-d20 crit/fumble behavior
- percentile shorthand
- count, side, keep, and expression-length bounds

This lane did not add Avrae/d20 or any other dice dependency.

## Revisit Criteria

Revisit a third-party dice parser only if it can be wrapped behind the existing
`DiceRoll` contract and proven with focused tests to preserve:

- deterministic seeded output
- compatible roll, drop, modifier, detail, crit, and fumble fields
- current first-d20 semantics
- bounded expression length, dice count, and die sides
- no broad dependency/runtime cost for the engine MCP server
