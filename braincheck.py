#!/usr/bin/env python3
"""braincheck - a typechecker for the Second Brain knowledge layer.

Every Markdown file in the vault is a typed document: its YAML frontmatter
must conform to schema.yaml (base fields + per-`type` fields), the same way
code must conform to its type declarations.

Usage:
  python braincheck.py check [paths...] [--fix] [--strict] [--json] [--quiet]
  python braincheck.py stats
  python braincheck.py types

Zero dependencies. Works with any Python 3.9+.
Exit codes: 0 = clean, 1 = errors found, 2 = usage/schema problem.
"""

import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA = os.path.join(SCRIPT_DIR, "schema.yaml")

# ---------------------------------------------------------------------------
# Minimal YAML-subset parser (zero-dependency).
# Supports: nested maps, block lists, inline lists, quoted scalars,
# comments, multiline literals (| and >). Enough for frontmatter + schema.
# ---------------------------------------------------------------------------

class YamlError(Exception):
    def __init__(self, msg, lineno):
        super().__init__(msg)
        self.lineno = lineno


def _split_comment(s):
    """Strip a trailing comment that is not inside quotes."""
    in_s = in_d = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            if i == 0 or s[i - 1] in " \t":
                return s[:i].rstrip()
    return s.rstrip()


def _scalar(raw):
    raw = raw.strip()
    if raw == "" or raw in ("~", "null", "Null", "NULL"):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw in ("true", "True", "TRUE"):
        return True
    if raw in ("false", "False", "FALSE"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _inline_list(raw, lineno):
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items, buf, depth, in_s, in_d = [], "", 0, False, False
    for ch in inner:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch in "[{" and not in_s and not in_d:
            depth += 1
        elif ch in "]}" and not in_s and not in_d:
            depth -= 1
        if ch == "," and depth == 0 and not in_s and not in_d:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    return [_scalar(x) for x in items]


KEY_RE = re.compile(r"^([^:\s][^:]*?):(\s+(.*)|\s*)$")


def parse_yaml(text):
    """Parse a YAML-subset document into Python dict/list/scalars."""
    raw_lines = text.split("\n")
    lines = []  # (lineno, indent, content)
    for n, raw in enumerate(raw_lines, 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError("tab character used for indentation", n)
        stripped = _split_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((n, indent, stripped.strip()))

    pos = [0]

    def parse_block(min_indent):
        if pos[0] >= len(lines):
            return None
        lineno, indent, content = lines[pos[0]]
        if indent < min_indent:
            return None
        block_indent = indent
        if content.startswith("- ") or content == "-":
            return parse_list(block_indent)
        return parse_map(block_indent)

    def parse_multiline(parent_indent):
        chunks = []
        while pos[0] < len(lines):
            n, ind, c = lines[pos[0]]
            if ind <= parent_indent:
                break
            chunks.append(c)
            pos[0] += 1
        return "\n".join(chunks)

    def parse_value(rest, parent_indent, lineno):
        rest = rest.strip()
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            return parse_multiline(parent_indent)
        if rest.startswith("[") and rest.endswith("]"):
            return _inline_list(rest, lineno)
        if rest == "{}":
            return {}
        if rest == "":
            child = parse_block(parent_indent + 1)
            return child  # may be None (null value)
        return _scalar(rest)

    def parse_map(block_indent):
        result = {}
        order = []
        while pos[0] < len(lines):
            lineno, indent, content = lines[pos[0]]
            if indent < block_indent:
                break
            if indent > block_indent:
                raise YamlError("unexpected indentation", lineno)
            if content.startswith("- "):
                raise YamlError("list item inside mapping", lineno)
            m = KEY_RE.match(content)
            if not m:
                raise YamlError(f"cannot parse line: {content!r}", lineno)
            key = m.group(1).strip().strip("\"'")
            rest = m.group(3) or ""
            if key in result:
                raise YamlError(f"duplicate key {key!r}", lineno)
            pos[0] += 1
            result[key] = parse_value(rest, block_indent, lineno)
            order.append((key, lineno))
        result["__lines__"] = dict(order) if order else {}
        return result

    def parse_list(block_indent):
        result = []
        while pos[0] < len(lines):
            lineno, indent, content = lines[pos[0]]
            if indent != block_indent or not (content.startswith("- ") or content == "-"):
                break
            item = content[1:].strip()
            pos[0] += 1
            if item == "":
                result.append(None)
            elif KEY_RE.match(item) and not item.startswith(("http:", "https:")):
                raise YamlError("mapping inside list is not supported in frontmatter", lineno)
            elif item.startswith("[") and item.endswith("]"):
                result.append(_inline_list(item, lineno))
            else:
                result.append(_scalar(item))
        return result

    doc = parse_block(0)
    if pos[0] < len(lines):
        raise YamlError("could not parse document past this line", lines[pos[0]][0])
    return doc if doc is not None else {}


def strip_meta(obj):
    """Remove internal __lines__ bookkeeping keys recursively."""
    if isinstance(obj, dict):
        return {k: strip_meta(v) for k, v in obj.items() if k != "__lines__"}
    if isinstance(obj, list):
        return [strip_meta(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Frontmatter extraction
# ---------------------------------------------------------------------------

FM_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)", re.S)


def extract_frontmatter(text):
    """Return (yaml_text, body_start_offset, fm_line_offset) or None."""
    if not text.startswith("---"):
        return None
    m = FM_RE.match(text)
    if not m:
        return None
    return m.group(1), m.end(), 1


# ---------------------------------------------------------------------------
# Field type checks
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
URL_RE = re.compile(r"^https?://\S+$")


def check_field_type(value, spec):
    """Return error string or None. spec is e.g. 'string', 'date', 'enum:a|b'."""
    if value is None:
        return None  # emptiness handled separately
    if spec == "any":
        return None
    if spec.startswith("enum:"):
        allowed = spec[5:].split("|")
        if str(value) not in allowed:
            return f"expected one of [{', '.join(allowed)}], got {value!r}"
        return None
    if spec == "string":
        if isinstance(value, (list, dict)):
            return f"expected a string, got a {type(value).__name__}"
        return None
    if spec == "date":
        if isinstance(value, (list, dict)):
            return "expected a date (YYYY-MM-DD), got a " + type(value).__name__
        if not DATE_RE.match(str(value)):
            return f"expected date format YYYY-MM-DD, got {value!r}"
        return None
    if spec == "slug":
        if not isinstance(value, str) or not SLUG_RE.match(value):
            return f"expected a slug (lowercase, a-z 0-9 . _ -), got {value!r}"
        return None
    if spec == "url":
        if not isinstance(value, str) or not URL_RE.match(value):
            return f"expected a URL (http/https), got {value!r}"
        return None
    if spec in ("list", "string-list"):
        if not isinstance(value, list):
            return f"expected a list, got {type(value).__name__} {value!r}"
        if spec == "string-list":
            for item in value:
                if isinstance(item, (list, dict)):
                    return "expected a flat list of strings"
        return None
    if spec == "bool":
        if not isinstance(value, bool):
            return f"expected true/false, got {value!r}"
        return None
    if spec == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"expected a number, got {value!r}"
        return None
    return f"unknown field spec {spec!r} in schema"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class Schema:
    def __init__(self, raw, path):
        raw = strip_meta(raw)
        self.path = path
        self.exclude = raw.get("exclude") or []
        self.default_type = raw.get("default_type", "note")
        base = raw.get("base") or {}
        self.base_required = base.get("required") or {}
        self.base_optional = base.get("optional") or {}
        self.types = raw.get("types") or {}
        for name, t in self.types.items():
            if not isinstance(t, dict):
                self.types[name] = {"required": {}, "optional": {}}

    def fields_for(self, doc_type):
        """Return (required: dict, known: dict) field->spec maps."""
        required = dict(self.base_required)
        known = dict(self.base_required)
        known.update(self.base_optional)
        t = self.types.get(doc_type)
        if t:
            required.update(t.get("required") or {})
            known.update(t.get("required") or {})
            known.update(t.get("optional") or {})
        return required, known


def load_schema(path):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        sys.exit(f"braincheck: cannot read schema {path}: {e}")
    try:
        return Schema(parse_yaml(text), path)
    except YamlError as e:
        sys.exit(f"braincheck: schema parse error {path}:{e.lineno}: {e}")


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

class Finding:
    __slots__ = ("path", "line", "level", "code", "message")

    def __init__(self, path, line, level, code, message):
        self.path, self.line, self.level = path, line, level
        self.code, self.message = code, message

    def render(self):
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}: {self.level} {self.code}: {self.message}"

    def as_dict(self):
        return {"path": self.path, "line": self.line, "level": self.level,
                "code": self.code, "message": self.message}


def is_excluded(relpath, patterns):
    parts = relpath.replace("\\", "/").split("/")
    rel = relpath.replace("\\", "/")
    for pat in patterns:
        pat = pat.replace("\\", "/").rstrip("/")
        if pat.endswith("/**"):
            pat = pat[:-3]
        if rel == pat or rel.startswith(pat + "/"):
            return True
        if "/" not in pat and pat in parts:
            return True
    return False


def iter_markdown(root, paths, exclude):
    targets = paths or [root]
    seen = set()
    for target in targets:
        target = os.path.abspath(target)
        if os.path.isfile(target):
            rel = os.path.relpath(target, root)
            if target.endswith(".md") and not is_excluded(rel, exclude):
                if target not in seen:
                    seen.add(target)
                    yield target, rel
            continue
        for dirpath, dirnames, filenames in os.walk(target):
            reldir = os.path.relpath(dirpath, root)
            if reldir == ".":
                reldir = ""
            dirnames[:] = sorted(
                d for d in dirnames
                if not is_excluded(os.path.join(reldir, d), exclude)
            )
            for f in sorted(filenames):
                if not f.endswith(".md"):
                    continue
                rel = os.path.join(reldir, f) if reldir else f
                if is_excluded(rel, exclude):
                    continue
                full = os.path.join(dirpath, f)
                if full not in seen:
                    seen.add(full)
                    yield full, rel


def check_file(full, rel, schema):
    findings = []
    try:
        text = open(full, encoding="utf-8", errors="replace").read()
    except OSError as e:
        findings.append(Finding(rel, 0, "error", "E000", f"cannot read file: {e}"))
        return findings, None

    fm = extract_frontmatter(text)
    if fm is None:
        findings.append(Finding(rel, 1, "error", "E001",
                                "missing YAML frontmatter (--- block at top of file)"))
        return findings, None

    yaml_text, _, line_offset = fm
    try:
        data = parse_yaml(yaml_text)
    except YamlError as e:
        findings.append(Finding(rel, e.lineno + line_offset, "error", "E002",
                                f"frontmatter is not valid YAML: {e}"))
        return findings, None

    if not isinstance(data, dict):
        findings.append(Finding(rel, 1 + line_offset, "error", "E002",
                                "frontmatter must be a mapping of key: value"))
        return findings, None

    key_lines = data.get("__lines__", {})
    fields = strip_meta(data)

    def line_of(key):
        return key_lines.get(key, 1) + line_offset

    doc_type = fields.get("type")
    if isinstance(doc_type, str):
        doc_type = doc_type.strip()

    type_known = isinstance(doc_type, str) and doc_type in schema.types
    if doc_type and isinstance(doc_type, str) and not type_known:
        findings.append(Finding(rel, line_of("type"), "warning", "W201",
                                f"unknown type {doc_type!r} (not declared in schema.yaml)"))

    required, known = schema.fields_for(doc_type if type_known else None)

    for key, spec in required.items():
        if key not in fields:
            findings.append(Finding(rel, 1 + line_offset, "error", "E101",
                                    f"missing required field {key!r}"
                                    + (f" (required for type {doc_type!r})"
                                       if key not in schema.base_required else "")))
        elif fields[key] is None or fields[key] == "":
            findings.append(Finding(rel, line_of(key), "error", "E106",
                                    f"required field {key!r} is empty"))

    for key, value in fields.items():
        spec = known.get(key)
        if spec is None:
            if type_known or not doc_type:
                findings.append(Finding(rel, line_of(key), "warning", "W202",
                                        f"unknown field {key!r} for type {doc_type!r}"
                                        if doc_type else f"unknown field {key!r}"))
            continue
        err = check_field_type(value, spec)
        if err:
            findings.append(Finding(rel, line_of(key), "error", "E103",
                                    f"field {key!r}: {err}"))

    return findings, fields


# ---------------------------------------------------------------------------
# Fixing
# ---------------------------------------------------------------------------

def slugify(name):
    s = re.sub(r"\.md$", "", os.path.basename(name)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "untitled"


def yaml_dump_value(v):
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def fix_file(full, rel, schema, findings, fields):
    """Apply safe automatic fixes. Returns list of fix descriptions."""
    fixes = []
    stat = os.stat(full)
    created_d = datetime.date.fromtimestamp(min(stat.st_mtime, stat.st_ctime)).isoformat()
    updated_d = datetime.date.fromtimestamp(stat.st_mtime).isoformat()
    text = open(full, encoding="utf-8", errors="replace").read()

    has_e001 = any(f.code == "E001" for f in findings)
    if has_e001:
        title = re.sub(r"\.md$", "", os.path.basename(full))
        fm_lines = [
            "---",
            f"type: {schema.default_type}",
            f"slug: {slugify(full)}",
            "tags: []",
            f"created: {created_d}",
            f"updated: {updated_d}",
            "---",
            "",
        ]
        new = "\n".join(fm_lines) + text
        open(full, "w", encoding="utf-8", newline="\n").write(new)
        fixes.append(f"{rel}: scaffolded frontmatter (type: {schema.default_type})")
        return fixes

    if fields is None:
        return fixes  # unparseable YAML: never auto-edit

    missing = [f for f in findings if f.code == "E101"]
    additions = []
    for f in missing:
        m = re.search(r"missing required field '([^']+)'", f.message)
        if not m:
            continue
        key = m.group(1)
        if key == "created":
            additions.append(f"created: {created_d}")
        elif key == "updated":
            additions.append(f"updated: {updated_d}")
        elif key == "slug":
            additions.append(f"slug: {slugify(full)}")
        elif key == "tags":
            additions.append("tags: []")
        elif key == "aliases":
            additions.append("aliases: []")
        # anything else (type-specific content fields) needs a human

    if additions:
        m = FM_RE.match(text)
        head = text[: m.end(1)]
        tail = text[m.end(1):]
        new = head + "\n" + "\n".join(additions) + tail
        open(full, "w", encoding="utf-8", newline="\n").write(new)
        for a in additions:
            fixes.append(f"{rel}: added {a}")
    return fixes


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_check(args, schema, root):
    all_findings = []
    fixed = []
    checked = 0
    for full, rel in iter_markdown(root, args.paths, schema.exclude):
        checked += 1
        findings, fields = check_file(full, rel, schema)
        if args.fix and findings:
            fixes = fix_file(full, rel, schema, findings, fields)
            if fixes:
                fixed.extend(fixes)
                findings, fields = check_file(full, rel, schema)  # re-check
        all_findings.extend(findings)

    if args.strict:
        for f in all_findings:
            if f.level == "warning":
                f.level = "error"

    errors = [f for f in all_findings if f.level == "error"]
    warnings = [f for f in all_findings if f.level == "warning"]

    if args.json:
        print(json.dumps({
            "checked": checked,
            "errors": len(errors),
            "warnings": len(warnings),
            "fixed": fixed,
            "findings": [f.as_dict() for f in all_findings],
        }, indent=2))
    else:
        for f in all_findings:
            if args.quiet and f.level == "warning":
                continue
            print(f.render())
        if fixed:
            print(f"\n--fix applied {len(fixed)} change(s):")
            for fx in fixed:
                print("  " + fx)
        print(f"\nbraincheck: {checked} file(s) checked, "
              f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def cmd_stats(args, schema, root):
    by_type = Counter()
    error_files = 0
    clean = 0
    for full, rel in iter_markdown(root, args.paths, schema.exclude):
        findings, fields = check_file(full, rel, schema)
        t = (fields or {}).get("type") or "(none)"
        by_type[str(t)] += 1
        if any(f.level == "error" for f in findings):
            error_files += 1
        elif not findings:
            clean += 1
    total = sum(by_type.values())
    print(f"{total} documents in the knowledge layer\n")
    width = max((len(t) for t in by_type), default=4)
    for t, c in by_type.most_common():
        mark = "" if t in schema.types else "   (not in schema)"
        print(f"  {t:<{width}}  {c}{mark}")
    print(f"\n  clean: {clean}   with errors: {error_files}   "
          f"with warnings only: {total - clean - error_files}")
    return 0


def cmd_types(args, schema, root):
    print(f"Schema: {schema.path}\n")
    print("Base required: " + ", ".join(f"{k} ({v})" for k, v in schema.base_required.items()))
    print("Base optional: " + ", ".join(schema.base_optional))
    print()
    for name in sorted(schema.types):
        t = schema.types[name]
        req = ", ".join((t.get("required") or {})) or "-"
        opt = ", ".join((t.get("optional") or {})) or "-"
        print(f"  {name}\n    required: {req}\n    optional: {opt}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="braincheck",
                                description="Typechecker for Second Brain YAML frontmatter")
    p.add_argument("command", nargs="?", default="check",
                   choices=["check", "stats", "types"])
    p.add_argument("paths", nargs="*", help="files or folders (default: whole vault)")
    p.add_argument("--fix", action="store_true",
                   help="scaffold missing frontmatter and fill derivable fields")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--quiet", action="store_true", help="hide warnings in output")
    p.add_argument("--schema", default=DEFAULT_SCHEMA, help="path to schema.yaml")
    p.add_argument("--root", default=None, help="vault root (default: cwd)")
    args = p.parse_args(argv)

    root = os.path.abspath(args.root or os.getcwd())
    schema = load_schema(args.schema)

    if args.command == "check":
        return cmd_check(args, schema, root)
    if args.command == "stats":
        return cmd_stats(args, schema, root)
    if args.command == "types":
        return cmd_types(args, schema, root)
    return 2


if __name__ == "__main__":  # entry point
    sys.exit(main())
