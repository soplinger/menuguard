import pytest

import menuguard as mg

SAMPLE = """\
ingredient flour: gluten
ingredient butter: milk
ingredient cod: fish
ingredient prawn: crustacean
ingredient potato: none
ingredient oil: none
dish Chips: potato, oil
dish Battered cod: cod, flour, oil | shares: fryer | free-from: crustacean
dish Prawn tempura: prawn, flour, oil | shares: fryer
dish Buttered toast: flour, butter | free-from: egg | may-contain: sesame
"""


def menu_of(text):
    return mg.parse_menu(text)


def messages(issues):
    return [issue.message for issue in issues]


def row_named(menu, name):
    return next(row for row in mg.analyse(menu) if row.dish.name == name)


def test_parse_reads_ingredients_dishes_and_line_numbers():
    menu = menu_of(SAMPLE)
    assert len(menu.ingredients) == 6
    assert [dish.name for dish in menu.dishes] == [
        "Chips",
        "Battered cod",
        "Prawn tempura",
        "Buttered toast",
    ]
    assert menu.dishes[1].line == 8
    assert menu.dishes[1].shares == ["fryer"]
    assert menu.dishes[1].free_from == ["crustacean"]
    assert menu.issues == []


def test_ingredient_none_means_no_allergens():
    menu = menu_of("ingredient water: none\n")
    assert menu.ingredients["water"].allergens == frozenset()


def test_unknown_allergen_suggests_closest_match():
    menu = menu_of("ingredient bread: glutne\n")
    assert messages(menu.issues) == ["unknown allergen 'glutne' (did you mean 'gluten'?)"]
    assert menu.issues[0].line == 1


def test_duplicate_ingredient_is_a_parse_error_pointing_at_first_line():
    menu = menu_of("ingredient salt: none\n\ningredient salt: none\n")
    assert menu.issues[0].line == 3
    assert "already defined on line 1" in menu.issues[0].message


def test_unknown_directive_and_unknown_field_are_reported():
    menu = menu_of("recipe Soup: potato\ndish Soup: potato | colour: red\n")
    texts = messages(menu.issues)
    assert "unknown directive 'recipe'" in texts[0]
    assert "unknown field 'colour'" in texts[1]


def test_unknown_ingredient_in_dish_suggests_fix():
    menu = menu_of("ingredient flour: gluten\ndish Bread: flower\n")
    assert "unknown ingredient 'flower' (did you mean 'flour'?)" in messages(mg.lint(menu))


def test_free_from_contradiction_is_an_error():
    menu = menu_of(
        "ingredient flour: gluten\n"
        "dish Roll: flour | free-from: gluten\n"
    )
    issues = mg.lint(menu)
    assert issues[0].severity == "error"
    assert "claims free from gluten but contains it via flour" in issues[0].message


def test_free_from_and_may_contain_same_allergen_is_an_error():
    menu = menu_of("ingredient oil: none\ndish Fries: oil | free-from: sesame | may-contain: sesame\n")
    assert "both free-from and may-contain" in mg.lint(menu)[0].message


def test_cross_contact_breaking_a_free_from_claim_is_a_warning():
    issues = mg.lint(menu_of(SAMPLE))
    assert [issue.severity for issue in issues] == ["warning"]
    assert "claims free from crustacean but shares fryer with Prawn tempura" in issues[0].message


def test_unused_ingredient_and_duplicate_dish_are_flagged():
    menu = menu_of("ingredient salt: none\ndish Soup: potato\ndish soup: potato\n")
    texts = messages(mg.lint(menu))
    assert any("'salt' is not used" in text for text in texts)
    assert any("'soup' is already defined on line 2" in text for text in texts)


def test_analyse_sets_contains_precautions_and_cross_contact():
    menu = menu_of(SAMPLE)
    toast = row_named(menu, "Buttered toast")
    assert toast.cell("gluten") == "C"
    assert toast.cell("milk") == "C"
    assert toast.cell("sesame") == "M"
    assert toast.cell("crustacean") == ""

    cod = row_named(menu, "Battered cod")
    assert cod.cell("fish") == "C"
    assert cod.cell("crustacean") == "X"
    assert cod.shared_with == ("Prawn tempura",)


def test_screen_contains_only_versus_strict():
    rows = mg.analyse(menu_of(SAMPLE))
    safe, blocked = mg.screen(rows, ["crustacean"])
    assert "Battered cod" in [row.dish.name for row in safe]

    safe, blocked = mg.screen(rows, ["crustacean"], strict=True)
    assert "Battered cod" not in [row.dish.name for row in safe]
    reasons = dict((row.dish.name, why) for row, why in blocked)
    assert reasons["Battered cod"] == ["cross-contact crustacean"]
    assert reasons["Prawn tempura"] == ["contains crustacean"]


def test_screen_strict_blocks_declared_precaution():
    rows = mg.analyse(menu_of(SAMPLE))
    safe, _ = mg.screen(rows, ["sesame"])
    assert "Buttered toast" in [row.dish.name for row in safe]
    safe, _ = mg.screen(rows, ["sesame"], strict=True)
    assert "Buttered toast" not in [row.dish.name for row in safe]


