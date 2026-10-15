"""
Tests for the cluster reference table.

The table exists because the cluster definitions lived in prose and drifted:
four clusters named but empty, ten named holdings closed, and crypto.md
recording a weight of $9,240 against a real $4,100. So the tests here are
mostly about the two things that caused that — a moving number written down,
and a name nothing checks.

Run from project root:
    pytest tests/test_clusters.py
"""

from __future__ import annotations

import csv

import pandas as pd
import pandera.pandas as pa
import pytest

from src import book, claims, clusters, exposure


def _store(tmp_path, rows=None):
    """A cluster table on disk, defaulting to one sane thematic row."""
    rows = rows if rows is not None else [{
        "cluster_id": "Crypto", "layer": "thematic", "in_equity_base": True,
        "note_ref": "",
        "status": "active", "added": "2026-08-10",
    }]
    p = tmp_path / "clusters.csv"
    pd.DataFrame(rows, columns=clusters.COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return p


def _positions(tmp_path, rows):
    p = tmp_path / "positions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


# ─── the escape hatch ───

def test_a_cluster_needs_only_a_name_and_a_layer(tmp_path):
    """Target and cap can be decided later; classifying must never be blocked."""
    p = _store(tmp_path)
    clusters.add_cluster("Water", layer="thematic", path=p)
    row = clusters.get("Water", path=p)
    assert row["layer"] == "thematic"


def test_cluster_id_is_the_readable_name(tmp_path):
    """Same decision as instrument_id: the key is what a person calls it."""
    p = _store(tmp_path)
    clusters.add_cluster("Grid & Infrastructure", layer="thematic", path=p)
    assert clusters.get("Grid & Infrastructure", path=p)["cluster_id"] == (
        "Grid & Infrastructure"
    )


def test_unknown_cluster_teaches_the_fix(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError, match="add_cluster"):
        clusters.get("Nope", path=p)


def test_check_known_names_the_command(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError) as e:
        clusters.check_known(["Crypto", "Cyrpto"], path=p)
    assert "Cyrpto" in str(e.value) and "add_cluster" in str(e.value)


def test_blank_cluster_is_not_a_foreign_key_violation(tmp_path):
    """An instrument with no cluster yet is incomplete, not invalid."""
    p = _store(tmp_path)
    clusters.check_known(["Crypto", "", "  "], path=p)  # must not raise

def test_bad_layer_names_the_vocab_file(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(pa.errors.SchemaErrors) as e:
        clusters.add_cluster("Odd", layer="vibes", path=p)
    assert "ops/vocab/cluster_layer" in claims.explain_schema_error(e.value)


def test_in_equity_base_false_is_not_read_as_true(tmp_path):
    """
    bool("False") is True in Python.

    Read as text, the flag would be silently on for every row and cash would
    be reported as a share of the equity book — a sentence with no meaning.
    """
    p = _store(tmp_path, [{
        "cluster_id": "Dry Powder", "layer": "role", "in_equity_base": False,
        "note_ref": "",
        "status": "active", "added": "2026-08-10",
    }])
    assert clusters.load(p)["in_equity_base"].iloc[0] is False or (
        not clusters.load(p)["in_equity_base"].iloc[0]
    )


# ─── weights: derived, never stored ───

def test_weight_is_computed_not_stored(tmp_path, monkeypatch):
    """No column holds a weight — that is what let crypto.md go stale."""
    assert "current_pct" not in clusters.COLUMNS
    assert not any(c.startswith("current") for c in clusters.COLUMNS)


def test_cash_has_no_share_of_the_equity_book(tmp_path, monkeypatch):
    """`pct_of_equity` is blank where the cluster is not in the equity base."""
    cp = _store(tmp_path, [
        {"cluster_id": "Dry Powder", "layer": "role", "in_equity_base": False,
         "note_ref": "", "status": "active",
         "added": "2026-08-10"},
        {"cluster_id": "Crypto", "layer": "thematic", "in_equity_base": True,
         "note_ref": "", "status": "active",
         "added": "2026-08-10"},
    ])
    pp = _positions(tmp_path, [
        {"instrument_id": "USD_CASH", "market_value": 300.0},
        {"instrument_id": "IBIT", "market_value": 100.0},
    ])

    import pandas as pd_
    # `instrument_type` is real here, not decorative: book.lots() now
    # requires the column to exist (it refuses otherwise, Task 3 fix D), and
    # USD_CASH carries its true type so this test depends on the cash
    # predicate in book.in_basis() rather than only on the cluster's
    # in_equity_base flag happening to agree with it.
    fake = pd_.DataFrame([
        {"instrument_id": "USD_CASH", "cluster_id": "Dry Powder",
         "instrument_type": "cash"},
        {"instrument_id": "IBIT", "cluster_id": "Crypto",
         "instrument_type": "etf"},
    ])
    from src import instruments
    monkeypatch.setattr(instruments, "load", lambda *a, **k: fake)

    w = clusters.weights(path=cp, positions_path=pp).set_index("cluster_id")
    assert pd.isna(w.loc["Dry Powder", "pct_of_equity"])
    assert w.loc["Dry Powder", "pct_of_nav"] == 75.0     # 300 of 400
    assert w.loc["Crypto", "pct_of_equity"] == 100.0     # 100 of the 100 equity


def test_unclassified_money_is_excluded_from_both_denominators(tmp_path, monkeypatch):
    """A lot whose instrument_id instruments.csv does not know used to sit
    inside both denominators anyway — the old code grouped with
    `dropna=False`, so it was counted but filed under no cluster. book.lots()
    drops it instead, so every pct_of_nav/pct_of_equity here is computed
    against the classified total only, and book.unclassified() is where the
    dropped money must show up (found by review, 2026-10-06 — this is the
    test that would have caught the regression)."""
    cp = _store(tmp_path, [{
        "cluster_id": "Crypto", "layer": "thematic", "in_equity_base": True,
        "note_ref": "", "status": "active", "added": "2026-08-10",
    }])
    pp = _positions(tmp_path, [
        {"instrument_id": "IBIT", "market_value": 100.0},
        {"instrument_id": "GHOST", "market_value": 900.0},
    ])

    import pandas as pd_
    from src import instruments
    monkeypatch.setattr(instruments, "load", lambda *a, **k: pd_.DataFrame(
        [{"instrument_id": "IBIT", "cluster_id": "Crypto",
          "instrument_type": "stock"}]))

    w = clusters.weights(path=cp, positions_path=pp).set_index("cluster_id")
    assert w.loc["Crypto", "market_value"] == 100.0
    # Not 10.0 — GHOST's $900 is not in the denominator at all, classified or
    # not, so Crypto is 100% of a $100 book rather than 10% of a $1,000 one.
    assert w.loc["Crypto", "pct_of_nav"] == 100.0
    assert w.loc["Crypto", "pct_of_equity"] == 100.0

    from src import book
    unknown = book.unclassified(positions_path=pp)
    assert list(unknown["instrument_id"]) == ["GHOST"]
    assert float(unknown.iloc[0]["market_value"]) == 900.0


def test_drift_reports_an_empty_cluster(tmp_path, monkeypatch):
    """The named-but-empty case the constitution could not catch."""
    cp = _store(tmp_path, [
        {"cluster_id": "Crypto", "layer": "thematic", "in_equity_base": True,
         "note_ref": "", "status": "active",
         "added": "2026-08-10"},
        {"cluster_id": "Clean Energy", "layer": "thematic", "in_equity_base": True,
         "note_ref": "", "status": "empty",
         "added": "2026-08-10"},
    ])
    pp = _positions(tmp_path, [{"instrument_id": "IBIT", "market_value": 100.0}])

    import pandas as pd_
    from src import instruments
    monkeypatch.setattr(instruments, "load", lambda *a, **k: pd_.DataFrame(
        [{"instrument_id": "IBIT", "cluster_id": "Crypto",
          "instrument_type": "etf"}]))

    d = clusters.drift(path=cp, positions_path=pp).set_index("cluster_id")
    assert "you hold nothing in it" in d.loc["Clean Energy", "why"]


# ─── the tree ───
#
# Six rules keep `parent` a one-level tree. Five are checked inside
# clusters.validate() because they need only this file; the sixth (a group
# holds no positions) needs instruments.csv and lives in the commit guard.


def _tree(tmp_path, rows):
    """A cluster table with the parent column filled in per row."""
    return _store(tmp_path, rows)


def _row(cid, parent="", layer="thematic", in_equity_base=True):
    return {"cluster_id": cid, "parent": parent, "layer": layer,
            "in_equity_base": in_equity_base, "note_ref": "",
            "status": "active", "added": "2026-08-16"}


def test_a_cluster_with_no_parent_still_loads(tmp_path):
    """The twelve ungrouped clusters are the regression surface."""
    p = _tree(tmp_path, [_row("Crypto")])
    assert clusters.load(p).iloc[0]["cluster_id"] == "Crypto"


def test_a_child_names_its_group(tmp_path):
    p = _tree(tmp_path, [_row("Technology", layer="group"),
                         _row("Software", parent="Technology")])
    assert clusters.children("Technology", p) == ["Software"]


def test_parent_must_name_a_cluster_that_exists(tmp_path):
    """Rule 1 — the same foreign key discipline as every other link."""
    p = _tree(tmp_path, [_row("Software", parent="Technology")])
    with pytest.raises(ValueError, match="not a cluster"):
        clusters.load(p)


def test_a_cluster_cannot_be_its_own_parent(tmp_path):
    """Rule 2."""
    p = _tree(tmp_path, [_row("Technology", parent="Technology",
                              layer="group")])
    with pytest.raises(ValueError, match="itself"):
        clusters.load(p)


def test_groups_are_one_level_deep(tmp_path):
    """Rule 3 — a group inside a group is refused, which also kills cycles."""
    p = _tree(tmp_path, [_row("Everything", layer="group"),
                         _row("Technology", parent="Everything", layer="group"),
                         _row("Software", parent="Technology")])
    with pytest.raises(ValueError, match="one level"):
        clusters.load(p)


def test_a_group_and_its_child_must_agree_about_equity(tmp_path):
    """Rule 4 — otherwise '% of the equity book' has two denominators."""
    p = _tree(tmp_path, [_row("Technology", layer="group"),
                         _row("Software", parent="Technology",
                              in_equity_base=False)])
    with pytest.raises(ValueError, match="in_equity_base"):
        clusters.load(p)


def test_a_cluster_with_children_must_be_a_group(tmp_path):
    """Rule 5 — a theme that became a container is no longer a theme."""
    p = _tree(tmp_path, [_row("Technology", layer="thematic"),
                         _row("Software", parent="Technology")])
    with pytest.raises(ValueError, match="must be 'group'"):
        clusters.load(p)


def test_an_empty_group_is_allowed_so_one_can_be_built(tmp_path):
    """The mirror of rule 5 is deliberately NOT enforced.

    A group is empty for the moment between being created and having anything
    put in it. Refusing that refuses the only order in which a group can be
    built. `drift()` reports it instead — visible, never blocking.
    """
    p = _tree(tmp_path, [_row("Technology", layer="group")])
    assert clusters.load(p).iloc[0]["layer"] == "group"


def test_set_parent_puts_a_cluster_into_a_group(tmp_path):
    p = _tree(tmp_path, [_row("Technology", layer="group"), _row("Software")])
    clusters.set_parent("Software", "Technology", p)
    assert clusters.children("Technology", p) == ["Software"]


def test_set_parent_refuses_an_unknown_group(tmp_path):
    p = _tree(tmp_path, [_row("Software")])
    with pytest.raises(ValueError, match="not a cluster"):
        clusters.set_parent("Software", "Technology", p)


def test_set_parent_leaves_the_file_untouched_when_it_refuses(tmp_path):
    """A rejected edit must not half-write the table."""
    p = _tree(tmp_path, [_row("Software")])
    before = p.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        clusters.set_parent("Software", "Technology", p)
    assert p.read_text(encoding="utf-8") == before


# ─── weights: a group is the sum of its children ───
#
# `weights()` reads each position's cluster from instruments.load(), which
# takes no path, so these fake it exactly as
# test_cash_has_no_share_of_the_equity_book already does.

_GROUPED_MEMBERS = [
    {"instrument_id": "NVDA", "cluster_id": "AI Infrastructure",
     "instrument_type": "stock"},
    {"instrument_id": "MSFT", "cluster_id": "Software",
     "instrument_type": "stock"},
    {"instrument_id": "IBIT", "cluster_id": "Crypto", "instrument_type": "etf"},
]


def _grouped(tmp_path, monkeypatch, money):
    """Technology over two children plus one ungrouped cluster, priced.

    `money` maps instrument_id -> market value, so each test states only the
    numbers its assertion turns on.
    """
    from src import instruments

    cp = _tree(tmp_path, [
        _row("Technology", layer="group"),
        _row("AI Infrastructure", parent="Technology"),
        _row("Software", parent="Technology"),
        _row("Crypto"),
    ])
    pp = _positions(tmp_path, [
        {"instrument_id": iid, "market_value": v} for iid, v in money.items()
    ])
    monkeypatch.setattr(instruments, "load",
                        lambda *a, **k: pd.DataFrame(_GROUPED_MEMBERS))
    return cp, pp


def test_a_group_is_worth_the_sum_of_its_children(tmp_path, monkeypatch):
    cp, pp = _grouped(tmp_path, monkeypatch,
                      {"NVDA": 600.0, "MSFT": 300.0, "IBIT": 100.0})
    w = clusters.weights(path=cp, positions_path=pp).set_index("cluster_id")
    assert w.at["Technology", "market_value"] == 900.0
    assert w.at["AI Infrastructure", "market_value"] == 600.0
    assert w.at["Software", "market_value"] == 300.0


def test_the_leaves_alone_still_sum_to_nav(tmp_path, monkeypatch):
    """The one arithmetic trap: summing every row double-counts each group.

    1.9x here because this fixture groups 3 of its 4 clusters; the live book
    is 1.3x. 2x is only the limit case where everything is grouped.
    """
    cp, pp = _grouped(tmp_path, monkeypatch,
                      {"NVDA": 600.0, "MSFT": 300.0, "IBIT": 100.0})
    w = clusters.weights(path=cp, positions_path=pp)
    leaves = w[~w["is_group"]]
    assert round(leaves["market_value"].sum(), 2) == 1000.0
    assert round(leaves["pct_of_nav"].sum(), 2) == 100.0
    # and the naive sum is exactly the mistake the flag exists to prevent
    assert round(w["market_value"].sum(), 2) == 1900.0


def test_a_group_is_listed_immediately_before_its_own_children(tmp_path, monkeypatch):
    """Crypto is worth more than either child and must not land between
    Technology and them — which sorting by money alone would do."""
    cp, pp = _grouped(tmp_path, monkeypatch,
                      {"NVDA": 600.0, "MSFT": 300.0, "IBIT": 700.0})
    order = clusters.weights(path=cp, positions_path=pp)["cluster_id"].tolist()
    assert order == ["Technology", "AI Infrastructure", "Software", "Crypto"]


def test_a_group_is_marked_so_nothing_sums_it_by_accident(tmp_path, monkeypatch):
    cp, pp = _grouped(tmp_path, monkeypatch, {"NVDA": 600.0})
    w = clusters.weights(path=cp, positions_path=pp).set_index("cluster_id")
    assert bool(w.at["Technology", "is_group"]) is True
    assert bool(w.at["Crypto", "is_group"]) is False
    assert w.at["Software", "parent"] == "Technology"
    assert w.at["Crypto", "parent"] == ""


def test_drift_names_the_empty_child_not_the_group(tmp_path, monkeypatch):
    """A group whose children are all empty would otherwise be reported
    twice — once as itself and once per child."""
    cp, pp = _grouped(tmp_path, monkeypatch, {"IBIT": 100.0})
    named = clusters.drift(path=cp, positions_path=pp)["cluster_id"].tolist()
    assert "AI Infrastructure" in named
    assert "Software" in named
    assert "Technology" not in named


def test_drift_names_a_group_with_nothing_inside_it(tmp_path, monkeypatch):
    """The safety net the design traded the blocking rule for.

    `a group must have children` was deliberately not enforced, on the stated
    grounds that drift() would report an empty one instead. It did not — a
    childless group has no children to be reported through, which is the exact
    case the trade was reasoning about (found by review, 2026-08-16).
    """
    from src import instruments

    cp = _tree(tmp_path, [_row("Energy Complex", layer="group"), _row("Crypto")])
    pp = _positions(tmp_path, [{"instrument_id": "IBIT", "market_value": 700.0}])
    monkeypatch.setattr(instruments, "load", lambda *a, **k: pd.DataFrame(
        [{"instrument_id": "IBIT", "cluster_id": "Crypto",
          "instrument_type": "etf"}]))
    d = clusters.drift(path=cp, positions_path=pp)
    assert "Energy Complex" in d["cluster_id"].tolist()
    assert "no clusters inside it" in d.set_index("cluster_id").at["Energy Complex", "why"]


def test_a_group_cannot_be_put_inside_another_group(tmp_path):
    """Rule 3 checked the parent's parent, so a CHILDLESS group nested one
    level down slipped through — the table said 'a group cannot sit inside a
    group' and the code did not check it (found by review, 2026-08-16).
    """
    p = _tree(tmp_path, [_row("Commodities", layer="group"),
                         _row("Technology", parent="Commodities", layer="group")])
    with pytest.raises(ValueError, match="one level"):
        clusters.load(p)


# ─── set_note_ref: linking a note was a hand-edit of a guarded file ───

def test_set_note_ref_links_a_cluster_to_its_note(tmp_path):
    """Nothing in src/ wrote this column, so the only route was editing the CSV.

    A cluster note written without it reports "missing" for ever —
    cluster_summary() reads note state from THAT column, not from the file
    being on disk. Both cluster notes written on 2026-09-27 needed a hand-edit
    of data/clusters.csv to become visible. Found by audit 2026-09-27.
    """
    store = _store(tmp_path)
    clusters.add_cluster("Metals", layer="thematic", path=store)
    note = tmp_path / "metals.md"
    note.write_text("# Metals\n", encoding="utf-8")

    clusters.set_note_ref("Metals", str(note), path=store)
    row = clusters.load(store).set_index("cluster_id").loc["Metals"]
    assert row["note_ref"] == str(note)


def test_set_note_ref_refuses_an_unknown_cluster(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeyError, match="Unknown cluster"):
        clusters.set_note_ref("Nope", "notes/clusters/nope.md", path=store)


def test_set_note_ref_refuses_a_path_that_is_not_a_file(tmp_path):
    """The same rule every other note_ref writer enforces: a link must point at
    something that exists, or /show reports a note nobody can read."""
    store = _store(tmp_path)
    clusters.add_cluster("Metals", layer="thematic", path=store)
    with pytest.raises(ValueError, match="does not point to a file"):
        clusters.set_note_ref("Metals", "notes/clusters/not-written-yet.md",
                              path=store)


def test_set_note_ref_leaves_the_file_untouched_when_it_refuses(tmp_path):
    store = _store(tmp_path)
    clusters.add_cluster("Metals", layer="thematic", path=store)
    before = store.read_text()
    with pytest.raises(ValueError):
        clusters.set_note_ref("Metals", "nope.md", path=store)
    assert store.read_text() == before


# ─── set_status — retiring a theme ───

def test_set_status_retires_a_cluster(tmp_path):
    """Nothing could write this column after add_cluster(), which is why
    Real Estate and Fixed Income still said `active` holding nothing."""
    p = tmp_path / "clusters.csv"
    clusters.add_cluster("Metals", layer="thematic", path=p)
    clusters.set_status("Metals", "retired", path=p)
    assert clusters.get("Metals", p)["status"] == "retired"


def test_set_status_refuses_a_value_outside_the_vocabulary(tmp_path):
    p = tmp_path / "clusters.csv"
    clusters.add_cluster("Metals", layer="thematic", path=p)
    with pytest.raises(Exception):
        clusters.set_status("Metals", "mothballed", path=p)


def test_set_status_refuses_an_unknown_cluster(tmp_path):
    p = tmp_path / "clusters.csv"
    with pytest.raises(KeyError, match="Nope|Unknown"):
        clusters.set_status("Nope", "retired", path=p)


def test_set_status_refuses_to_retire_a_cluster_that_still_holds_money(tmp_path):
    """A retired cluster that shows money is a slot the record calls closed
    and the numbers call open — and notes.status() drops the row, so its note
    requirement vanishes too. Found by review 2026-09-28."""
    p = tmp_path / "clusters.csv"
    clusters.add_cluster("Metals", layer="thematic", path=p)
    inst = tmp_path / "instruments.csv"
    pd.DataFrame([{c: "" for c in __import__("src.instruments", fromlist=["x"]).COLUMNS}]
                 ).to_csv(inst, index=False)
    pos = tmp_path / "positions.csv"
    pd.DataFrame([{"instrument_id": "GDX"}]).to_csv(pos, index=False)
    import src.instruments as ins_mod
    real = ins_mod.load
    ins_mod.load = lambda *a, **k: pd.DataFrame(
        [{"instrument_id": "GDX", "cluster_id": "Metals"}])
    try:
        with pytest.raises(ValueError, match="still holds"):
            clusters.set_status("Metals", "retired", path=p, positions_path=pos)
    finally:
        ins_mod.load = real


def test_set_status_refuses_to_retire_a_group_with_live_children(tmp_path):
    p = tmp_path / "clusters.csv"
    clusters.add_cluster("Industrials", layer="group", path=p)
    clusters.add_cluster("Industrials (broad)", layer="sector-breadth",
                         parent="Industrials", path=p)
    with pytest.raises(ValueError, match="still active|children"):
        clusters.set_status("Industrials", "retired", path=p,
                            positions_path=tmp_path / "nope.csv")


def test_set_status_names_the_fix_for_an_unknown_cluster(tmp_path):
    """Every other writer in this module carries the "Add it with:" line."""
    p = tmp_path / "clusters.csv"
    clusters.add_cluster("Metals", layer="thematic", path=p)
    with pytest.raises(KeyError, match="add_cluster"):
        clusters.set_status("Nope", "retired", path=p)


def test_drift_does_not_report_a_retired_cluster(tmp_path):
    """Holding nothing is the definition of retired, so reporting it is noise
    in the one report that exists to find forgotten themes."""
    p = tmp_path / "clusters.csv"
    clusters.add_cluster("Metals", layer="thematic", path=p)
    clusters.add_cluster("Clean Energy", layer="thematic", path=p)
    clusters.set_status("Metals", "retired", path=p,
                        positions_path=tmp_path / "nope.csv")
    pos = tmp_path / "positions.csv"
    pd.DataFrame(columns=["ticker", "instrument_id", "market_value"]).to_csv(
        pos, index=False)
    named = set(clusters.drift(p, pos)["cluster_id"])
    assert "Metals" not in named and "Clean Energy" in named


def test_set_layer_changes_the_kind_of_slot(tmp_path):
    """Healthcare stopped being a theme on 2026-09-29 and became a sector slot."""
    path = tmp_path / "clusters.csv"
    clusters.add_cluster("Healthcare", layer="thematic", path=path)
    clusters.set_layer("Healthcare", "sector-breadth", path=path)
    assert clusters.get("Healthcare", path=path)["layer"] == "sector-breadth"


def test_set_layer_refuses_a_value_outside_the_vocabulary(tmp_path):
    """A typo must leave the file exactly as it was, not half-written."""
    path = tmp_path / "clusters.csv"
    clusters.add_cluster("Healthcare", layer="thematic", path=path)
    before = path.read_text()
    with pytest.raises(pa.errors.SchemaErrors):
        clusters.set_layer("Healthcare", "sector breadth", path=path)
    assert path.read_text() == before


def test_set_layer_refuses_making_a_group_of_a_cluster_that_holds_positions(tmp_path):
    """Found by review 2026-09-29: this state blocks every later commit.

    weights() drops a group's money out of the rows that sum to NAV, and the
    commit hook refuses anything filed directly into a group — so the only way
    back is hand-editing the file set_layer() exists to replace.
    """
    path = tmp_path / "clusters.csv"
    clusters.add_cluster("Healthcare", layer="thematic", path=path)
    before = path.read_text()
    import src.instruments as ins_mod
    real = ins_mod.load
    ins_mod.load = lambda *a, **k: pd.DataFrame(
        [{"instrument_id": "MRK", "cluster_id": "Healthcare"}])
    try:
        with pytest.raises(ValueError, match="holds"):
            clusters.set_layer("Healthcare", "group", path=path)
    finally:
        ins_mod.load = real
    assert path.read_text() == before


def test_set_layer_refuses_demoting_a_group_that_still_has_children(tmp_path):
    """The tree rule the docstring promises, actually exercised."""
    path = tmp_path / "clusters.csv"
    clusters.add_cluster("Technology", layer="group", path=path)
    clusters.add_cluster("Software", layer="thematic", parent="Technology",
                         path=path)
    before = path.read_text()
    with pytest.raises(Exception):
        clusters.set_layer("Technology", "thematic", path=path)
    assert path.read_text() == before


def test_set_layer_refuses_an_unknown_cluster(tmp_path):
    path = tmp_path / "clusters.csv"
    clusters.add_cluster("Healthcare", layer="thematic", path=path)
    with pytest.raises(KeyError):
        clusters.set_layer("Nope", "role", path=path)


# ─── tree: three levels, in the order it is drawn ───

def _tree_book(tmp_path, technology_status="active"):
    """A group with two themes, a role cluster outside the equity base, one
    empty live theme and one retired theme.

    `technology_status` (pre-push review 2026-10-07, finding 3): lets a
    caller retire the GROUP directly in the file while its two children
    (AI Infrastructure, Software) stay active — the orphan-parent shape
    `clusters.set_status()` itself refuses to create (it checks that a
    group's own children are all retired first), so reaching it at all
    means writing the table directly, the same hand-edit route the
    finding names. Every existing caller keeps the default and is
    unaffected."""
    import csv as _csv
    from src import instruments
    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "BIG", "instrument_id": "BIG", "shares": 10,
         "cost_basis": 900, "last_price": 100, "currency": "USD",
         "market_value": 1000, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "SML", "instrument_id": "SML", "shares": 1,
         "cost_basis": 90, "last_price": 100, "currency": "USD",
         "market_value": 100, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "BILL", "instrument_id": "BILL", "shares": 5,
         "cost_basis": 490, "last_price": 100, "currency": "USD",
         "market_value": 500, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "BIG", "name": "Big", "instrument_type": "stock",
         "sector": "Technology", "cluster_id": "AI Infrastructure",
         "note_ref": "notes/positions/BIG.md", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "SML", "name": "Small", "instrument_type": "stock",
         "sector": "Technology", "cluster_id": "Software",
         "note_ref": "", "status": "active", "added": "2026-01-01"},
        {"instrument_id": "BILL", "name": "Bills", "instrument_type": "etf",
         "cluster_id": "Dry Powder", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": "Technology", "parent": "", "layer": "group",
         "in_equity_base": True, "note_ref": "notes/clusters/technology.md",
         "status": technology_status, "added": "2026-01-01"},
        {"cluster_id": "AI Infrastructure", "parent": "Technology",
         "layer": "thematic", "in_equity_base": True,
         "note_ref": "notes/clusters/ai-infrastructure.md", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Software", "parent": "Technology", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Dry Powder", "parent": "", "layer": "role",
         "in_equity_base": False, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Clean Energy", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "empty",
         "added": "2026-01-01"},
        {"cluster_id": "Old Metals", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "retired",
         "added": "2026-01-01"},
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    # instruments.load() defaults at the production table; tree() reads it
    # through book.lots(), which takes the path.
    return {"positions_path": pos, "clusters_path": cl,
            "instruments_path": inst}


def test_tree_runs_group_then_its_clusters_then_their_holdings(tmp_path):
    """Spec D5: ranked bars drilled three levels. The order IS the drawing
    order — page.py shapes rows and does not re-sort, so a wrong order here is
    a wrong page."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    seq = list(zip(t["level"], t["cluster_id"], t["instrument_id"]))
    assert seq[0] == ("group", "Technology", "")
    assert seq[1][:2] == ("cluster", "AI Infrastructure")
    assert seq[2][0] == "holding"
    assert seq[2][2] == "BIG"


def test_a_group_total_is_its_children_and_is_not_added_to_them(tmp_path):
    """Summing every row over-counts: a group row carries its children's money
    as well as its own name. The rows that add up to NAV are the leaves."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    group = t[t["level"] == "group"].set_index("cluster_id")
    kids = t[(t["level"] == "cluster") & (t["parent"] == "Technology")]
    assert group.at["Technology", "market_value"] == pytest.approx(
        kids["market_value"].sum())
    assert t[t["level"] == "holding"]["market_value"].sum() == pytest.approx(
        book.nav("everything", positions_path=paths["positions_path"],
                 clusters_path=paths["clusters_path"],
                 instruments_path=paths["instruments_path"]))


def test_an_empty_live_theme_is_last_and_flagged(tmp_path):
    """Stepping out of a theme is part of the spread (spec, panel 2), so it is
    shown — greyed, at the end, never mixed into the ranking."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    empty = t[t["is_empty"]]
    assert set(empty["cluster_id"]) == {"Clean Energy"}
    assert t.index[t["is_empty"]].min() > t.index[~t["is_empty"]].max()


def test_a_retired_theme_is_not_in_the_tree_at_all(tmp_path):
    """A theme he closed is not part of the spread. drift() already draws this
    line — retiring Metals and Water added two permanent rows saying exactly
    what `retired` already said."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    assert "Old Metals" not in set(t["cluster_id"])


def test_a_live_cluster_orphaned_by_a_retired_parent_still_gets_a_row(tmp_path):
    """Fix, pre-push review 2026-10-07 (finding 3) — the fourth state
    `tree()`'s own docstring forbids. `tree()` filters retired rows BEFORE
    building the parent map, so a live cluster whose PARENT was retired
    (its own status untouched) names a parent no longer in `df.index` —
    non-blank, so the old top-level test (`not parent[cid]`) skipped it;
    its own id still known, so the unfiled bucket's foreign-key check
    never caught it either. It landed in neither place.

    Reproduced on a shape of the real book: Technology retired while its
    two children, AI Infrastructure and Software, stay active —
    `clusters.set_status()` itself refuses to create this directly (it
    checks a group's children are all retired first), so this is reachable
    only by writing the table directly, same as `_tree_book`'s own
    `technology_status` param does here, or by `set_status(child,
    "active")` later while the group stays retired — the guard only runs
    in the retiring direction."""
    paths = _tree_book(tmp_path, technology_status="retired")
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])

    assert "Technology" not in set(t["cluster_id"]), (
        "the retired group itself must still be gone"
    )
    assert {"AI Infrastructure", "Software"} <= set(t["cluster_id"]), (
        "the two live children of the retired group vanished instead of "
        "getting their own row"
    )
    ai = t[(t["cluster_id"] == "AI Infrastructure") &
          (t["level"] == "cluster")].iloc[0]
    sw = t[(t["cluster_id"] == "Software") & (t["level"] == "cluster")].iloc[0]
    assert ai["parent"] == "", "promoted to top-level, not left under a ghost"
    assert sw["parent"] == ""
    assert ai["market_value"] == pytest.approx(1000.0)   # BIG
    assert sw["market_value"] == pytest.approx(100.0)    # SML

    nav = book.nav("everything", positions_path=paths["positions_path"],
                   clusters_path=paths["clusters_path"],
                   instruments_path=paths["instruments_path"])
    assert t[t["level"] == "holding"]["market_value"].sum() == pytest.approx(nav), (
        "money fell out of the tree entirely — the fourth state tree()'s "
        "own docstring forbids"
    )


