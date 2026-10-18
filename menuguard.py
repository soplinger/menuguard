#!/usr/bin/env python3
"""Menuguard: offline restaurant allergen matrix checker.

Reads one plain-text menu file. Ingredients are mapped to the 14 major
allergens, and each dish lists its ingredients, shared equipment and
precautions. From that Menuguard builds a dish-by-allergen matrix, screens
dishes for a guest's restrictions, verifies free-from claims and gates CI.

Standard library only. Nothing is read from or sent to the network.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import html
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

ALLERGENS = (
    "celery",
    "crustacean",
    "egg",
    "fish",
    "gluten",
    "lupin",
    "milk",
    "mollusc",
    "mustard",
    "peanut",
    "sesame",
    "soy",
    "sulphites",
    "tree-nut",
)
ALLERGEN_SET = frozenset(ALLERGENS)
DISH_FIELDS = ("shares", "free-from", "may-contain")
LEGEND = (
    "C = contains  M = may contain (declared precaution)  "
    "X = cross-contact via shared equipment  - = none"
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class CliError(Exception):
    """A problem with the command line or with an input file."""


@dataclass(frozen=True)
class Issue:
    line: int
    severity: str  # "error" or "warning"
    message: str


@dataclass
class Ingredient:
    name: str
    allergens: FrozenSet[str]
    line: int


@dataclass
class Dish:
    name: str
    line: int
    ingredients: List[str]
    shares: List[str]
    free_from: List[str]
    may_contain: List[str]


@dataclass
class Menu:
    ingredients: Dict[str, Ingredient]
    dishes: List[Dish]
    issues: List[Issue]


@dataclass(frozen=True)
class Row:
    """One dish with its allergen exposure worked out."""

    dish: Dish
    contains: FrozenSet[str]
    may_contain: FrozenSet[str]
    cross: FrozenSet[str]
    shared_with: Tuple[str, ...]

    def cell(self, allergen: str) -> str:
        if allergen in self.contains:
            return "C"
        if allergen in self.may_contain:
            return "M"
        if allergen in self.cross:
            return "X"
        return ""


def _names(text: str) -> List[str]:
    return [part.strip().lower() for part in text.split(",") if part.strip()]


def _suggest(word: str, options) -> str:
    close = difflib.get_close_matches(word, list(options), n=1)
    return f" (did you mean '{close[0]}'?)" if close else ""


def _list(values) -> str:
    return ", ".join(sorted(values)) or "none"


def parse_menu(text: str) -> Menu:
    """Parse menu text. Syntax problems are recorded as issues, not raised."""
    menu = Menu(ingredients={}, dishes=[], issues=[])
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, rest = line.partition(" ")
        if keyword == "ingredient":
            _parse_ingredient(menu, lineno, rest)
        elif keyword == "dish":
            _parse_dish(menu, lineno, rest)
        else:
            menu.issues.append(
                Issue(lineno, "error", f"unknown directive '{keyword}' (use 'ingredient' or 'dish')")
            )
    return menu


def _parse_ingredient(menu: Menu, lineno: int, rest: str) -> None:
    name, sep, allergen_text = rest.partition(":")
    name = name.strip().lower()
    if not sep or not name:
        menu.issues.append(Issue(lineno, "error", "expected 'ingredient NAME: allergen, ...' (or 'none')"))
        return
    if name in menu.ingredients:
        first = menu.ingredients[name].line
        menu.issues.append(Issue(lineno, "error", f"ingredient '{name}' is already defined on line {first}"))
        return
    allergens = set()
    for value in _names(allergen_text):
        if value == "none":
            continue
        if value in ALLERGEN_SET:
            allergens.add(value)
        else:
            menu.issues.append(
                Issue(lineno, "error", f"unknown allergen '{value}'{_suggest(value, ALLERGENS)}")
            )
    menu.ingredients[name] = Ingredient(name, frozenset(allergens), lineno)


def _parse_dish(menu: Menu, lineno: int, rest: str) -> None:
    head, *fields = rest.split("|")
    name, sep, ingredient_text = head.partition(":")
    name = name.strip()
    if not sep or not name:
        menu.issues.append(Issue(lineno, "error", "expected 'dish NAME: ingredient, ... [| field: value]'"))
        return
    dish = Dish(name, lineno, _names(ingredient_text), [], [], [])
    targets = {"shares": dish.shares, "free-from": dish.free_from, "may-contain": dish.may_contain}
    seen = set()
    for field_text in fields:
        key, sep, value = field_text.partition(":")
        key = key.strip().lower()
        if not sep or key not in targets:
            menu.issues.append(
                Issue(lineno, "error", f"unknown field '{key}' (use shares, free-from or may-contain)")
            )
            continue
        if key in seen:
            menu.issues.append(Issue(lineno, "error", f"field '{key}' is given twice"))
            continue
        seen.add(key)
        targets[key].extend(_names(value))
    menu.dishes.append(dish)


def _ingredient_allergens(menu: Menu, dish: Dish) -> FrozenSet[str]:
    found = set()
    for name in dish.ingredients:
        if name in menu.ingredients:
            found |= menu.ingredients[name].allergens
    return frozenset(found)


def _precautions(dish: Dish) -> FrozenSet[str]:
    return frozenset(dish.may_contain) & ALLERGEN_SET


def _sources(menu: Menu, dish: Dish, allergen: str) -> List[str]:
    return [
        name
        for name in dish.ingredients
        if name in menu.ingredients and allergen in menu.ingredients[name].allergens
    ]


def analyse(menu: Menu) -> List[Row]:
    """Work out every dish's contains, precautionary and cross-contact sets."""
    rows = []
    for dish in menu.dishes:
        contains = _ingredient_allergens(menu, dish)
        may = _precautions(dish) - contains
        shared = set(dish.shares)
        others = [other for other in menu.dishes if other is not dish and shared & set(other.shares)]
        exposure = set()
        for other in others:
            exposure |= _ingredient_allergens(menu, other) | _precautions(other)
        cross = frozenset(exposure) - contains - may
        rows.append(Row(dish, contains, may, cross, tuple(other.name for other in others)))
    return rows


