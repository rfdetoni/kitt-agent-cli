# Naming, License and Trademark Notes

K.I.T.T. Agent CLI source code is distributed under the repository's MIT license. A software license and a trademark/right-of-publicity analysis are separate questions.

## Current policy

- Do not claim affiliation, sponsorship or endorsement by the owners of *Knight Rider* or related marks.
- Do not import third-party logos, character artwork, audio, screenshots or other branded assets without an appropriate license.
- Keep product descriptions focused on the project's own functionality.
- Treat the spelling/name `K.I.T.T.` as a naming risk to be reviewed before commercial branding, trademark registration or broad public marketing.
- Do not perform an automated rebrand across the ecosystem merely because a risk exists; a rename affects package names, CLI commands, URLs, protocol identifiers, documentation and user expectations and therefore requires an explicit migration plan.

## Decision boundary

A definitive trademark/legal conclusion requires qualified human/legal review in the jurisdictions and commercial context that matter to the project. Repository automation should only preserve factual licensing metadata and avoid unsupported claims.

If a rename is ever approved, stage it as a compatibility migration: introduce new public names/aliases first, keep documented compatibility shims for the agreed window, then remove legacy naming in a later version.