def test_tree_coverage_pct_reads_100_when_the_tree_is_complete(tmp_path):
    """The normal case: every dollar in the basis belongs to some row
    `tree()` draws, so the derived coverage must read 100.0 — the honest
    answer the By-theme panel's Total row now shows instead of an assumed
    constant (fix, pre-push review 2026-10-07, finding 3)."""
    paths = _tree_book(tmp_path)
    for basis in book.BASES:
        got = clusters.tree_coverage_pct(basis, paths["clusters_path"],
                                         paths["positions_path"],
                                         paths["instruments_path"])
        assert got == pytest.approx(100.0, abs=0.01)


def test_tree_coverage_pct_reveals_a_shortfall(tmp_path, monkeypatch):
    """The failure this function exists to catch — finding 3's own point:
    the By-theme panel's Total row used to hard-code 100%, the one number
    that could have revealed real money missing from every row `tree()`
    draws, structurally incapable of doing so. `tree()` itself is fixed
    now (the orphan case lands its own row), so this monkeypatches `tree()`
    to return the exact PRE-FIX shape instead — the whole Technology/AI
    Infrastructure/Software family dropped, same as the live defect — so
    this test pins the DETECTOR on its own arithmetic, independent of
    whatever `tree()` does today or after some future, different bug."""
    paths = _tree_book(tmp_path)
    real_tree = clusters.tree

    def dropped(*a, **k):
        t = real_tree(*a, **k)
        return t[~t["cluster_id"].isin(
            ["Technology", "AI Infrastructure", "Software"]
        )].reset_index(drop=True)

    monkeypatch.setattr(clusters, "tree", dropped)
    got = clusters.tree_coverage_pct("everything", paths["clusters_path"],
                                     paths["positions_path"],
                                     paths["instruments_path"])
    assert got < 100.0, (
        "dropping a whole live family from tree() must show up as missing "
        "coverage, not a silent 100%"
    )


