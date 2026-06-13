"""Tests for braincheck. Run with: python -m pytest  (or plain `python tests/test_braincheck.py`)."""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import braincheck  # noqa: E402

SCHEMA = os.path.join(HERE, "fixtures", "schema.yaml")
VAULT = os.path.join(HERE, "fixtures", "vault")


def run(paths, **kw):
    schema = braincheck.load_schema(SCHEMA)
    findings = []
    for full, rel in braincheck.iter_markdown(VAULT, [os.path.join(VAULT, p) for p in paths], schema.exclude):
        f, _ = braincheck.check_file(full, rel, schema)
        findings.extend(f)
    return findings


def codes(findings):
    return sorted(f.code for f in findings)


# --- YAML parser ---------------------------------------------------------

def test_parser_inline_list():
    d = braincheck.strip_meta(braincheck.parse_yaml("tags: [a, b, c]"))
    assert d["tags"] == ["a", "b", "c"]


def test_parser_block_list():
    d = braincheck.strip_meta(braincheck.parse_yaml("tags:\n  - a\n  - b\n"))
    assert d["tags"] == ["a", "b"]


def test_parser_empty_map():
    d = braincheck.strip_meta(braincheck.parse_yaml("dashboard: {}"))
    assert d["dashboard"] == {}


def test_parser_rejects_tabs():
    try:
        braincheck.parse_yaml("a:\n\t- x")
        assert False, "should have raised"
    except braincheck.YamlError:
        pass


# --- field type checks ---------------------------------------------------

def test_date_check():
    assert braincheck.check_field_type("2026-06-01", "date") is None
    assert braincheck.check_field_type("nope", "date") is not None


def test_slug_check():
    assert braincheck.check_field_type("good-slug", "slug") is None
    assert braincheck.check_field_type("Bad Slug", "slug") is not None


def test_enum_check():
    assert braincheck.check_field_type("a", "enum:a|b") is None
    assert braincheck.check_field_type("z", "enum:a|b") is not None


# --- end-to-end on fixtures ---------------------------------------------

def test_valid_file_clean():
    assert run(["valid-concept.md"]) == []


def test_missing_frontmatter_errors():
    assert "E001" in codes(run(["no-frontmatter.md"]))


def test_bad_fields():
    c = codes(run(["bad-fields.md"]))
    assert "E101" in c   # missing tags/aliases
    assert "E103" in c   # bad slug / bad date


def test_unknown_type_warns():
    # An unknown `type` warns (W201). Unknown *fields* are suppressed for
    # unknown types, since the schema can't say what fields that type allows.
    c = codes(run(["unknown-type.md"]))
    assert "W201" in c
    assert "W202" not in c


def test_unknown_field_on_known_type_warns():
    c = codes(run(["known-type-extra-field.md"]))
    assert "W202" in c
    assert "W201" not in c


def test_fix_scaffolds_frontmatter(tmp_path=None):
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        target = os.path.join(tmp, "orphan.md")
        with open(target, "w") as fh:
            fh.write("# no frontmatter here\n")
        schema = braincheck.load_schema(SCHEMA)
        findings, fields = braincheck.check_file(target, "orphan.md", schema)
        braincheck.fix_file(target, "orphan.md", schema, findings, fields)
        after = open(target).read()
        assert after.startswith("---")
        assert "type: note" in after
        # re-check: scaffolded file should now be clean
        f2, _ = braincheck.check_file(target, "orphan.md", schema)
        assert [x for x in f2 if x.level == "error"] == []
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:
                failed += 1
                print(f"ERROR {name}: {e}")
    print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
    sys.exit(1 if failed else 0)
