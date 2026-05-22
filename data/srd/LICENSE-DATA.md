# SRD 5.2.1 Data License & Attribution

The full rules dataset vendored under `data/srd/srd524/` is the **System
Reference Document 5.2.1 ("SRD 5.2.1")** published by Wizards of the Coast LLC.
It is licensed under the **Creative Commons Attribution 4.0 International
License (CC-BY-4.0)**.

(The smaller hand-authored starter set in `data/srd/*.json` is covered by the
same SRD attribution — see `data/srd/ATTRIBUTION.md`.)

## License

- **License:** Creative Commons Attribution 4.0 International (CC-BY-4.0)
- **License text / legal code:** https://creativecommons.org/licenses/by/4.0/legalcode
- **Human-readable summary:** https://creativecommons.org/licenses/by/4.0/

CC-BY-4.0 permits sharing and adaptation, including for commercial purposes, so
long as appropriate credit is given, a link to the license is provided, and any
changes are indicated. The license is irrevocable.

> ### License summary (CC-BY-4.0)
>
> **You are free to:**
> - **Share** — copy and redistribute the material in any medium or format.
> - **Adapt** — remix, transform, and build upon the material for any purpose,
>   even commercially.
>
> **Under the following terms:**
> - **Attribution** — You must give appropriate credit, provide a link to the
>   license, and indicate if changes were made. You may do so in any reasonable
>   manner, but not in any way that suggests the licensor endorses you or your
>   use.
> - **No additional restrictions** — You may not apply legal terms or
>   technological measures that legally restrict others from doing anything the
>   license permits.

## Required attribution (Wizards of the Coast)

The following attribution is **required by CC-BY-4.0** and must be retained when
this data is redistributed:

> This work includes material from the System Reference Document 5.2.1
> ("SRD 5.2.1") by Wizards of the Coast LLC, available at
> https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative
> Commons Attribution 4.0 International License, available at
> https://creativecommons.org/licenses/by/4.0/legalcode.

## Changes made

This dataset has been **converted from the original SRD 5.2.1 document into
structured JSON** by the Open5e project (the conversion source below), and is
vendored here unmodified from that source. ClawDnD's rules server further
*adapts* these records at load time (field normalization into a lookup index);
the on-disk JSON under `data/srd/srd524/` is a verbatim copy of the upstream
Open5e files.

## Conversion source (Open5e)

The JSON structuring of the SRD 5.2.1 used here comes from the **Open5e** open
data project, which converts the SRD into machine-readable JSON:

- **Project:** Open5e — https://github.com/open5e/open5e-api
- **Source path:** `data/v2/wizards-of-the-coast/srd-2024/`
- **Pinned commit:** `c4eeac38512401c70ed58df204cadc1511f2b0de`
- **License of the source data:** the upstream files self-declare
  `"licenses": ["cc-by-40"]` in `Document.json`, matching the SRD 5.2.1 license
  above.

Open5e's conversion work is gratefully acknowledged. Open5e is not affiliated
with, nor does it endorse, ClawDnD.

## Scope of what is vendored

Only the **Wizards of the Coast `srd-2024`** set is vendored. The upstream
`srd-2014` (OGL) tree and all third-party-publisher folders (Kobold Press,
Green Ronin, EN Publishing, Open5e-original content, etc.) are intentionally
**excluded** so that this directory contains exclusively the CC-BY-4.0 SRD
5.2.1 material.

Vendored categories: spells, creatures (monsters), magic items, equipment
(items / weapons / armor), conditions, feats, backgrounds, species, classes,
and the rules/rule-sections. A few very large or non-essential upstream files
(per-level class-feature granules, vendor services, ancillary description
tables) were skipped to keep the bundle compact.
