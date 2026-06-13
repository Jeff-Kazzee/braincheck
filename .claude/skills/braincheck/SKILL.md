---
name: braincheck
description: >-
  Typecheck and fix YAML frontmatter across a Markdown knowledge base / vault
  (Obsidian, docs, notes). Use when the user wants to validate frontmatter,
  enforce a metadata schema, find Markdown files with missing or malformed
  frontmatter, audit a vault's structure, add a new document type, or scaffold
  frontmatter onto files that lack it. Triggers: "check my frontmatter",
  "validate the vault", "enforce a schema on my notes", "what types of docs do
  I have", "fix missing frontmatter", "lint my Markdown metadata".
---

# braincheck

`braincheck` is a zero-dependency Python CLI that treats every Markdown file as a
**typed document**: its YAML frontmatter must conform to `schema.yaml` (shared
base fields + per-`type` fields). It is `tsc` for a knowledge base. This skill
teaches you to operate it on the user's behalf.

## Prerequisites

- Python 3.9+ (`python` or `python3` on PATH). No packages to install.
- A `braincheck.py` and a `schema.yaml` (start from `schema.example.yaml`).
- Run from the vault root, or pass `--root` to point at the tree to scan.

If the user has installed it (`pip install .`), the command is `braincheck`.
Otherwise invoke the script directly: `python braincheck.py ...`. Detect which by
checking for `braincheck.py` in the repo vs. `braincheck` on PATH.

## The mental model

- **Base fields** apply to every document. By default `type`, `created`,
  `updated` are required.
- **Per-type fields**: each value of `type:` (e.g. `concept`, `project`,
  `person`) declares its own required/optional fields under `types:` in
  `schema.yaml`.
- **`schema.yaml` is the single source of truth.** To change the rules, edit
  the schema — never hard-code rules elsewhere.

## Commands

```bash
braincheck                       # check whole tree (cwd) vs ./schema.yaml
braincheck check PATH...         # check specific files or folders
braincheck check --fix           # safe autofix (see below)
braincheck check --strict        # warnings become errors
braincheck check --quiet         # hide warnings, show errors only
braincheck check --json          # machine-readable: {checked, errors, warnings, findings[]}
braincheck stats                 # document counts per type + health summary
braincheck types                 # print the active schema
```

Flags: `--schema PATH` (default `./schema.yaml`), `--root PATH` (tree root).
Exit codes: `0` clean, `1` errors found, `2` usage/schema problem. Use the exit
code in scripts and CI rather than parsing text.

## Finding codes

| Code | Level   | Meaning |
|------|---------|---------|
| E001 | error   | missing YAML frontmatter block |
| E002 | error   | frontmatter is not valid YAML |
| E101 | error   | missing required field |
| E103 | error   | field has wrong type / bad format (bad date, bad slug, …) |
| E106 | error   | required field present but empty |
| W201 | warning | unknown `type` (not declared in schema) |
| W202 | warning | unknown field for this type |

Note: unknown **fields** are intentionally *not* flagged on documents whose
`type` is itself unknown — the schema can't say what fields that type allows.
Declare the type first, then field warnings appear.

## Recommended workflow

1. **Survey first.** Run `braincheck stats` to see the type distribution and how
   many files are clean vs. erroring. Types marked `(not in schema)` are
   candidates to either add to the schema or rename in the files.
2. **Triage with JSON.** Run `braincheck check --json` and group findings by
   `code` to understand the dominant problems before changing anything.
3. **Decide: fix the files, or fix the schema.** A flood of W201/W202 usually
   means the schema is behind reality — add the missing types/fields. A flood of
   E001/E101 means the files need frontmatter — use `--fix`.
4. **Autofix the mechanical cases.** `braincheck check --fix` then re-run check.
5. **Re-run until clean** (or until only intentional warnings remain). For CI,
   gate on exit code, optionally with `--strict`.

## What `--fix` does — and what it never does

Does, safely:
- Scaffolds a frontmatter block onto files that have none (`type:` =
  `default_type`, `slug` from filename, `created`/`updated` from file
  timestamps, empty `tags`).
- Adds missing **derivable** base fields: `created`, `updated`, `slug`, `tags`,
  `aliases`.

Never:
- Touches files whose YAML doesn't parse (E002) — fix those by hand.
- Guesses type-specific content fields (e.g. `platform`, `relationship`).
- Modifies any existing value.

So after `--fix`, expect remaining E101s for type-specific required fields. Fill
those in collaboration with the user — don't invent values.

## Editing the schema (the common request)

To add a document type:

```yaml
types:
  meeting:
    required:
      slug: slug
      date: date
      attendees: string-list
    optional:
      location: string
```

Field specs: `string`, `date` (YYYY-MM-DD), `slug` (lowercase `a-z 0-9 . _ -`),
`url`, `list`, `string-list`, `bool`, `number`, `any`, and `enum:a|b|c`. To make
a field tolerant (e.g. a date that carries a time/timezone), use `string` or
`any` instead of `date`. Use the top-level `exclude:` list to keep
infrastructure folders (`.git`, `_templates`, archives, build dirs) out of the
knowledge layer. After editing, run `braincheck types` to confirm it parsed,
then `braincheck check`.

## Guardrails for the agent

- **Schema changes vs. file edits are different decisions.** Prefer editing the
  schema when the files reflect a real, intentional convention; prefer editing
  files when they're genuinely malformed. When unsure, ask the user.
- **Run `--fix` on a clean working tree** (committed or backed up) so the user
  can review the diff. It edits files in place.
- **Don't fabricate field values.** Dates/slugs are derivable; semantic fields
  are not.
- **Large vaults:** scope with `braincheck check subfolder/` while iterating;
  run the full tree only to confirm.
