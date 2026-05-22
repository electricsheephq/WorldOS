---
description: Roll dice through the deterministic engine (e.g. /roll 2d6+3, /roll d20 advantage).
argument-hint: "<expression> e.g. 2d6+3, d20 advantage, 4d6kh3"
allowed-tools: mcp__clawdnd-engine__roll
---
Roll: $ARGUMENTS

Call `clawdnd-engine` `roll` with this expression and report the result — show each die face, the modifier, and the total. If the player asked for advantage or disadvantage, pass it through. This is a real, auditable roll from the engine, not an imagined number.