def test_a_theme_empty_only_because_of_the_basis_is_not_called_empty(tmp_path):
    """Dry Powder holds real money and holds nothing under pure-equity. Greying
    it as 'named but you hold nothing in it' would be false — the basis is what
    emptied it, and the basis is on screen."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("pure-equity", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    row = t[t["cluster_id"] == "Dry Powder"]
    assert row.empty or not bool(row["is_empty"].iloc[0])


def test_every_percentage_is_of_the_basis_asked_for(tmp_path):
    paths = _tree_book(tmp_path)
    for basis in book.BASES:
        t = clusters.tree(basis, paths["clusters_path"],
                          paths["positions_path"], paths["instruments_path"])
        leaves = t[t["level"] == "holding"]
        assert leaves["pct_of_basis"].sum() == pytest.approx(100.0, abs=0.1)


def test_a_holding_carries_its_note_so_a_bar_can_be_clicked(tmp_path):
    """Spec panel 2: clicking a ticker opens its page, clicking a theme opens
    the theme's note. The link is data, not something the renderer guesses."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    big = t[t["instrument_id"] == "BIG"].iloc[0]
    assert big["note_ref"] == "notes/positions/BIG.md"


def test_a_cluster_with_no_note_has_a_blank_ref_never_the_string_nan(tmp_path):
    """A blank note_ref means no link at all — a bar he cannot click because
    nothing is written there is itself information, never a link to a file
    that was never real.

    `load()` reads a blank CSV cell as the float NaN (`na_values=[""]`,
    same reason `parent` needs its own `.fillna("")` two lines above this
    column's). Left unfilled, `row()` builds this column with a bare
    `str(...)`, and `str(float("nan"))` is the four-character string
    `"nan"` — not blank, so `page._href()` would treat it as a REAL ref
    and turn it into a clickable link to `../nan`. `Software` here is
    exactly that case on file: active, held (`SML`), blank `note_ref`.
    """
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    software = t[(t["cluster_id"] == "Software")
                & (t["level"] == "cluster")].iloc[0]
    assert software["note_ref"] == ""
    assert software["note_ref"] != "nan"


