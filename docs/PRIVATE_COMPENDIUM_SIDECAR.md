# Private Compendium Sidecar

WorldOS can plan local imports from user-owned books, adventures, exports, and
homebrew without committing or redistributing that material. The private compendium
sidecar is a local-only directory outside the git checkout. It is for metadata,
source files, converter output, and future normalized private content owned by the
person running the tool.

This lane is intentionally a scaffold, not a public content import. It must not add
private books, OGL/SRD-derived private exports, paid adventure text, user exports, or
generated corpora to the repository.

## Boundaries

- Keep the sidecar outside the repo. The default is
  `~/.worldos/private-compendium`.
- Override the sidecar root with `WORLDOS_PRIVATE_COMPENDIUM_ROOT` when needed.
- Treat `content/worlds/_private/<world-id>/...` as the only future WorldOS output
  namespace for private world material.
- Do not mutate campaign state, engine state, or public `content/worlds/*` content from
  this tool.
- Do not make Java/Quarkus, Obsidian vaults, or converter-specific indexes runtime
  authority for WorldOS. They are sidecar inputs only.

## Manifest

Create a local sidecar manifest outside the checkout:

```bash
python3 tools/ingest/private_compendium_sidecar.py --init
```

By default this writes:

```text
~/.worldos/private-compendium/private-compendium-manifest.json
```

Example manifest:

```json
{
  "schema_version": 1,
  "world_id": "my-private-world",
  "owner_acknowledgement": true,
  "sources": [
    {
      "id": "owned-source",
      "title": "Owned Source",
      "format": "markdown",
      "path": "vault/owned-source.md",
      "content_type": "lore"
    }
  ]
}
```

`owner_acknowledgement` must be `true`. It is an explicit local assertion that the
operator owns or is otherwise permitted to process the referenced private material.

## Dry Run

The current tool validates the manifest and prints planned private outputs only:

```bash
python3 tools/ingest/private_compendium_sidecar.py
```

It will reject:

- sidecar roots inside the git checkout
- path escapes such as `../outside.md`
- missing ownership acknowledgement
- unsupported content types
- duplicate or path-like source identifiers

For now, supported planned content is lore-only:

```text
content/worlds/_private/<world-id>/lore/compendium/<source-id>.md
```

That path is already gitignored and guarded by `scripts/license_check.py`.

## Future Import Step

A later importer can read this validated plan and copy or normalize owner-approved
records into gitignored private content. That later step should remain idempotent,
path-safe, and explicit about provenance:

- `private_only: true`
- source id and local source path
- import run id or timestamp
- ownership acknowledgement state
- no public redistribution license claim

Structured private monsters, items, and spells should use explicit private namespaces
and must not silently override committed SRD names.