def lint(menu: Menu) -> List[Issue]:
    """Return every parse, consistency and claim issue, sorted by line."""
    issues = list(menu.issues)
    first_seen: Dict[str, int] = {}
    used = set()
    for dish in menu.dishes:
        key = dish.name.lower()
        if key in first_seen:
            issues.append(Issue(dish.line, "error", f"dish '{dish.name}' is already defined on line {first_seen[key]}"))
        else:
            first_seen[key] = dish.line
        if not dish.ingredients:
            issues.append(Issue(dish.line, "error", f"dish '{dish.name}' lists no ingredients"))
        for name in dish.ingredients:
            if name in menu.ingredients:
                used.add(name)
            else:
                issues.append(
                    Issue(dish.line, "error", f"unknown ingredient '{name}'{_suggest(name, menu.ingredients)}")
                )
        for allergen in dish.free_from + dish.may_contain:
            if allergen not in ALLERGEN_SET:
                issues.append(
                    Issue(dish.line, "error", f"unknown allergen '{allergen}'{_suggest(allergen, ALLERGENS)}")
                )

    for row in analyse(menu):
        dish = row.dish
        for allergen in dish.free_from:
            if allergen not in ALLERGEN_SET:
                continue
            if allergen in row.contains:
                via = ", ".join(_sources(menu, dish, allergen))
                issues.append(
                    Issue(dish.line, "error", f"'{dish.name}' claims free from {allergen} but contains it via {via}")
                )
            elif allergen in dish.may_contain:
                issues.append(
                    Issue(dish.line, "error", f"'{dish.name}' lists {allergen} as both free-from and may-contain")
                )
            elif allergen in row.cross:
                equipment = ", ".join(sorted(set(dish.shares)))
                others = ", ".join(row.shared_with)
                issues.append(
                    Issue(
                        dish.line,
                        "warning",
                        f"'{dish.name}' claims free from {allergen} but shares {equipment} with {others}",
                    )
                )

    for ingredient in menu.ingredients.values():
        if ingredient.name not in used:
            issues.append(Issue(ingredient.line, "warning", f"ingredient '{ingredient.name}' is not used by any dish"))
    return sorted(issues, key=lambda issue: issue.line)


def parse_allergens(text: str) -> List[str]:
    values = _names(text)
    if not values:
        raise CliError("no allergens given")
    for value in values:
        if value not in ALLERGEN_SET:
            raise CliError(f"unknown allergen '{value}'{_suggest(value, ALLERGENS)}")
    return list(dict.fromkeys(values))


def screen(rows: Sequence[Row], avoid: Sequence[str], strict: bool = False):
    """Split rows into (safe, [(blocked row, reasons)]) for a guest's avoid list.

    Default mode only blocks dishes that contain an avoided allergen. Strict
    mode also blocks declared precautions and cross-contact from shared kit.
    """
    safe: List[Row] = []
    blocked: List[Tuple[Row, List[str]]] = []
    for row in rows:
        reasons = []
        for allergen in avoid:
            if allergen in row.contains:
                reasons.append(f"contains {allergen}")
            elif strict and allergen in row.may_contain:
                reasons.append(f"may contain {allergen}")
            elif strict and allergen in row.cross:
                reasons.append(f"cross-contact {allergen}")
        if reasons:
            blocked.append((row, reasons))
        else:
            safe.append(row)
    return safe, blocked