# ─── tree, fix round 1: local emptiness ordering, the unfiled bucket, parent depth ───

def _nested_empty_book(tmp_path):
    """A group with one live child and one globally-empty child, plus a
    separate, smaller, standalone live family.

    Built to reproduce the review's finding: with ranking keyed on money
    magnitude alone, an empty child nested in a big group sorted ahead of a
    smaller but fully live standalone family, simply because its group's
    total was bigger. The fix keeps emptiness a LOCAL, not a global, ordering
    fact.
    """
    import csv as _csv
    from src import instruments
    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "AAA", "instrument_id": "AAA", "shares": 10,
         "cost_basis": 900, "last_price": 100, "currency": "USD",
         "market_value": 1000, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "BBB", "instrument_id": "BBB", "shares": 1,
         "cost_basis": 9, "last_price": 10, "currency": "USD",
         "market_value": 10, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "AAA", "name": "Triple A", "instrument_type": "stock",
         "cluster_id": "Live Child", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "BBB", "name": "Small Beta", "instrument_type": "stock",
         "cluster_id": "Small Family", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": "Big Group", "parent": "", "layer": "group",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Empty Child", "parent": "Big Group", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Live Child", "parent": "Big Group", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Small Family", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl, "instruments_path": inst}


def test_an_empty_child_stays_in_its_groups_block(tmp_path):
    """Critical 1 (review, fix round 1): an empty child sorts after its own
    group's live children, but does not jump out of its group's block past a
    smaller, fully live standalone family — the two invariants ("empty is
    last" and "a group's children stay together") conflict, and the group is
    the one that wins, because everything starts collapsed and a group is its
    children's context."""
    paths = _nested_empty_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    idx = {cid: i for i, cid in enumerate(t["cluster_id"])
           if cid in ("Live Child", "Empty Child", "Small Family")}
    # still inside its own group's block: after the live sibling...
    assert idx["Live Child"] < idx["Empty Child"]
    # ...but before the next, smaller, fully live family — not stranded past it.
    assert idx["Empty Child"] < idx["Small Family"]

    empty_row = t[t["cluster_id"] == "Empty Child"].iloc[0]
    assert bool(empty_row["is_empty"]) is True
    assert empty_row["parent"] == "Big Group"


