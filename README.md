# braincheck

A typechecker for Markdown knowledge bases. Every Markdown file is treated as a
**typed document**: its YAML frontmatter must conform to a schema (base fields +
per-`type` fields), the same way code must conform to its type declarations.

Think `tsc`, but for the frontmatter across your whole vault.

- **Zero dependencies** — one file, plain Python 3.9+. No `pip install` needed to run it.
- **Base + per-type schema** — shared fields for every doc, plus required/optional
  fields per `type` (concept, project, person, …), all declared in one `schema.yaml`.
- **Errors vs. warnings** — missing frontmatter and type violations are errors;
  unknown types/fields are warnings you can ratchet up with `--strict`.
- **Safe autofix** — `--fix` scaffolds missing frontmatter and fills derivable
  fields (dates from file timestamps, slug from filename) without touching existing values.
- **CI-friendly** — exit code `0` clean / `1` errors, plus `--json` output.

## Install

Run it directly — no install:

```bash
python braincheck.py check /path/to/vault --schema schema.yaml
```

Or install as a command:

```bash
pip install .        # provides the `braincheck` console script
braincheck --help
```

## Usage

```bash
braincheck                      # check the whole tree (cwd) against ./schema.yaml
braincheck check docs/          # check one folder
braincheck check note.md        # check one file
braincheck --fix                # scaffold missing frontmatter, fill derivable fields
braincheck --strict             # treat warnings as errors
braincheck --quiet              # errors only
braincheck --json               # machine-readable output
braincheck stats                # document counts per type + health summary
braincheck types                # print the active schema
```

Useful flags: `--schema PATH` (default `./schema.yaml`), `--root PATH` (tree to scan).

Exit codes: `0` = clean, `1` = errors found, `2` = usage/schema problem.

## The schema

`schema.yaml` is the single source of truth — the type system for your vault.

```yaml
base:
  required:
    type: string
    created: date
    updated: date
  optional:
    slug: slug
    tags: string-list

types:
  concept:
    required:
      slug: slug
      tags: string-list
      aliases: string-list
  person:
    required:
      slug: slug
      relationship: string
      last_contact: date
```

Field specs: `string`, `date`, `slug`, `url`, `list`, `string-list`, `bool`,
`number`, `any`, and `enum:a|b|c`. The `exclude:` list keeps infrastructure
folders (`.git`, templates, archives, …) out of the knowledge layer.

See [`schema.example.yaml`](schema.example.yaml) for a full, real-world schema
covering 22 document types.

## What `--fix` does (and doesn't)

**Does:** scaffold a frontmatter block onto files that have none (`type:` =
`default_type`, slug from filename, `created`/`updated` from file timestamps,
empty `tags`); add missing `created`/`updated`/`slug`/`tags`/`aliases` where derivable.

**Doesn't:** touch files with unparseable YAML, guess type-specific content
fields, or modify any existing value. Those need a human.

## Checks

| Code | Level   | Meaning                                            |
|------|---------|----------------------------------------------------|
| E001 | error   | missing YAML frontmatter                           |
| E002 | error   | frontmatter is not valid YAML                      |
| E101 | error   | missing required field                             |
| E103 | error   | field has wrong type / bad format                  |
| E106 | error   | required field is present but empty                |
| W201 | warning | unknown `type` (not declared in schema)            |
| W202 | warning | unknown field for this type                        |

## Development

```bash
python tests/test_braincheck.py     # zero-dep test runner
python -m pytest                    # if you have pytest
```

CI runs the suite on Python 3.9 / 3.11 / 3.13.

## License

MIT — see [LICENSE](LICENSE).
