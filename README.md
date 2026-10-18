# Menuguard

**Offline restaurant allergen matrix checker.** Describe your menu once in a plain-text file, then get a dish-by-allergen matrix, safe-dish lists for a guest's restrictions, cross-contact warnings and a CI gate that stops a wrong "free-from" claim from reaching the printed card.

Menuguard is a single standard-library Python file. It has no accounts, no API keys, no servers and no network calls.

## Features

- **Line-numbered lint** of the menu file, with "did you mean" suggestions for typo'd allergens and ingredients.
- **Dish-by-allergen matrix** in aligned text, Markdown or CSV, with a legend.
- **Contains, precaution and cross-contact** per dish: `C` for contains, `M` for a declared may-contain precaution, `X` for allergens brought in by shared equipment such as a fryer.
- **Free-from claim verification**: a dish that says `free-from: gluten` is an error if any ingredient carries gluten, and a warning if shared kit exposes it.
- **Guest screening** with `safe --avoid gluten,milk`, which lists safe dishes and the reason each other dish is excluded. `--strict` also blocks precautions and cross-contact.
- **Dish explanations** (`show`) that trace each allergen back to the ingredient that carries it.
- **Ingredient usage** (`where`): find every dish that uses an ingredient, such as all the dishes with butter.
- **Allergen statistics** (`stats`): counts of dishes that contain, may contain, or are cross-contact exposed for each allergen.
- **Printable HTML card** (`card`) with colour-coded cells, escaped so dish names can't inject markup.
- **Menu diff** (`diff`) between two versions, reporting added and removed dishes and allergens gained or lost.
- **CI gate**: `lint` exits 1 on errors, and `lint --werror` also fails on warnings.
- **Usage and file errors** return exit code 2 with a clear message.

## Quick start

The menu format is one fact per line:

```text
ingredient flour: gluten
ingredient cod: fish
ingredient prawn: crustacean
ingredient peanut-oil: peanut
ingredient potato: none

dish Fish and chips: cod, flour, potato, peanut-oil | shares: fryer | free-from: crustacean
dish Prawn tempura: prawn, flour, peanut-oil | shares: fryer
```

`ingredient NAME: allergen, ...` maps an ingredient to allergens (`none` for no major allergens). `dish NAME: ingredient, ...` lists a dish's ingredients, with optional `| shares:`, `| free-from:` and `| may-contain:` fields. The full example is in [`examples/bistro-menu.txt`](examples/bistro-menu.txt).

Check the menu:

```sh
python3 menuguard.py lint examples/bistro-menu.txt
```

This reports the example's one cross-contact warning: Fish and chips claims to be free from crustacean, but it shares the fryer with Prawn tempura. Each issue is printed as `file:line: severity: message`, followed by a summary line.

Print the matrix, then screen for a guest who avoids gluten and milk:

```sh
python3 menuguard.py matrix examples/bistro-menu.txt
python3 menuguard.py safe examples/bistro-menu.txt --avoid gluten,milk --strict
```

Write a printable card and compare with last week:

```sh
python3 menuguard.py card examples/bistro-menu.txt -o bistro-card.html
python3 menuguard.py diff examples/bistro-menu-last-week.txt examples/bistro-menu.txt
```

## Commands

| Command | What it does |
| --- | --- |
| `lint MENU [--werror]` | Report every issue; exit 1 on errors (or warnings with `--werror`). |
| `matrix MENU [--format text\|md\|csv] [--all]` | Print the dish-by-allergen matrix. `--all` shows all 14 columns. |
| `safe MENU --avoid LIST [--strict]` | List safe dishes and why others are excluded. |
| `show MENU DISH` | Explain one dish, allergen by allergen. |
| `where MENU INGREDIENT` | List the dishes that use an ingredient. |
| `stats MENU` | Count dishes per allergen. |
| `card MENU [-o FILE]` | Write a printable HTML allergen card. |
| `diff OLD NEW` | Show what changed between two menu files. |

Exit codes: `0` success, `1` lint or gate failure, `2` usage or file error.

## Allergens

Menuguard uses the 14 major allergens of the EU and UK: celery, crustacean, egg, fish, gluten, lupin, milk, mollusc, mustard, peanut, sesame, soy, sulphites and tree-nut. Names are case-insensitive.

## How to run

Requires Python 3.8 or later. There is nothing to install.

```sh
python3 menuguard.py COMMAND ...
```

## How to test

```sh
python3 -m pytest -q
```

The tests need `pytest` only. They cover parsing, lint rules, cross-contact, screening, matrix and card rendering, diff, and the command-line exit codes.

## Notes and limits

Menuguard is a record-keeping and checking tool. It does not replace a kitchen's allergen procedures, supplier declarations or staff training, and its output is only as accurate as the ingredient data you enter. Keep ingredient allergen data up to date with your suppliers' specifications.

## Project layout

```text
menuguard.py                  the whole tool
examples/bistro-menu.txt      sample menu (lints with one cross-contact warning)
examples/bistro-menu-last-week.txt   older menu for the diff command
tests/                        pytest suite
```
