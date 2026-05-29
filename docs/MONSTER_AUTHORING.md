# Monster Authoring Skeleton

WorldOS keeps bundled SRD creatures canonical. Native authored monster packs are
additive only: they can introduce net-new monster names, but they cannot override
or shadow an SRD creature.

Future authored packs live under `data/bestiary/authored/<pack-id>/pack.json`.
Each pack manifest carries:

- `id`
- `title`
- `license`
- `source`
- `provenance`
- `monsters`

Each monster record must also carry its own explicit `license`, `source`, and
`provenance` metadata. Pack-level metadata is not enough for a committed monster
record, because records may later be copied, projected, reviewed, or exported
independently.

The initial skeleton intentionally commits no private, OGL-only, trade-dress, or
unreviewed monster records. Tests use temporary authored packs to prove the
contract:

- missing record metadata excludes the monster from the runtime index
- a same-name authored record cannot replace an SRD creature
- the player bestiary projection exposes only safe preview fields plus metadata

The player-safe projection is read-only and omits HP, AC, ability scores, action
descriptions, tactical notes, and private authoring fields.