def _stray_lot_book(tmp_path, cluster_cell, cluster_rows):
    """One $900 lot whose instrument names `cluster_cell`, against a cluster
    table that — depending on the test — never had that cluster, still has
    it but retired, or has nothing at all. `cluster_rows` may be empty."""
    import csv as _csv
    from src import instruments
    pos = tmp_path / "positions.csv"
    pd.DataFrame([{"ticker": "GHOST", "instrument_id": "GHOST", "shares": 1,
                   "cost_basis": 900, "last_price": 900, "currency": "USD",
                   "market_value": 900.0, "priced_at": "2026-10-02",
                   "notes": ""}]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    idf = pd.DataFrame([{"instrument_id": "GHOST", "name": "Ghost Co",
                        "instrument_type": "stock", "cluster_id": cluster_cell,
                        "note_ref": "", "status": "active",
                        "added": "2026-01-01"}])
    for col in instruments.COLUMNS:
        if col not in idf.columns:
            idf[col] = ""
    idf[instruments.COLUMNS].to_csv(inst, index=False,
                                    quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    cdf = pd.DataFrame(cluster_rows, columns=clusters.COLUMNS) if cluster_rows \
        else pd.DataFrame(columns=clusters.COLUMNS)
    cdf.to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl, "instruments_path": inst}


def _assert_ghost_is_unfiled_and_sums(paths, basis="everything"):
    t = clusters.tree(basis, paths["clusters_path"], paths["positions_path"],
                      paths["instruments_path"])
    bucket = t[t["cluster_id"] == clusters.UNFILED_LABEL]
    assert not bucket.empty, "the unfiled bucket must appear — money dropped"
    cluster_row = bucket[bucket["level"] == "cluster"].iloc[0]
    assert bool(cluster_row["is_empty"]) is False
    ghost = bucket[bucket["instrument_id"] == "GHOST"]
    assert not ghost.empty
    from src import book
    nav = book.nav(basis, positions_path=paths["positions_path"],
                   clusters_path=paths["clusters_path"],
                   instruments_path=paths["instruments_path"])
    assert t[t["level"] == "holding"]["market_value"].sum() == pytest.approx(nav)
    return t


def test_a_blank_cluster_lands_in_the_unfiled_bucket_not_nowhere(tmp_path):
    """Critical 2, cause 1 (review, fix round 1): `add_instrument()`
    deliberately allows a blank `cluster_id` so a missing theme never blocks
    recording a trade — the normal state of every freshly bought ticker."""
    paths = _stray_lot_book(tmp_path, "", [
        {"cluster_id": "Some Cluster", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ])
    _assert_ghost_is_unfiled_and_sums(paths)


def test_an_unknown_cluster_lands_in_the_unfiled_bucket_not_nowhere(tmp_path):
    """Critical 2, cause 2: the instrument names a cluster data/clusters.csv
    does not have at all — a hand-edit or an unguarded write, not something
    the commit hook would let through, but tree() must not crash or drop the
    money when it happens anyway."""
    paths = _stray_lot_book(tmp_path, "Nonexistent Theme", [
        {"cluster_id": "Some Cluster", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ])
    _assert_ghost_is_unfiled_and_sums(paths)


def test_a_retired_clusters_money_lands_in_the_unfiled_bucket_not_nowhere(tmp_path):
    """Critical 2, cause 3: the instrument still names a cluster he has since
    retired. `set_status()` refuses to retire a cluster while it still holds
    money, but nothing stops him buying the SAME instrument again later,
    which is exactly the moment this becomes live."""
    paths = _stray_lot_book(tmp_path, "Old Theme", [
        {"cluster_id": "Old Theme", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "retired",
         "added": "2026-01-01"},
    ])
    _assert_ghost_is_unfiled_and_sums(paths)


def test_parent_is_the_row_one_level_up_at_every_level(tmp_path):
    """Minor 4 (review, fix round 1): `parent` must mean one thing throughout
    — a group's parent is blank, a cluster's parent is its group or blank,
    and a holding's parent is the cluster that holds it, never that
    cluster's own group."""
    paths = _tree_book(tmp_path)
    t = clusters.tree("everything", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    group_row = t[(t["level"] == "group") & (t["cluster_id"] == "Technology")].iloc[0]
    cluster_row = t[(t["level"] == "cluster") &
                    (t["cluster_id"] == "AI Infrastructure")].iloc[0]
    holding_row = t[(t["level"] == "holding") & (t["instrument_id"] == "BIG")].iloc[0]
    assert group_row["parent"] == ""
    assert cluster_row["parent"] == "Technology"
    assert holding_row["parent"] == "AI Infrastructure"


def _tie_at_zero_basis_book(tmp_path):
    """Two children of one group tie at zero money IN THE BASIS, for
    different reasons: one holds nothing anywhere; the other holds real
    money that a cash-stripping basis excludes. Magnitude alone cannot tell
    them apart — both are 0 under the basis — only `held_anywhere` can.
    Named so the alphabetical tiebreak the OLD key fell back on would get it
    wrong: "Alpha" sorts before "Zulu", but Zulu (not actually empty) must
    rank first.
    """
    import csv as _csv
    from src import instruments
    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "CASHX", "instrument_id": "CASHX", "shares": 0,
         "cost_basis": 0, "last_price": 0, "currency": "USD",
         "market_value": 500.0, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "CASHX", "name": "House Cash",
         "instrument_type": "cash", "cluster_id": "Zulu Child",
         "note_ref": "", "status": "active", "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": "Big Group", "parent": "", "layer": "group",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Alpha Child", "parent": "Big Group", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "Zulu Child", "parent": "Big Group", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl, "instruments_path": inst}


def test_a_zero_magnitude_tie_is_broken_by_emptiness_not_by_name(tmp_path):
    """Fix round 2 (review): the nested-case test alone could not fail,
    because a genuinely empty child's `direct` money is already the minimum
    magnitude, so the OLD magnitude-only key already happened to put it
    last — nothing exercised the `held_anywhere[c] == 0` term itself. This
    fixture forces the tie the term exists to break: Alpha Child holds
    nothing anywhere (`direct == 0`, `held_anywhere == 0`); Zulu Child holds
    $500 that `minus-cash` strips out (`direct == 0`, `held_anywhere > 0`).
    Both sit at magnitude 0, so only `held_anywhere` can rank Zulu ahead of
    Alpha — the alphabetical fallback alone would get it backwards.
    """
    paths = _tie_at_zero_basis_book(tmp_path)
    t = clusters.tree("minus-cash", paths["clusters_path"],
                      paths["positions_path"], paths["instruments_path"])
    idx = {cid: i for i, cid in enumerate(t["cluster_id"])
           if cid in ("Alpha Child", "Zulu Child")}
    assert idx["Zulu Child"] < idx["Alpha Child"]

    rows = t[t["level"] == "cluster"].set_index("cluster_id")
    assert bool(rows.at["Zulu Child", "is_empty"]) is False
    assert bool(rows.at["Alpha Child", "is_empty"]) is True


# ─── overlap: two themes can be one bet ───
#
# A cluster records why he bought something, not what he actually owns — so
# two clusters can quietly be the same bet, named two ways. These fixtures
# extend _tree_book()'s pattern (a positions/instruments/clusters CSV trio
# under tmp_path) with a fourth table, market/etf_holdings.csv, the same way
# tests/test_exposure.py's _holdings_table() does, because a company only
# shows up twice when at least one side is reached THROUGH a fund.

def _overlap_book(tmp_path):
    """Two independent pairs of themes, each sharing one company two ways.

    AI Infrastructure reaches NVDA through QFUND, whose stored holdings cover
    only 80% of it (NVDA 0.5 + MSFT 0.3) — a floor. Momentum Stocks holds the
    same NVDA directly, at a smaller dollar value, so the shared slice is
    capped by the smaller side (and inherits the floor from the larger one).

    Dividend Aristocrats reaches KO through EXACTFUND, which is fully
    accounted for (no floor) — pinned at exactly $100, the Task 6 brief's own
    number. Value Picks holds the same KO directly at $30 — the brief's other
    number, so the shared value this fixture produces ($30, not $130 and not
    $65) is the literal worked example from the task brief.

    The two pairs share nothing with each other (NVDA/MSFT vs KO), so
    overlap() must return exactly these two rows and no cross pairs.
    """
    import csv as _csv
    from src import instruments, market

    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "NVDA", "instrument_id": "NVDA", "shares": 1,
         "cost_basis": 40, "last_price": 40, "currency": "USD",
         "market_value": 40.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "QFUND", "instrument_id": "QFUND", "shares": 1,
         "cost_basis": 200, "last_price": 200, "currency": "USD",
         "market_value": 200.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "KO", "instrument_id": "KO", "shares": 1,
         "cost_basis": 30, "last_price": 30, "currency": "USD",
         "market_value": 30.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "EXACTFUND", "instrument_id": "EXACTFUND", "shares": 1,
         "cost_basis": 100, "last_price": 100, "currency": "USD",
         "market_value": 100.0, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "cluster_id": "Momentum Stocks", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "QFUND", "name": "Quant Fund", "instrument_type": "etf",
         "cluster_id": "AI Infrastructure", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "KO", "name": "Coca-Cola", "instrument_type": "stock",
         "cluster_id": "Value Picks", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "EXACTFUND", "name": "Exact Fund",
         "instrument_type": "etf", "cluster_id": "Dividend Aristocrats",
         "note_ref": "", "status": "active", "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": cid, "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"}
        for cid in ("AI Infrastructure", "Momentum Stocks",
                    "Dividend Aristocrats", "Value Picks")
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    hold = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "QFUND", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0,
         "source": "issuer file, long tail dropped below the 0.05% floor",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "QFUND", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 0.3, "coverage": 1.0,
         "source": "issuer file, long tail dropped below the 0.05% floor",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "EXACTFUND", "holding": "KO", "holding_name": "COCA-COLA CO",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ], columns=market.HOLDINGS_COLUMNS).to_csv(
        hold, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl,
            "instruments_path": inst, "holdings_path": hold}


def _self_overlap_book(tmp_path):
    """One theme reaching the same company two ways (direct and a fully
    accounted fund) so its own total is substantial, and a second theme
    holding something entirely unrelated.

    Built so either of two bugs would be visible rather than accidentally
    invisible: pairing a cluster with itself (its own MSFT total is large
    enough that a stray self-row would stand out), and printing a zero row
    for a pair that genuinely shares nothing (Momentum vs Unrelated).
    """
    import csv as _csv
    from src import instruments, market

    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "MSFT", "instrument_id": "MSFT", "shares": 1,
         "cost_basis": 400, "last_price": 400, "currency": "USD",
         "market_value": 400.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "MOMFUND", "instrument_id": "MOMFUND", "shares": 1,
         "cost_basis": 300, "last_price": 300, "currency": "USD",
         "market_value": 300.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "XOM", "instrument_id": "XOM", "shares": 1,
         "cost_basis": 250, "last_price": 250, "currency": "USD",
         "market_value": 250.0, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "MSFT", "name": "Microsoft", "instrument_type": "stock",
         "cluster_id": "Momentum", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "MOMFUND", "name": "Momentum Fund",
         "instrument_type": "etf", "cluster_id": "Momentum", "note_ref": "",
         "status": "active", "added": "2026-01-01"},
        {"instrument_id": "XOM", "name": "Exxon Mobil", "instrument_type": "stock",
         "cluster_id": "Unrelated", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": cid, "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"}
        for cid in ("Momentum", "Unrelated")
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    hold = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "MOMFUND", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ], columns=market.HOLDINGS_COLUMNS).to_csv(
        hold, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl,
            "instruments_path": inst, "holdings_path": hold}


