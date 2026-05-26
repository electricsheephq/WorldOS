# Settlement Pressure Skeleton

This first slice adds a clean-room settlement pressure read model for living worlds.
It is intentionally small: content may seed public civic pressure for a Location,
optionally naming public faction ids, establishments, and public NPC pressure.

The engine stores settlement rows under `Campaign.strategic_state.settlements`.
Rows are keyed by `location_id`, so they stay anchored to the existing Location map
instead of becoming a second travel graph or faction authority.

Seeded fields are:

- `location_id`
- `settlement_type`
- `governance`
- `public_safety`
- `economy`
- `unrest`
- `public_faction_ids`
- `establishments`
- `public_npcs`
- `notes`

`notes` is engine-side DM context only. Atlas projects only player-safe metadata:
settlement type, governance, public safety, economy, unrest, public faction names,
establishments, and public NPC pressure. The viewer remains read-only and continues
to expose `/move` as its only player intent lane.

Malformed settlement rows and rows with unknown location, faction, or NPC references
are skipped with diagnostics. Existing saves load unchanged because the settlements
map defaults to empty.
