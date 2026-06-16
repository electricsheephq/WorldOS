#!/usr/bin/env python3
"""Tiny, dependency-free helper that computes the scorer's `prompt_construction_hash`.

WHY THIS EXISTS
---------------
`qa/score.sh` assembles a scoring prompt from (a) a lens rubric file, (b) an output
schema file, and (c) a FIXED template scaffold (section headers + the JSON-only
instruction). Over time the rubric or the scaffold can drift — and because the
scorecard number depends on the prompt the LLM saw, a silent rubric/template change
can move scores without anyone noticing. To make that drift *detectable*, we stamp a
stable fingerprint into every score JSON artifact: the SHA-256 of

    rubric-file-contents  +  schema-file-contents  +  the static template scaffold

and DELIBERATELY NOT the transcript or the engine state (those vary per run, so the
fingerprint stays constant across runs of the same lens — only rubric/template/schema
edits move it). Identical rubric+template ⇒ identical hash; edit the rubric content ⇒
the hash changes. That is exactly the property the determinism test asserts.

This is the SINGLE source of truth for the scaffold + the hash recipe so that
`score.sh` (which calls this module via `python`) and the test agree by construction —
neither re-implements the string.

ADDITIVE / SAFE: this module only reads files and prints a hex digest; it never writes
state, never touches the engine, the gateway, Eva, or any global config.
"""

from __future__ import annotations

import hashlib
import sys

# The FIXED prompt scaffold that score.sh wraps around the rubric/schema/transcript/state.
# This MUST stay byte-identical to the template string in qa/score.sh (the printf format,
# with the runtime-substituted segments — rubric/schema/transcript/state — represented by
# the {rubric}/{schema}/{transcript}/{state} placeholders). The hash covers the rubric +
# schema CONTENTS and this scaffold, but NOT the transcript/state values, so it is stable
# across runs and only moves when the rubric, schema, or this scaffold change.
PROMPT_TEMPLATE_SCAFFOLD = (
    "{rubric}\n\n"
    "# ===== OUTPUT FORMAT =====\n"
    "Respond with ONLY a single JSON object conforming to this schema — "
    "no prose, no markdown, no code fences:\n"
    "{schema}\n\n"
    "# ===== DISTILLED TRANSCRIPT =====\n"
    "{transcript}\n\n"
    "# ===== FINAL ENGINE STATE (ground truth) =====\n"
    "{state}\n"
)


def prompt_construction_hash(rubric_text: str, schema_text: str, scaffold: str = PROMPT_TEMPLATE_SCAFFOLD) -> str:
    """Return the SHA-256 hex digest of (rubric contents + schema contents + scaffold).

    The transcript and engine state are intentionally excluded — see the module docstring.
    Inputs are joined with explicit NUL separators so that no concatenation collision is
    possible (e.g. moving a trailing char between the rubric and the schema can never
    yield the same digest).
    """
    h = hashlib.sha256()
    for part in (rubric_text, schema_text, scaffold):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def hash_from_files(rubric_path: str, schema_path: str, scaffold: str = PROMPT_TEMPLATE_SCAFFOLD) -> str:
    """Read the rubric + schema files (utf-8) and return the prompt_construction_hash."""
    with open(rubric_path, encoding="utf-8") as fh:
        rubric_text = fh.read()
    with open(schema_path, encoding="utf-8") as fh:
        schema_text = fh.read()
    return prompt_construction_hash(rubric_text, schema_text, scaffold)


def main(argv: list[str]) -> int:
    """CLI: `_score_prompt_hash.py <rubric.md> <schema.json>` → prints the hex digest.

    Used by score.sh via `python …` so the shell never re-implements the recipe.
    """
    if len(argv) != 3:
        sys.stderr.write("usage: _score_prompt_hash.py <rubric.md> <schema.json>\n")
        return 2
    sys.stdout.write(hash_from_files(argv[1], argv[2]))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
