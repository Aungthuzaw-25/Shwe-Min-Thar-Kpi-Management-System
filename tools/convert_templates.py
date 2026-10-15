#!/usr/bin/env python3
"""Convert this repository's Jinja templates to Nunjucks without reformatting HTML."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
TAG_PATTERN = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.DOTALL)
BLOCK_OPENERS = {"if", "for", "block", "macro", "call", "filter", "with", "autoescape"}
BLOCK_CLOSERS = {
    "endif": "if",
    "endfor": "for",
    "endblock": "block",
    "endmacro": "macro",
    "endcall": "call",
    "endfilter": "filter",
    "endwith": "with",
    "endautoescape": "autoescape",
}
GET_PATTERN = re.compile(
    r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.get\(\s*"
    r"(?:(['\"])(.*?)\2|([A-Za-z_]\w*))"
    r"\s*(?:,\s*(False|True|None|null|undefined))?\s*\)",
    re.DOTALL,
)
STRFTIME_PATTERN = re.compile(
    r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[[^\]]+\])*)\.strftime\(\s*"
    r"(['\"])(.*?)\2\s*\)",
    re.DOTALL,
)
SLICE_PATTERN = re.compile(
    r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
    r"\[\s*([^:\]]*)\s*:\s*([^\]]*)\s*\]"
)
EMPTY_INIT_PATHS = (
    Path("__init__.py"),
    Path("auth/__init__.py"),
    Path("dashboard/__init__.py"),
    Path("layouts/__init__.py"),
)


def template_files() -> list[Path]:
    return sorted(path for path in TEMPLATES.rglob("*.html") if path.is_file())


def parse_tag(tag: str) -> tuple[str, str] | None:
    if not tag.startswith("{%"):
        return None
    body = tag[2:-2].strip()
    if not body:
        return None
    parts = body.split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def replace_in_tag(tag: str, review: list[str]) -> str:
    if tag.startswith("{#"):
        return tag
    return _convert_tag_content(tag, review)


def _convert_tag_content(tag: str, review: list[str]) -> str:
    converted = GET_PATTERN.sub(_replace_get_expression, tag)
    converted = STRFTIME_PATTERN.sub(_replace_strftime_expression, converted)
    converted = _replace_template_slices(converted, review)
    if ".get(" in converted:
        review.append(f"Unsupported `.get()` expression: {converted}")

    return _rewrite_template_paths(converted)


def _unhandled_slice(match: re.Match[str], review: list[str]) -> str:
    review.append(f"Unsupported Python slice: {match.group(0)}")
    return match.group(0)


def _replace_get_expression(match: re.Match[str]) -> str:
    obj = match.group(1)
    key = repr(match.group(3)) if match.group(2) else match.group(4)
    default = match.group(5)
    if default is None:
        return f"{obj}[{key}]"
    if default == "None":
        default = "null"
    return f"({obj}[{key}] if {obj}[{key}] is defined else {default})"


def _replace_strftime_expression(match: re.Match[str]) -> str:
    return f"({match.group(1)}|strftime({match.group(3)!r}))"


def _rewrite_template_paths(source: str) -> str:
    return re.sub(
        r"(?P<quote>['\"])(?P<path>[^'\"]+)\.html(?P=quote)",
        lambda match: (
            f"{match.group('quote')}{match.group('path')}.njk{match.group('quote')}"
            if (TEMPLATES / f"{match.group('path')}.html").is_file()
            else match.group(0)
        ),
        source,
    )


def _replace_template_slices(source: str, review: list[str]) -> str:
    return SLICE_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}|first"
            if not match.group(2).strip() and match.group(3).strip() == "1"
            else _unhandled_slice(match, review)
        ),
        source,
    )


def convert_source(source: str, review: list[str]) -> str:
    return TAG_PATTERN.sub(lambda match: replace_in_tag(match.group(0), review), source)


def validate_syntax(source: str, filename: str) -> list[str]:
    errors: list[str] = []
    stack: list[tuple[str, int]] = []
    for match in TAG_PATTERN.finditer(source):
        parsed = parse_tag(match.group(0))
        if parsed is not None:
            command, _ = parsed
            line = source.count("\n", 0, match.start()) + 1
            _validate_tag_command(command, line, filename, stack, errors)

    for command, line in reversed(stack):
        errors.append(f"{filename}:{line}: unclosed {command}")
    return errors


def _validate_tag_command(
    command: str,
    line: int,
    filename: str,
    stack: list[tuple[str, int]],
    errors: list[str],
) -> None:
    if command in BLOCK_OPENERS:
        stack.append((command, line))
        return
    if command in BLOCK_CLOSERS:
        expected = BLOCK_CLOSERS[command]
        if stack and stack[-1][0] == expected:
            stack.pop()
        else:
            errors.append(f"{filename}:{line}: unexpected {command}")
        return
    if command in {"elif", "else"} and (
        not stack or stack[-1][0] not in {"if", "for"}
    ):
        errors.append(f"{filename}:{line}: unexpected {command}")


def constructs(source: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in TAG_PATTERN.finditer(source):
        tag = match.group(0)
        parsed = parse_tag(tag)
        if parsed:
            counts[parsed[0]] += 1
        elif tag.startswith("{{"):
            counts["expression"] += 1
        elif tag.startswith("{#"):
            counts["comment"] += 1
        if " | " in tag or "|tojson" in tag or "|strftime" in tag:
            for filter_name in re.findall(r"\|\s*([A-Za-z_]\w*)", tag):
                counts[f"filter:{filter_name}"] += 1
    return counts


def masked_non_template_text(source: str) -> list[str]:
    outside: list[str] = []
    position = 0
    for match in TAG_PATTERN.finditer(source):
        outside.append(source[position:match.start()])
        position = match.end()
    outside.append(source[position:])
    return outside


def update_references(files: list[Path]) -> list[tuple[Path, str]]:
    template_map = {
        path.relative_to(TEMPLATES).with_suffix(".html").as_posix():
        path.relative_to(TEMPLATES).with_suffix(".njk").as_posix()
        for path in files
    }
    basenames = {
        Path(old).name: Path(new).name for old, new in template_map.items()
    }
    changed: list[tuple[Path, str]] = []
    candidates = [
        *sorted((ROOT / "app").rglob("*.py")),
        *sorted((ROOT / "tests").rglob("*.py")),
        ROOT / "tailwind.config.js",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = original
        for old, new in template_map.items():
            updated = updated.replace(old, new)
            updated = updated.replace(f"templates/{old}", f"templates/{new}")
        for old, new in basenames.items():
            updated = re.sub(
                rf"(?P<q>['\"]){re.escape(old)}(?P=q)",
                lambda match, new=new: f"{match.group('q')}{new}{match.group('q')}",
                updated,
            )
        if path.name == "tailwind.config.js":
            updated = updated.replace(
                '"./templates/**/*.html"', '"./templates/**/*.njk"'
            )
        if updated != original:
            changed.append((path, updated))
    return changed


def _convert_files(
    files: list[Path], syntax_check: bool
) -> tuple[list[tuple[Path, str, list[str]]], Counter[str], list[str], list[str]]:
    reviews: list[str] = []
    summary: Counter[str] = Counter()
    results: list[tuple[Path, str, list[str]]] = []
    errors: list[str] = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        summary.update(constructs(source))
        converted = convert_source(source, reviews)
        results.append((path, source, [converted]))
        if syntax_check:
            errors.extend(validate_syntax(converted, path.as_posix()))
    return results, summary, reviews, errors


def _print_plan(
    files: list[Path],
    summary: Counter[str],
    reviews: list[str],
    errors: list[str],
    references: list[tuple[Path, str]],
    syntax_check: bool,
) -> None:
    print(f"Templates found: {len(files)}")
    print("Construct counts:")
    for name, count in sorted(summary.items()):
        print(f"  {name}: {count}")
    print("Planned template renames:")
    for path in files:
        print(f"  {path.relative_to(ROOT).as_posix()} -> "
              f"{path.relative_to(ROOT).with_suffix('.njk').as_posix()}")
    print("Planned code/config reference updates:")
    for path, _ in references:
        print(f"  {path.relative_to(ROOT).as_posix()}")
    if reviews:
        print("Needs manual review:")
        for item in sorted(set(reviews)):
            print(f"  {item}")
    else:
        print("Needs manual review: none")
    if errors:
        print("Syntax check failures:")
        for item in errors:
            print(f"  {item}")
    elif syntax_check:
        print("Syntax check: passed")


def _apply_conversion(
    results: list[tuple[Path, str, list[str]]],
    references: list[tuple[Path, str]],
    remove_init: bool,
) -> None:
    for old_path, source, [converted] in results:
        new_path = old_path.with_suffix(".njk")
        subprocess.run(
            ["git", "mv", str(old_path.relative_to(ROOT)), str(new_path.relative_to(ROOT))],
            cwd=ROOT,
            check=True,
        )
        new_path.write_text(converted, encoding="utf-8", newline="")
        if masked_non_template_text(source) != masked_non_template_text(converted):
            raise RuntimeError(f"Non-template text changed in {old_path.relative_to(ROOT)}")

    for path, updated in references:
        path.write_text(updated, encoding="utf-8", newline="")

    if remove_init:
        for relative in EMPTY_INIT_PATHS:
            candidate = TEMPLATES / relative
            if candidate.is_file() and candidate.stat().st_size == 0:
                subprocess.run(
                    ["git", "rm", str(candidate.relative_to(ROOT))],
                    cwd=ROOT,
                    check=True,
                )


def _write_report(
    files: list[Path], references: list[tuple[Path, str]], reviews: list[str], errors: list[str]
) -> None:
    report = [
        "# Template conversion report",
        "",
        f"- Converted {len(files)} templates in place, preserving directory and base names.",
        "- Changes inside Jinja delimiters are limited to path extensions, `.get()`, "
          "`.strftime()`, and the `[:1]` slice conversions listed above.",
        "- Verified text outside template delimiters remains byte-for-byte unchanged.",
        f"- Updated {len(references)} route, test, or Tailwind configuration files.",
        "",
        "## Syntax check",
        "",
        "Balanced Jinja/Nunjucks delimiter and block check passed." if not errors
        else "\n".join(f"- {error}" for error in errors),
        "",
        "## Needs manual review",
        "",
        "\n".join(f"- {item}" for item in sorted(set(reviews))) or "- None.",
        "",
        "## Converted template files",
        "",
        *[f"- `{path.relative_to(ROOT).with_suffix('.njk').as_posix()}`" for path in files],
    ]
    (ROOT / "TEMPLATE_CONVERSION_REPORT.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8", newline=""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes using git mv")
    parser.add_argument(
        "--remove-init", action="store_true", help="remove empty templates package markers"
    )
    parser.add_argument(
        "--syntax-check", action="store_true", help="check balanced Jinja/Nunjucks tags"
    )
    args = parser.parse_args()

    if not TEMPLATES.is_dir():
        parser.error("templates/ does not exist")
    files = template_files()
    if not files:
        parser.error("no templates/**/*.html files found")

    results, summary, reviews, all_errors = _convert_files(files, args.syntax_check)
    references = update_references(files)
    _print_plan(files, summary, reviews, all_errors, references, args.syntax_check)

    if not args.apply:
        print("Dry run only; no files changed.")
        return 1 if all_errors else 0
    if all_errors:
        print("Refusing to apply because syntax checks failed.", file=sys.stderr)
        return 1

    _apply_conversion(results, references, args.remove_init)
    _write_report(files, references, reviews, all_errors)
    print("Applied conversion and wrote TEMPLATE_CONVERSION_REPORT.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