def test_two_themes_holding_the_same_company_overlap(tmp_path, monkeypatch):
    """Spec panel 6. Two themes can be one bet, and nothing in the record said
    so — a theme is why he bought something, and two whys can buy the same
    company."""
    paths = _overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    pair = out[out["cluster_a"].isin(["AI Infrastructure", "Momentum Stocks"]) &
              out["cluster_b"].isin(["AI Infrastructure", "Momentum Stocks"])]
    assert len(pair) == 1
    row = pair.iloc[0]
    assert {row["cluster_a"], row["cluster_b"]} == {"AI Infrastructure",
                                                     "Momentum Stocks"}
    assert row["shared_value"] == pytest.approx(40.0)
    assert "NVIDIA" in row["top_names"]


def test_overlap_counts_the_smaller_of_the_two_shares(tmp_path, monkeypatch):
    """If theme A holds $100 of NVDA and theme B holds $30, the money sitting
    in the same company is $30, not $130 and not $65. Summing would double
    count; averaging would be a number with no meaning."""
    paths = _overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    pair = out[out["cluster_a"].isin(["Dividend Aristocrats", "Value Picks"]) &
              out["cluster_b"].isin(["Dividend Aristocrats", "Value Picks"])]
    assert len(pair) == 1
    # $100 (Dividend Aristocrats, through EXACTFUND) and $30 (Value Picks,
    # direct) share $30 — never 130 (summed) and never 65 (averaged).
    assert pair.iloc[0]["shared_value"] == pytest.approx(30.0)