def matrix_columns(rows: Sequence[Row], all_columns: bool = False) -> List[str]:
    if all_columns:
        return list(ALLERGENS)
    used = set()
    for row in rows:
        used |= row.contains | row.may_contain | row.cross
    return [allergen for allergen in ALLERGENS if allergen in used]


def render_matrix(rows: Sequence[Row], columns: Sequence[str], fmt: str = "text") -> str:
    header = ["Dish", *columns]
    body = [[row.dish.name, *[row.cell(allergen) for allergen in columns]] for row in rows]
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerows(body)
        return buf.getvalue()
    shown = [[cell or "-" for cell in line] for line in body]
    if fmt == "md":
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
        lines += ["| " + " | ".join(line) + " |" for line in shown]
        return "\n".join(lines) + "\n"
    grid = [header] + shown
    widths = [max(len(line[i]) for line in grid) for i in range(len(header))]
    out = ["  ".join(text.ljust(width) for text, width in zip(line, widths)).rstrip() for line in grid]
    out.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(out) + "\n\n" + LEGEND + "\n"


def render_card(rows: Sequence[Row], columns: Sequence[str], title: str) -> str:
    esc = html.escape
    head = "".join(f'<th scope="col">{esc(allergen)}</th>' for allergen in columns)
    body_rows = []
    for row in rows:
        cells = []
        for allergen in columns:
            mark = row.cell(allergen)
            cells.append(f'<td class="cell-{mark or "none"}">{esc(mark or "-")}</td>')
        body_rows.append(f'<tr><th scope="row">{esc(row.dish.name)}</th>{"".join(cells)}</tr>')
    return CARD_TEMPLATE.format(
        title=esc(title),
        head=head,
        body="\n".join(body_rows),
    )


CARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} allergen card</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid #bbb; padding: 0.35rem 0.6rem; text-align: center; }}
th[scope="row"] {{ text-align: left; }}
.cell-C {{ background: #f6c6c6; font-weight: bold; }}
.cell-M {{ background: #fbe7b5; }}
.cell-X {{ background: #d5e8f7; }}
</style>
</head>
<body>
<h1>{title}: allergen matrix</h1>
<table>
<thead><tr><th scope="col">Dish</th>{head}</tr></thead>
<tbody>
{body}
</tbody>
</table>
<p>C = contains. M = may contain (precaution). X = cross-contact via shared equipment.</p>
</body>
</html>
"""


def _find_row(rows: Sequence[Row], name: str) -> Row:
    key = name.strip().lower()
    for row in rows:
        if row.dish.name.lower() == key:
            return row
    raise CliError(f"no dish named '{name}'")


def describe(menu: Menu, name: str) -> List[str]:
    row = _find_row(analyse(menu), name)
    dish = row.dish
    lines = [f"{dish.name} (line {dish.line})", "ingredients:"]
    for ingredient in dish.ingredients:
        if ingredient in menu.ingredients:
            lines.append(f"  {ingredient}: {_list(menu.ingredients[ingredient].allergens)}")
        else:
            lines.append(f"  {ingredient}: UNKNOWN")
    lines.append(f"contains: {_list(row.contains)}")
    lines.append(f"may contain: {_list(row.may_contain)}")
    cross_line = f"cross-contact: {_list(row.cross)}"
    if row.shared_with:
        cross_line += f" (shares {', '.join(sorted(set(dish.shares)))} with {', '.join(row.shared_with)})"
    lines.append(cross_line)
    lines.append(f"free-from claims: {', '.join(dish.free_from) or 'none'}")
    return lines


def where(menu: Menu, ingredient: str) -> List[str]:
    key = ingredient.strip().lower()
    if key not in menu.ingredients:
        raise CliError(f"unknown ingredient '{ingredient}'")
    lines = [f"{key}: {_list(menu.ingredients[key].allergens)}"]
    users = [dish.name for dish in menu.dishes if key in dish.ingredients]
    lines += [f"  used in {name}" for name in users] or ["  not used in any dish"]
    return lines


def stats(menu: Menu, rows: Sequence[Row]) -> List[str]:
    lines = [f"dishes: {len(rows)}", f"ingredients: {len(menu.ingredients)}"]
    for allergen in ALLERGENS:
        contains = sum(allergen in row.contains for row in rows)
        may = sum(allergen in row.may_contain for row in rows)
        cross = sum(allergen in row.cross for row in rows)
        if contains or may or cross:
            lines.append(f"{allergen:<12} contains {contains:>3}  may-contain {may:>3}  cross-contact {cross:>3}")
    if len(lines) == 2:
        lines.append("no allergens found")
    return lines


def diff(old: Menu, new: Menu) -> List[str]:
    old_rows = {row.dish.name.lower(): row for row in analyse(old)}
    new_rows = {row.dish.name.lower(): row for row in analyse(new)}
    lines = []
    for key in sorted(new_rows.keys() - old_rows.keys()):
        lines.append(f"added: {new_rows[key].dish.name} (contains {_list(new_rows[key].contains)})")
    for key in sorted(old_rows.keys() - new_rows.keys()):
        lines.append(f"removed: {old_rows[key].dish.name}")
    for key in sorted(old_rows.keys() & new_rows.keys()):
        before, after = old_rows[key].contains, new_rows[key].contains
        parts = []
        if after - before:
            parts.append(f"now contains {_list(after - before)}")
        if before - after:
            parts.append(f"no longer contains {_list(before - after)}")
        if parts:
            lines.append(f"changed: {new_rows[key].dish.name}: {'; '.join(parts)}")
    return lines or ["no changes"]


def load_menu(path: str) -> Menu:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(f"cannot read {path}: {exc}") from exc
    return parse_menu(text)


def cmd_lint(args) -> int:
    menu = load_menu(args.menu)
    issues = lint(menu)
    for issue in issues:
        print(f"{args.menu}:{issue.line}: {issue.severity}: {issue.message}")
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = len(issues) - errors
    print(f"{errors} error(s), {warnings} warning(s) in {len(menu.dishes)} dish(es)")
    return EXIT_FAIL if errors or (args.werror and warnings) else EXIT_OK


def cmd_matrix(args) -> int:
    rows = analyse(load_menu(args.menu))
    print(render_matrix(rows, matrix_columns(rows, args.all), args.format), end="")
    return EXIT_OK


def cmd_safe(args) -> int:
    menu = load_menu(args.menu)
    avoid = parse_allergens(args.avoid)
    safe, blocked = screen(analyse(menu), avoid, args.strict)
    mode = "strict" if args.strict else "contains only"
    print(f"safe when avoiding {', '.join(avoid)} ({mode}): {len(safe)} of {len(menu.dishes)} dish(es)")
    for row in safe:
        print(f"  {row.dish.name}")
    if blocked:
        print("excluded:")
        for row, reasons in blocked:
            print(f"  {row.dish.name}: {'; '.join(reasons)}")
    return EXIT_OK


def cmd_show(args) -> int:
    print("\n".join(describe(load_menu(args.menu), args.dish)))
    return EXIT_OK


def cmd_where(args) -> int:
    print("\n".join(where(load_menu(args.menu), args.ingredient)))
    return EXIT_OK


def cmd_stats(args) -> int:
    menu = load_menu(args.menu)
    print("\n".join(stats(menu, analyse(menu))))
    return EXIT_OK


def cmd_card(args) -> int:
    menu = load_menu(args.menu)
    rows = analyse(menu)
    page = render_card(rows, matrix_columns(rows), Path(args.menu).stem)
    if args.output:
        try:
            Path(args.output).write_text(page, encoding="utf-8")
        except OSError as exc:
            raise CliError(f"cannot write {args.output}: {exc}") from exc
        print(f"wrote {args.output}")
    else:
        print(page, end="")
    return EXIT_OK


def cmd_diff(args) -> int:
    print("\n".join(diff(load_menu(args.old), load_menu(args.new))))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="menuguard", description="Offline restaurant allergen matrix checker.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("lint", help="check the menu and report line-numbered issues")
    p.add_argument("menu")
    p.add_argument("--werror", action="store_true", help="also fail on warnings")
    p.set_defaults(handler=cmd_lint)

    p = sub.add_parser("matrix", help="print the dish-by-allergen matrix")
    p.add_argument("menu")
    p.add_argument("--format", choices=("text", "md", "csv"), default="text")
    p.add_argument("--all", action="store_true", help="show every allergen column, not just used ones")
    p.set_defaults(handler=cmd_matrix)

    p = sub.add_parser("safe", help="list dishes safe for a guest's restrictions")
    p.add_argument("menu")
    p.add_argument("--avoid", required=True, help="comma-separated allergens, e.g. gluten,milk")
    p.add_argument("--strict", action="store_true", help="also exclude precautions and cross-contact")
    p.set_defaults(handler=cmd_safe)

    p = sub.add_parser("show", help="explain one dish")
    p.add_argument("menu")
    p.add_argument("dish")
    p.set_defaults(handler=cmd_show)

    p = sub.add_parser("where", help="find the dishes that use an ingredient")
    p.add_argument("menu")
    p.add_argument("ingredient")
    p.set_defaults(handler=cmd_where)

    p = sub.add_parser("stats", help="count dishes per allergen")
    p.add_argument("menu")
    p.set_defaults(handler=cmd_stats)

    p = sub.add_parser("card", help="write a printable HTML allergen card")
    p.add_argument("menu")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    p.set_defaults(handler=cmd_card)

    p = sub.add_parser("diff", help="compare two menu files")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(handler=cmd_diff)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except CliError as exc:
        print(f"menuguard: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