def test_parse_allergens_rejects_unknown_and_empty():
    assert mg.parse_allergens("Gluten, milk, gluten") == ["gluten", "milk"]
    with pytest.raises(mg.CliError, match="did you mean 'gluten'"):
        mg.parse_allergens("glutne")
    with pytest.raises(mg.CliError, match="no allergens given"):
        mg.parse_allergens(" , ")


def test_matrix_columns_show_only_used_allergens_unless_all():
    rows = mg.analyse(menu_of(SAMPLE))
    assert mg.matrix_columns(rows) == ["crustacean", "fish", "gluten", "milk", "sesame"]
    assert len(mg.matrix_columns(rows, all_columns=True)) == len(mg.ALLERGENS)


def test_matrix_markdown_and_csv_shapes():
    rows = mg.analyse(menu_of(SAMPLE))
    columns = ["gluten", "milk"]
    md = mg.render_matrix(rows, columns, "md").splitlines()
    assert md[0] == "| Dish | gluten | milk |"
    assert md[2] == "| Chips | - | - |"
    csv_text = mg.render_matrix(rows, columns, "csv").splitlines()
    assert csv_text[0] == "Dish,gluten,milk"
    assert csv_text[-1] == "Buttered toast,C,C"


def test_card_escapes_dish_names():
    menu = menu_of("ingredient flour: gluten\ndish <b>Bun</b> & roll: flour\n")
    rows = mg.analyse(menu)
    page = mg.render_card(rows, mg.matrix_columns(rows), "Test")
    assert "&lt;b&gt;Bun&lt;/b&gt; &amp; roll" in page
    assert "<b>Bun</b>" not in page
    assert 'class="cell-C"' in page


def test_describe_explains_sources_and_cross_contact():
    lines = mg.describe(menu_of(SAMPLE), "battered COD")
    assert lines[0] == "Battered cod (line 8)"
    assert "  flour: gluten" in lines
    assert "contains: fish, gluten" in lines
    assert any(line.startswith("cross-contact: crustacean") and "Prawn tempura" in line for line in lines)


def test_describe_unknown_dish_raises():
    with pytest.raises(mg.CliError, match="no dish named 'Soup'"):
        mg.describe(menu_of(SAMPLE), "Soup")


def test_where_lists_users_of_an_ingredient():
    lines = mg.where(menu_of(SAMPLE), "Flour")
    assert lines[0] == "flour: gluten"
    assert lines[1:] == ["  used in Battered cod", "  used in Prawn tempura", "  used in Buttered toast"]
    with pytest.raises(mg.CliError):
        mg.where(menu_of(SAMPLE), "saffron")


def test_stats_counts_each_allergen():
    menu = menu_of(SAMPLE)
    lines = mg.stats(menu, mg.analyse(menu))
    assert lines[0] == "dishes: 4"
    gluten = next(line for line in lines if line.startswith("gluten"))
    assert "contains   3" in gluten
    assert "cross-contact   0" in gluten
    crustacean = next(line for line in lines if line.startswith("crustacean"))
    assert "contains   1" in crustacean
    assert "cross-contact   1" in crustacean


def test_diff_reports_added_removed_and_changed_dishes():
    old = menu_of("ingredient flour: gluten\ningredient oil: none\ndish Bread: flour, oil\ndish Chips: oil\n")
    new = menu_of("ingredient flour: gluten\ningredient oil: none\ndish Bread: oil\ndish Soup: oil\n")
    lines = mg.diff(old, new)
    assert "added: Soup (contains none)" in lines
    assert "removed: Chips" in lines
    assert "changed: Bread: no longer contains gluten" in lines


def test_diff_of_identical_menus_has_no_changes():
    menu = menu_of(SAMPLE)
    assert mg.diff(menu, menu) == ["no changes"]


def test_cli_lint_exit_codes(tmp_path, capsys):
    clean = tmp_path / "clean.txt"
    clean.write_text("ingredient potato: none\ndish Chips: potato\n", encoding="utf-8")
    assert mg.main(["lint", str(clean)]) == mg.EXIT_OK
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out

    broken = tmp_path / "broken.txt"
    broken.write_text("ingredient bread: glutne\n", encoding="utf-8")
    assert mg.main(["lint", str(broken)]) == mg.EXIT_FAIL
    assert "broken.txt:1: error: unknown allergen 'glutne'" in capsys.readouterr().out

    warn = tmp_path / "warn.txt"
    warn.write_text("ingredient salt: none\ningredient potato: none\ndish Chips: potato\n", encoding="utf-8")
    assert mg.main(["lint", str(warn)]) == mg.EXIT_OK
    assert mg.main(["lint", "--werror", str(warn)]) == mg.EXIT_FAIL


def test_cli_missing_file_is_usage_error(tmp_path, capsys):
    assert mg.main(["stats", str(tmp_path / "nope.txt")]) == mg.EXIT_USAGE
    assert "menuguard: cannot read" in capsys.readouterr().err


def test_cli_safe_prints_safe_and_excluded(tmp_path, capsys):
    path = tmp_path / "menu.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    assert mg.main(["safe", str(path), "--avoid", "crustacean", "--strict"]) == mg.EXIT_OK
    out = capsys.readouterr().out
    assert "safe when avoiding crustacean (strict): 2 of 4 dish(es)" in out
    assert "  Chips" in out
    assert "  Prawn tempura: contains crustacean" in out