def test_a_pair_is_listed_once(tmp_path, monkeypatch):
    """(A, B) and (B, A) are one fact. Listing both doubles the panel and
    makes the ranking meaningless."""
    paths = _overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    pairs = list(zip(out["cluster_a"], out["cluster_b"]))
    seen = set()
    for a, b in pairs:
        assert (b, a) not in seen, f"{(a, b)} and its reverse both appear"
        seen.add((a, b))
    # the two independent pairs this book contains, each exactly once
    assert len(out) == 2


def test_a_theme_does_not_overlap_itself(tmp_path, monkeypatch):
    """A theme reaching one company two ways — direct and through a fully
    accounted fund — never pairs with itself, even though its own total for
    that company ($700) is large enough that a stray self-row (a bug pairing
    every cluster against the full cross product rather than combinations)
    would be impossible to miss."""
    paths = _self_overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    assert not (out["cluster_a"] == out["cluster_b"]).any()


def test_overlap_is_a_floor_when_either_side_rests_on_a_partial_fund(
        tmp_path, monkeypatch):
    """Same rule as by_company(): a total built across a fund whose holdings
    reach part of it is 'at least'."""
    paths = _overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    floor_pair = out[
        out["cluster_a"].isin(["AI Infrastructure", "Momentum Stocks"]) &
        out["cluster_b"].isin(["AI Infrastructure", "Momentum Stocks"])]
    exact_pair = out[
        out["cluster_a"].isin(["Dividend Aristocrats", "Value Picks"]) &
        out["cluster_b"].isin(["Dividend Aristocrats", "Value Picks"])]
    # AI Infrastructure's side of the NVDA pair comes through QFUND, which
    # only accounts for 80% of itself — the pair inherits that floor.
    assert bool(floor_pair.iloc[0]["is_floor"]) is True
    # Dividend Aristocrats' side of the KO pair comes through EXACTFUND,
    # which is fully accounted for — proving the flag is not just always on.
    assert bool(exact_pair.iloc[0]["is_floor"]) is False


def test_pairs_with_nothing_in_common_are_left_out(tmp_path, monkeypatch):
    """A row of zero per pair makes a panel nobody reads. The themes that
    share nothing are the normal case."""
    paths = _self_overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    # Momentum and Unrelated hold completely disjoint companies (MSFT vs
    # XOM) — the only two clusters in this book, so a pair-with-nothing-in
    # -common row would be the ENTIRE output if it were not left out.
    assert out.empty


# ─── overlap, fix round 1 (2026-10-07 review) ───

def test_pct_of_a_divides_by_the_themes_whole_money_not_just_its_companies(
        tmp_path, monkeypatch):
    """D1 (Important, fix round 1). The reviewer's own numbers: company money
    is about 100% of a technology theme's own money but 55% of Precious
    Metals', 40% of Crypto's, 37% of Agriculture's — so a company-only
    denominator makes two rows in this same column percentages of two
    different things, and the column exists to be read against itself.

    AI Infrastructure's $200 in QFUND splits into $160 of named companies
    (NVDA + MSFT) and a real $40 the fund's own holdings don't reach — not a
    company, but still part of the theme's money. The percentage must be
    read against the WHOLE $200, not the $160: $40 shared / $200 = 20.0%,
    not $40 / $160 = 25.0% (the wrong, company-only answer this test would
    have let through before fix round 1)."""
    paths = _overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    pair = out[out["cluster_a"].isin(["AI Infrastructure", "Momentum Stocks"]) &
              out["cluster_b"].isin(["AI Infrastructure", "Momentum Stocks"])]
    assert len(pair) == 1
    row = pair.iloc[0]
    ai_col = "pct_of_a" if row["cluster_a"] == "AI Infrastructure" else "pct_of_b"
    assert row[ai_col] == pytest.approx(20.0)
    assert row[ai_col] != pytest.approx(25.0)


def test_company_by_cluster_excludes_cash_and_bucket_rows(tmp_path, monkeypatch):
    """T2 (Important, fix round 1). This is the headline rule of the whole
    task — overlap is computed over `kind == "company"` rows only — and it
    shipped with nothing able to fail: the reviewer confirmed every test
    stayed green with the filter removed entirely from
    `exposure.company_by_cluster()` (src/exposure.py:478).

    It can no longer be pinned through `clusters.overlap()`'s percentages:
    D1 (above) moved `pct_of_a`/`pct_of_b` off `company_by_cluster()`'s own
    total and onto `book.holdings()`'s, which is independent of this filter
    by design. And `shared_value` itself was never reachable either — a
    cash row's key is its own instrument_id and a bucket row's key embeds
    its own fund's ticker, and `instruments.csv` gives every instrument_id
    exactly one cluster_id, so neither kind can EVER collide across two
    different clusters (see the Task 6 report's proof). So the only place
    left where this rule has an observable effect is `company_by_cluster()`
    itself, which is also where the brief and the reviewer both point.

    AI Infrastructure's QFUND is $200: $160 of named companies (NVDA+MSFT)
    and a $40 remainder its own stored holdings don't reach — real money,
    not a company. `company_by_cluster()` must report $160 for this theme,
    not $200, and must never emit a row for the bucket key at all."""
    paths = _overlap_book(tmp_path)
    cbc = exposure.company_by_cluster(**paths)
    ai = cbc[cbc["cluster_id"] == "AI Infrastructure"]
    assert ai["market_value"].sum() == pytest.approx(160.0)
    assert not any(exposure.UNACCOUNTED in k for k in ai["key"])


def _crossover_overlap_book(tmp_path):
    """Two themes whose per-company shares CROSS: Crossover A holds more of
    COMPX and less of COMPY than Crossover B does. Built so the per-company
    minimum (the correct rule) and the minimum of the two THEMES' totals (the
    wrong rule T1 found nothing could distinguish) give different answers:

        COMPX: A=$100, B=$30  -> per-company min = 30
        COMPY: A=$10,  B=$50  -> per-company min = 10
        per-company answer:     30 + 10 = 40
        min-of-totals (WRONG):  min(100+10, 30+50) = min(110, 80) = 80

    Every fixture before this one happened to be degenerate — the two rules
    agreed — so an implementation taking the minimum of the totals would
    have passed all six Task 6 tests.
    """
    import csv as _csv
    from src import instruments, market

    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "XCO", "instrument_id": "XCO", "shares": 1,
         "cost_basis": 100, "last_price": 100, "currency": "USD",
         "market_value": 100.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "YCO", "instrument_id": "YCO", "shares": 1,
         "cost_basis": 10, "last_price": 10, "currency": "USD",
         "market_value": 10.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "XYFUND", "instrument_id": "XYFUND", "shares": 1,
         "cost_basis": 80, "last_price": 80, "currency": "USD",
         "market_value": 80.0, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "XCO", "name": "Company X", "instrument_type": "stock",
         "cluster_id": "Crossover A", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "YCO", "name": "Company Y", "instrument_type": "stock",
         "cluster_id": "Crossover A", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "XYFUND", "name": "XY Fund", "instrument_type": "etf",
         "cluster_id": "Crossover B", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": cid, "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"}
        for cid in ("Crossover A", "Crossover B")
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    hold = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "XYFUND", "holding": "XCO", "holding_name": "COMPANY X",
         "weight": 0.375, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "XYFUND", "holding": "YCO", "holding_name": "COMPANY Y",
         "weight": 0.625, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ], columns=market.HOLDINGS_COLUMNS).to_csv(
        hold, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl,
            "instruments_path": inst, "holdings_path": hold}


def test_overlap_uses_the_per_company_minimum_not_the_minimum_of_totals(
        tmp_path, monkeypatch):
    """T1 (Important, fix round 1). Crossover A holds more COMPX and less
    COMPY than Crossover B — the per-company minimum (30 + 10 = 40) and the
    minimum of the two themes' totals (min(110, 80) = 80) genuinely differ,
    so this is the first fixture that can actually catch an implementation
    taking the wrong one."""
    paths = _crossover_overlap_book(tmp_path)
    out = clusters.overlap(**paths)
    assert len(out) == 1
    assert out.iloc[0]["shared_value"] == pytest.approx(40.0)
    assert out.iloc[0]["shared_value"] != pytest.approx(80.0)


def _floor_outside_shared_book(tmp_path):
    """Clean A holds a company directly AND, separately, a fund whose stored
    holdings are only half accounted for (a real floor on a DIFFERENT
    company, COMPY, that Clean B never holds at all). Clean A and Clean B's
    only shared company, COMPX, comes through funds that are fully accounted
    on both sides. Built to catch a narrowing bug: without restricting the
    floor OR to the keys actually SHARED between the two clusters, a floor
    anywhere in either cluster's own book — even on a company the other
    cluster has never heard of — would wrongly mark every pair that cluster
    is in.
    """
    import csv as _csv
    from src import instruments, market

    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "COMPX", "instrument_id": "COMPX", "shares": 1,
         "cost_basis": 50, "last_price": 50, "currency": "USD",
         "market_value": 50.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "FLOORFUND", "instrument_id": "FLOORFUND", "shares": 1,
         "cost_basis": 999, "last_price": 999, "currency": "USD",
         "market_value": 999.0, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "EXACTFUND2", "instrument_id": "EXACTFUND2", "shares": 1,
         "cost_basis": 20, "last_price": 20, "currency": "USD",
         "market_value": 20.0, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = [
        {"instrument_id": "COMPX", "name": "Comp X", "instrument_type": "stock",
         "cluster_id": "Clean A", "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"instrument_id": "FLOORFUND", "name": "Floor Fund",
         "instrument_type": "etf", "cluster_id": "Clean A", "note_ref": "",
         "status": "active", "added": "2026-01-01"},
        {"instrument_id": "EXACTFUND2", "name": "Exact Fund Two",
         "instrument_type": "etf", "cluster_id": "Clean B", "note_ref": "",
         "status": "active", "added": "2026-01-01"},
    ]
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(inst, index=False,
                                   quoting=_csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    crows = [
        {"cluster_id": cid, "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"}
        for cid in ("Clean A", "Clean B")
    ]
    cdf = pd.DataFrame(crows)
    for col in clusters.COLUMNS:
        if col not in cdf.columns:
            cdf[col] = ""
    cdf[clusters.COLUMNS].to_csv(cl, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    hold = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "FLOORFUND", "holding": "COMPY", "holding_name": "COMP Y CO",
         "weight": 0.5, "coverage": 1.0,
         "source": "issuer file, long tail dropped below the 0.05% floor",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "EXACTFUND2", "holding": "COMPX", "holding_name": "COMP X CO",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ], columns=market.HOLDINGS_COLUMNS).to_csv(
        hold, index=False, quoting=_csv.QUOTE_NONNUMERIC)

    return {"positions_path": pos, "clusters_path": cl,
            "instruments_path": inst, "holdings_path": hold}


def test_a_floor_elsewhere_in_one_theme_does_not_leak_into_an_unrelated_pair(
        tmp_path, monkeypatch):
    """M4 (Minor, fix round 1). The `.loc[shared.index]` narrowing on the
    floor OR is what keeps a floor fund's flag inside the pair it actually
    belongs to. Clean A also holds FLOORFUND (a real floor, on COMPY), but
    Clean B never holds COMPY at all — their only shared company, COMPX,
    comes through funds that are fully accounted on both sides, so the pair
    must read is_floor=False despite Clean A's own, unrelated floor."""
    paths = _floor_outside_shared_book(tmp_path)
    out = clusters.overlap(**paths)
    pair = out[out["cluster_a"].isin(["Clean A", "Clean B"]) &
              out["cluster_b"].isin(["Clean A", "Clean B"])]
    assert len(pair) == 1
    assert pair.iloc[0]["shared_value"] == pytest.approx(20.0)
    assert bool(pair.iloc[0]["is_floor"]) is False


def test_overlap_of_two_shorts_uses_the_smaller_magnitude_not_the_more_negative_value(
        monkeypatch):
    """M3 (Minor, fix round 1). A plain `min()` on two negative numbers picks
    the MORE negative one — the larger short — which overstates the overlap
    exactly the way summing would for two longs. No short is held today, and
    the instrument taxonomy for shorts/futures/options is still an open
    question (REBUILD.md §2c) — this does not answer it, it only makes sure
    the arithmetic is not wrong on the day it stops being hypothetical. Built
    directly against a synthetic `company_by_cluster()` frame rather than a
    held short position, since nothing in today's data can produce one."""
    from src import book

    cbc = pd.DataFrame([
        {"cluster_id": "Short A", "key": "SHCO", "label": "Short Co",
         "market_value": -100.0, "is_floor": False},
        {"cluster_id": "Short B", "key": "SHCO", "label": "Short Co",
         "market_value": -30.0, "is_floor": False},
    ], columns=exposure.COMPANY_BY_CLUSTER_COLUMNS)
    monkeypatch.setattr(exposure, "company_by_cluster", lambda *a, **k: cbc)
    monkeypatch.setattr(book, "holdings", lambda *a, **k: pd.DataFrame([
        {"cluster_id": "Short A", "market_value": -100.0},
        {"cluster_id": "Short B", "market_value": -30.0},
    ]))
    out = clusters.overlap()
    assert len(out) == 1
    assert out.iloc[0]["shared_value"] == pytest.approx(-30.0)


def test_a_long_and_a_short_in_the_same_name_do_not_overlap(monkeypatch):
    """M3 (Minor, fix round 1). A long and a short in one name are two
    opposing bets, not a shared one. Since this is their only common name,
    the pair must not appear at all — the same "nothing in common" rule that
    already keeps a zero row off the panel."""
    from src import book

    cbc = pd.DataFrame([
        {"cluster_id": "Long A", "key": "OPCO", "label": "Op Co",
         "market_value": 100.0, "is_floor": False},
        {"cluster_id": "Short B", "key": "OPCO", "label": "Op Co",
         "market_value": -50.0, "is_floor": False},
    ], columns=exposure.COMPANY_BY_CLUSTER_COLUMNS)
    monkeypatch.setattr(exposure, "company_by_cluster", lambda *a, **k: cbc)
    monkeypatch.setattr(book, "holdings", lambda *a, **k: pd.DataFrame([
        {"cluster_id": "Long A", "market_value": 100.0},
        {"cluster_id": "Short B", "market_value": -50.0},
    ]))
    out = clusters.overlap()
    assert out.empty
