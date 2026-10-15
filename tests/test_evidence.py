"""The evidence store — documents stored once, linked to claims many-to-many."""

from __future__ import annotations

import pytest

from src import claims


def test_evidence_kind_vocabulary_exists():
    assert claims.vocab("evidence_kind") == ["chart", "research", "article", "note"]


def test_evidence_storage_vocabulary_exists():
    assert claims.vocab("evidence_storage") == ["repo", "link", "private"]


# ─── The document table ───

import pandera.errors as pa_errors  # noqa: E402

from src import evidence  # noqa: E402


def _good_doc(**over) -> dict:
    """A valid research document. Override one field to test one rule."""
    row = {
        "kind": "research",
        "title": "Fundstrat Core Large Cap, May",
        "source": "fundstrat",
        "published": "2026-05-21",
        "storage": "private",
        "location": "2026-05-21-fundstrat-core-may.pdf",
    }
    row.update(over)
    return row


def test_add_evidence_returns_sequential_ids(tmp_path):
    path = tmp_path / "evidence.csv"
    assert evidence.add_evidence(path=path, **_good_doc()) == "e0001"
    # A different document, not the same one retitled — (storage, location) is
    # what identifies a document, so the second needs its own file.
    assert evidence.add_evidence(
        path=path,
        **_good_doc(title="Second",
                    location="2026-06-21-fundstrat-core-june.pdf")) == "e0002"


def test_missing_file_is_an_empty_store(tmp_path):
    df = evidence.load_evidence(tmp_path / "nothing.csv")
    assert df.empty
    assert list(df.columns) == evidence.EVIDENCE_COLUMNS


def test_kind_must_be_in_vocabulary(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.add_evidence(path=path, **_good_doc(kind="pdf"))


def test_source_must_be_in_origin_vocabulary(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.add_evidence(path=path, **_good_doc(source="bloomberg"))


def test_storage_must_be_in_vocabulary(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.add_evidence(path=path, **_good_doc(storage="cloud"))


def test_title_is_required(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.add_evidence(path=path, **_good_doc(title=""))


def test_impossible_published_date_is_refused(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.add_evidence(path=path, **_good_doc(published="2026-02-30"))


def test_published_may_be_blank(tmp_path):
    path = tmp_path / "evidence.csv"
    assert evidence.add_evidence(path=path, **_good_doc(published=None)) == "e0001"


# ─── The write guards: promises about the world, not about shape ───


def test_published_cannot_be_in_the_future(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="published"):
        evidence.add_evidence(path=path, **_good_doc(published="2099-01-01"))


def test_published_cannot_be_later_than_added(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="published"):
        evidence.add_evidence(path=path, added="2026-05-01",
                              **_good_doc(published="2026-05-21"))


def test_repo_storage_needs_a_real_file(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="does not point to a file"):
        evidence.add_evidence(path=path,
                              **_good_doc(storage="repo", location="notes/nope.png"))


def test_repo_storage_accepts_a_real_file(tmp_path):
    path = tmp_path / "evidence.csv"
    eid = evidence.add_evidence(
        path=path,
        **_good_doc(kind="chart", source="mine", storage="repo",
                    location="notes/screenshots/2026-08-15-IWM-price-1d.png"))
    assert eid == "e0001"


def test_repo_storage_refuses_an_absolute_path(tmp_path):
    """An absolute path is valid on one machine only."""
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="absolute"):
        evidence.add_evidence(path=path,
                              **_good_doc(storage="repo", location="/tmp/chart.png"))


def test_repo_storage_refuses_a_path_that_climbs_out_of_where_it_names(tmp_path):
    """`notes/picks/../research/x.pdf` is relative, and reaches the gitignored
    folder the paid PDFs live in. Accepted until 2026-08-17: the repo branch
    tested only `is_absolute()`, so every prefix rule in this module could be
    walked around with two dots.
    """
    with pytest.raises(ValueError, match="climbs out"):
        evidence.check_location("repo", "notes/picks/../research/2026-08-01-13d-ai.pdf",
                                require_file=False)


def test_repo_storage_still_accepts_an_ordinary_relative_path():
    """The traversal rule must not narrow what the table already stores."""
    evidence.check_location("repo", "notes/screenshots/2026-08-15-IWM-price-1d.png",
                            require_file=False)                       # no raise


def test_link_storage_must_be_a_url(tmp_path):
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="http"):
        evidence.add_evidence(path=path,
                              **_good_doc(storage="link", location="grannyshots.com"))


def test_link_storage_accepts_a_url(tmp_path):
    path = tmp_path / "evidence.csv"
    eid = evidence.add_evidence(
        path=path,
        **_good_doc(storage="link", location="https://grannyshots.com/etf/"))
    assert eid == "e0001"


def test_private_storage_refuses_a_path(tmp_path):
    """A private file is a bare filename — the module supplies notes/research/."""
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="bare filename"):
        evidence.add_evidence(
            path=path,
            **_good_doc(storage="private", location="notes/research/report.pdf"))


def test_private_storage_requires_the_naming_pattern():
    with pytest.raises(ValueError, match="YYYY-MM-DD-source-topic"):
        evidence.check_location("private", "13D_Aug2026_FINAL_v2.pdf")


def test_private_storage_requires_a_known_source_in_the_name():
    with pytest.raises(ValueError, match="ops/vocab/origin"):
        evidence.check_location("private", "2026-08-17-bloomberg-ai-capex.pdf")


def test_private_storage_refuses_an_impossible_date_in_the_name():
    with pytest.raises(ValueError, match="not.*a real calendar date"):
        evidence.check_location("private", "2026-02-31-13d-ai-capex.pdf")


def test_private_storage_accepts_a_well_formed_name():
    evidence.check_location("private", "2026-08-17-13d-ai-capex.pdf")   # no raise


def test_private_storage_accepts_a_multi_word_topic():
    evidence.check_location("private", "2026-08-17-fundstrat-first-word-may.pdf")


def test_private_storage_still_refuses_a_path():
    with pytest.raises(ValueError, match="bare filename"):
        evidence.check_location("private", "notes/research/2026-08-17-13d-ai.pdf")


def test_a_storage_with_no_location_rule_is_refused():
    """Adding a line to ops/vocab/evidence_storage is not enough.

    A new storage with no branch in check_location() would otherwise be accepted
    with no rule applied at all — which is how an absolute path to a paid PDF
    would reach a committed table.
    """
    with pytest.raises(ValueError, match="no location rule"):
        evidence.check_location("cloud", "/Users/someone/Downloads/secret.pdf")


def test_added_cannot_be_in_the_future(tmp_path):
    """`published` is compared against `added`, so a future `added` slipped both
    checks — a 2099 published date was accepted by pairing it with a 2099 added."""
    path = tmp_path / "evidence.csv"
    with pytest.raises(ValueError, match="added"):
        evidence.add_evidence(path=path, added="2099-06-01",
                              **_good_doc(published="2099-01-01"))


def test_location_is_stored_stripped(tmp_path):
    """A trailing space survives a paste and is invisible in the CSV. If the
    checked string and the stored string differ, stale_documents() reports a
    file that is really there, on every run, forever."""
    path = tmp_path / "evidence.csv"
    evidence.add_evidence(
        path=path,
        **_good_doc(kind="chart", source="mine", storage="repo",
                    location=" notes/screenshots/2026-08-15-IWM-price-1d.png "))
    assert evidence.load_evidence(path).iloc[0]["location"] == (
        "notes/screenshots/2026-08-15-IWM-price-1d.png")
    assert evidence.stale_documents(path=path).empty


# ─── One row per document: the promise the table exists for ───


def test_the_same_document_cannot_be_filed_twice(tmp_path):
    """`id` being unique is not the promise. Two ids on one file split every
    question about it: for_source() returns half the links, unlinked() calls
    the other half research that was never acted on."""
    path = tmp_path / "evidence.csv"
    evidence.add_evidence(path=path, **_good_doc())
    with pytest.raises(ValueError, match="filed more than once"):
        evidence.add_evidence(path=path, **_good_doc(title="Same file, retitled"))


def test_a_duplicate_added_by_hand_is_refused_on_read(tmp_path):
    """The guard runs on load, not only on write — so the commit gate catches a
    duplicate pasted straight into the CSV."""
    path = tmp_path / "evidence.csv"
    evidence.add_evidence(path=path, **_good_doc())
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines + [lines[-1].replace("e0001", "e0002")]) + "\n")
    with pytest.raises(ValueError, match="filed more than once"):
        evidence.load_evidence(path)


def test_check_locations_keeps_shape_rules_but_not_file_existence(tmp_path):
    """What the commit gate needs. A paid-PDF path must never reach a commit;
    a chart deleted months later must not brick every future commit."""
    path = tmp_path / "evidence.csv"
    evidence.add_evidence(
        path=path,
        **_good_doc(kind="chart", source="mine", storage="repo",
                    location="notes/screenshots/2026-08-15-IWM-price-1d.png"))
    path.write_text(path.read_text().replace(
        "notes/screenshots/2026-08-15-IWM-price-1d.png", "notes/screenshots/gone.png"))
    evidence.check_locations(path)                      # a vanished file passes
    with pytest.raises(ValueError):
        evidence.check_locations(path, require_file=True)   # and is still findable

    path.write_text(path.read_text().replace(
        '"repo","notes/screenshots/gone.png"',
        '"private","/Users/y/notes/research/paid.pdf"'))
    with pytest.raises(ValueError, match="bare filename"):
        evidence.check_locations(path)                  # the paid path never does


# ─── The link table: which source backs which thing ───


def _linked(tmp_path):
    """A document, a summary of it, and an empty link file."""
    docs, eid = _doc_for_summary(tmp_path)
    summaries = tmp_path / "evidence_summaries.csv"
    rel = _picks_file(tmp_path, "2026-08-17-13d-technology.md")
    sid = evidence.add_summary(eid, rel, path=summaries, evidence_path=docs,
                               project_root=tmp_path)
    return {"links": tmp_path / "evidence_links.csv",
            "paths": {"document": docs, "summary": summaries},
            "eid": eid, "sid": sid}


def test_link_writes_a_row(tmp_path):
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="14",
                  why="the capex case", path=f["links"], paths=f["paths"])
    df = evidence.load_links(f["links"])
    assert len(df) == 1
    assert df.iloc[0]["target_type"] == "cluster"
    assert df.iloc[0]["source_id"] == f["sid"]


def test_a_document_can_back_four_different_kinds_of_thing(tmp_path):
    """Many-to-many is the whole point: one document, many targets."""
    f = _linked(tmp_path)
    for target_type, target_id in (("cluster", "Technology"),
                                   ("cluster", "Crypto"),
                                   ("instrument", "NVDA"),
                                   ("claim", "c0003")):
        evidence.link(target_type, target_id, "document", f["eid"],
                      path=f["links"], paths=f["paths"])
    assert len(evidence.load_links(f["links"])) == 4


def test_link_refuses_a_target_that_does_not_exist(tmp_path):
    f = _linked(tmp_path)
    with pytest.raises(ValueError, match="No cluster with id 'Technolgy'"):
        evidence.link("cluster", "Technolgy", "document", f["eid"],
                      path=f["links"], paths=f["paths"])


def test_link_refuses_a_source_in_the_wrong_table(tmp_path):
    f = _linked(tmp_path)
    with pytest.raises(ValueError, match="summary ids start with 's'"):
        evidence.link("cluster", "Technology", "summary", f["eid"],
                      path=f["links"], paths=f["paths"])


def test_the_same_page_cannot_back_the_same_target_twice(tmp_path):
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="14",
                  path=f["links"], paths=f["paths"])
    with pytest.raises(ValueError, match="already links"):
        evidence.link("cluster", "Technology", "summary", f["sid"], page="14",
                      path=f["links"], paths=f["paths"])


def test_two_pages_of_one_source_may_back_one_target(tmp_path):
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="14",
                  path=f["links"], paths=f["paths"])
    evidence.link("cluster", "Technology", "summary", f["sid"], page="31",
                  path=f["links"], paths=f["paths"])
    assert len(evidence.load_links(f["links"])) == 2


@pytest.mark.parametrize("bad", ["12a", "-3", "0", "1.5", "twelve"])
def test_page_must_be_a_whole_number(tmp_path, bad):
    """The rule survived the rename, so its bad cases must survive too."""
    f = _linked(tmp_path)
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.link("cluster", "Technology", "summary", f["sid"], page=bad,
                      path=f["links"], paths=f["paths"])


def test_a_page_may_be_a_range(tmp_path):
    """A theme often spans pages — Fundstrat's IBM selloff runs 15-17, and
    13D's Optimus paragraph runs 55-56. Citing only the first page sends a
    reader to a page that does not carry the whole claim."""
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="175-176",
                  path=f["links"], paths=f["paths"])
    assert evidence.load_links(f["links"])["page"].iloc[0] == "175-176"


def test_a_range_and_its_first_page_are_different_links(tmp_path):
    """`page` is part of the key, so 175 and 175-176 are two rows. That is the
    existing behaviour for 175 and 176; a range does not change it."""
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="175",
                  path=f["links"], paths=f["paths"])
    evidence.link("cluster", "Technology", "summary", f["sid"], page="175-176",
                  path=f["links"], paths=f["paths"])
    assert len(evidence.load_links(f["links"])) == 2


def test_a_padded_page_is_stored_trimmed(tmp_path):
    """link() trims, so the file holds one canonical form. Without this,
    " 175" and "175" are two rows of the key that read identically."""
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="  175-176  ",
                  path=f["links"], paths=f["paths"])
    assert evidence.load_links(f["links"])["page"].iloc[0] == "175-176"


def test_a_hand_edited_padded_page_is_refused(tmp_path):
    """The schema itself does not tolerate whitespace — only link() trims.
    A CSV edited by hand must not be able to smuggle in a second ' 175'."""
    f = _linked(tmp_path)
    f["links"].write_text(
        '"target_type","target_id","source_type","source_id","page","why","added"\n'
        '"cluster","Technology","summary","' + f["sid"] + '"," 175","x","2026-08-21"\n'
    )
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.load_links(f["links"])


@pytest.mark.parametrize("bad", ["05", "5-07", "0012", "١٢", "１２", "¹²", "²-³"])
def test_a_page_must_be_plain_digits_in_canonical_form(tmp_path, bad):
    """`page` is part of the key, so every page needs exactly one spelling.

    "5" and "05" are the same page and would sit in the file as two rows a
    reader cannot tell apart — the same hole as " 175" and "175". Non-Latin
    digits pass str.isdigit(), so "١٢" would validate, store, and print as
    p.١٢ while never matching "12".
    """
    f = _linked(tmp_path)
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.link("cluster", "Technology", "summary", f["sid"], page=bad,
                      path=f["links"], paths=f["paths"])


@pytest.mark.parametrize(
    "bad",
    ["0-5",        # page zero does not exist
     "176-175",    # backwards
     "175-175",    # not a range — that is just page 175
     "1-2-3",      # not a range either
     "5-",         # missing end
     "12-1a"])     # the "12a" hole, reopened at the other end
def test_a_bad_range_is_refused(tmp_path, bad):
    f = _linked(tmp_path)
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.link("cluster", "Technology", "summary", f["sid"], page=bad,
                      path=f["links"], paths=f["paths"])


def test_check_links_refuses_a_hand_edited_dangling_target(tmp_path):
    f = _linked(tmp_path)
    f["links"].write_text(
        '"target_type","target_id","source_type","source_id","page","why","added"\n'
        f'"cluster","Atlantis","document","{f["eid"]}","","","2026-08-17"\n'
    )
    with pytest.raises(ValueError, match="No cluster with id 'Atlantis'"):
        evidence.check_links(f["links"], paths=f["paths"])


def test_check_links_refuses_a_hand_edited_dangling_source(tmp_path):
    """The other half of the same guard. CLAUDE.md promises the commit gate
    refuses a link to a claim *or document* that does not exist, so both loops
    need a hand-edit test — a write-time refusal does not cover a pasted row."""
    f = _linked(tmp_path)
    f["links"].write_text(
        '"target_type","target_id","source_type","source_id","page","why","added"\n'
        '"cluster","Technology","document","e0404","","","2026-08-17"\n'
    )
    with pytest.raises(ValueError, match="No document with id 'e0404'"):
        evidence.check_links(f["links"], paths=f["paths"])


def test_an_unknown_target_type_is_refused_on_read(tmp_path):
    f = _linked(tmp_path)
    f["links"].write_text(
        '"target_type","target_id","source_type","source_id","page","why","added"\n'
        f'"portfolio","all","document","{f["eid"]}","","","2026-08-17"\n'
    )
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.load_links(f["links"])


# ─── The views ───


def test_for_target_returns_the_source_behind_a_cluster(tmp_path):
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"], page="14",
                  why="the capex case", path=f["links"], paths=f["paths"])
    got = evidence.for_target("cluster", "Technology", path=f["links"],
                              paths=f["paths"])
    assert len(got) == 1
    row = got.iloc[0]
    # a summary borrows its PARENT's title, so no row is ever nameless
    assert row["title"] == "Fundstrat Core Large Cap, May"
    assert row["document_id"] == f["eid"]
    assert row["location"] == "notes/picks/2026-08-17-13d-technology.md"
    assert row["page"] == "14"


def test_for_target_carries_storage_so_screenshots_still_resolve(tmp_path):
    """holding.py filters on storage == 'repo'. Dropping the column would empty
    every claim's screenshot list without a single test failing on its own."""
    f = _linked(tmp_path)
    evidence.link("claim", "c0003", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    got = evidence.for_target("claim", "c0003", path=f["links"], paths=f["paths"])
    assert got.iloc[0]["storage"] == "private"      # from _good_doc()


def test_for_target_is_empty_for_something_with_nothing_attached(tmp_path):
    f = _linked(tmp_path)
    assert evidence.for_target("cluster", "Crypto", path=f["links"],
                               paths=f["paths"]).empty


def test_for_source_reads_links_backwards(tmp_path):
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    evidence.link("instrument", "NVDA", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    assert len(evidence.for_source("document", f["eid"], path=f["links"])) == 2


def test_unlinked_names_a_summary_nobody_attached(tmp_path):
    f = _linked(tmp_path)
    got = evidence.unlinked(path=f["links"],
                            evidence_path=f["paths"]["document"],
                            summaries_path=f["paths"]["summary"])
    assert set(got["id"]) == {f["eid"], f["sid"]}


def test_unlinked_drops_what_was_attached(tmp_path):
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "summary", f["sid"],
                  path=f["links"], paths=f["paths"])
    got = evidence.unlinked(path=f["links"],
                            evidence_path=f["paths"]["document"],
                            summaries_path=f["paths"]["summary"])
    assert set(got["id"]) == {f["eid"]}


def test_unevidenced_claims_counts_only_claim_links(tmp_path):
    """A theme-wide report is not research on one bet, and must not silence
    the gap for one."""
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    got = evidence.unevidenced_claims(path=f["links"])
    assert "c0003" in set(got["id"])       # still unevidenced


def test_inherited_counts_a_holdings_cluster_and_group(tmp_path):
    """ASML sits in AI Infrastructure, which sits in the Technology group."""
    f = _linked(tmp_path)
    evidence.link("cluster", "Technology", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    evidence.link("cluster", "AI Infrastructure", "document", f["eid"], page="9",
                  path=f["links"], paths=f["paths"])
    got = evidence.inherited("instrument", "ASML", path=f["links"])
    assert got == {("cluster", "AI Infrastructure"): 1, ("cluster", "Technology"): 1}


def test_inherited_keeps_a_cluster_and_a_sector_of_the_same_name_apart(tmp_path):
    """Seven names are BOTH a cluster id and a sector id. MRK sits in cluster
    Healthcare and in sector Healthcare, so a dict keyed by name alone
    overwrote one ancestor's count with the other's — the cluster's two
    documents disappeared from the pointer line with no error at all.
    Keyed by (target_type, target_id), the two can never collide."""
    f = _linked(tmp_path)
    evidence.link("cluster", "Healthcare", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    evidence.link("cluster", "Healthcare", "summary", f["sid"],
                  path=f["links"], paths=f["paths"])
    evidence.link("sector", "Healthcare", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    got = evidence.inherited("instrument", "MRK", path=f["links"])
    assert got == {("cluster", "Healthcare"): 2, ("sector", "Healthcare"): 1}
    # nearest first, in ancestors() order — Task 8 formats the line from this
    assert list(got) == [("cluster", "Healthcare"), ("sector", "Healthcare")]


def test_inherited_reaches_a_holding_from_the_parent_of_its_sector(tmp_path):
    """A document filed on `sector Technology` must reach ASML.

    21 of the 24 instruments that carry a sector carry a LEAF label, so while
    the walk stopped at the leaf, "a report about technology" — a legal target
    and the wording CLAUDE.md itself uses — reached nothing at all.

    Both levels are their own key: a sector is a different question from its
    parent, so the counts are pointed at, never merged.
    """
    f = _linked(tmp_path)
    evidence.link("sector", "Tech - Semiconductors", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    evidence.link("sector", "Technology", "document", f["eid"],
                  path=f["links"], paths=f["paths"])
    evidence.link("sector", "Technology", "summary", f["sid"],
                  path=f["links"], paths=f["paths"])
    got = evidence.inherited("instrument", "ASML", path=f["links"])
    assert got == {("sector", "Tech - Semiconductors"): 1,
                   ("sector", "Technology"): 2}


def _fixture_tree(tmp_path) -> dict:
    """A holding in a cluster in a group, none of which exist in the real data/.

    Its sector is a leaf under a self-parented top sector, exactly as
    data/sectors.csv is shaped, so the walk up and the self-parent guard are
    both exercised on a tree the real tables have never heard of.

    Returns the {type: path} override the whole module speaks.
    """
    from src import clusters as clusters_mod
    from src import instruments as instruments_mod
    from src import sectors as sectors_mod
    cpath = tmp_path / "clusters.csv"
    ipath = tmp_path / "instruments.csv"
    spath = tmp_path / "sectors.csv"
    clusters_mod.add_cluster("Oceans", "group", path=cpath)
    clusters_mod.add_cluster("Deep Sea Mining", "thematic", path=cpath,
                             parent="Oceans")
    sectors_mod.add_sector("Materials", "Materials", path=spath)
    sectors_mod.add_sector("Deep Sea - Nodules", "Materials", path=spath)
    instruments_mod.add_instrument("KRKN", "Kraken Minerals", "stock",
                                   path=ipath, cluster_id="Deep Sea Mining",
                                   sector="Deep Sea - Nodules")
    instruments_mod.add_instrument("MTLS", "Metals Broad", "etf",
                                   path=ipath, sector="Materials")
    return {"cluster": cpath, "instrument": ipath, "sector": spath}


def test_a_top_level_sector_is_not_its_own_ancestor_twice(tmp_path):
    """The 11 SPY sectors are self-parented rows. A naive walk lists each of
    them twice, and the pointer line would then read "2 more on the Materials
    sector, 2 more on the Materials sector"."""
    tree = _fixture_tree(tmp_path)
    assert evidence.ancestors("instrument", "MTLS", paths=tree) == [
        ("sector", "Materials")]


def test_inherited_reads_the_reference_tables_it_is_pointed_at(tmp_path):
    """The counts must come from the fixture tree, not the real portfolio.

    This test could not be written at all until ancestors() took `paths`: it
    read data/instruments.csv unconditionally, so a caller working in a fixture
    tree — holding.summary() is data_dir-parameterised — got the real
    portfolio's clusters answered back, and a fixture instrument the real file
    has never heard of crashed instead.
    """
    f = _linked(tmp_path)
    tree = _fixture_tree(tmp_path)
    everything = {**f["paths"], **tree}
    evidence.link("cluster", "Deep Sea Mining", "document", f["eid"],
                  path=f["links"], paths=everything)
    evidence.link("cluster", "Oceans", "document", f["eid"],
                  path=f["links"], paths=everything)
    evidence.link("cluster", "Oceans", "summary", f["sid"],
                  path=f["links"], paths=everything)

    got = evidence.inherited("instrument", "KRKN", path=f["links"], paths=tree)
    assert got == {("cluster", "Deep Sea Mining"): 1, ("cluster", "Oceans"): 2}
    assert list(got) == [("cluster", "Deep Sea Mining"), ("cluster", "Oceans")]

    # And the crash the override exists to prevent, stated outright: pointed at
    # the real tree, KRKN is nobody.
    with pytest.raises(KeyError, match="KRKN"):
        evidence.inherited("instrument", "KRKN", path=f["links"])


def test_ancestors_reads_the_reference_tables_it_is_pointed_at(tmp_path):
    """The same override one level down, with no link table involved at all.

    All THREE reference tables, sectors included: `Deep Sea - Nodules` is not in
    the real data/sectors.csv, so without that override the sector walk would
    answer from the real portfolio's taxonomy or find nothing at all.
    """
    tree = _fixture_tree(tmp_path)
    assert evidence.ancestors("instrument", "KRKN", paths=tree) == [
        ("cluster", "Deep Sea Mining"),
        ("sector", "Deep Sea - Nodules"),
        ("cluster", "Oceans"),
        ("sector", "Materials")]
    assert evidence.ancestors("cluster", "Deep Sea Mining", paths=tree) == [
        ("cluster", "Oceans")]


def test_ancestors_still_reads_the_real_tree_by_default():
    """`paths=None` must not change which tables the app reads.

    Nearest first, one level at a time: ASML's own cluster and sector, then what
    each of those sits inside — the AI Infrastructure cluster's group and the
    Tech - Semiconductors label's parent sector.
    """
    assert evidence.ancestors("instrument", "ASML") == [
        ("cluster", "AI Infrastructure"),
        ("sector", "Tech - Semiconductors"),
        ("cluster", "Technology"),
        ("sector", "Technology")]


def test_inherited_is_empty_when_nothing_sits_above(tmp_path):
    f = _linked(tmp_path)
    assert evidence.inherited("instrument", "ASML", path=f["links"]) == {}


def test_stale_documents_names_a_repo_file_that_vanished(tmp_path):
    """claims.stale_refs() used to cover evidence paths. That half moved here
    with the column — dropping it would have silently stopped watching."""
    epath = tmp_path / "evidence.csv"
    evidence.add_evidence(
        path=epath,
        **_good_doc(kind="chart", source="mine", storage="repo",
                    location="notes/screenshots/2026-08-15-IWM-price-1d.png"))
    got = evidence.stale_documents(path=epath)
    assert got.empty, "a file that exists is not stale"

    # Hand-edit the row to point at a file that is not there.
    text = epath.read_text().replace(
        "notes/screenshots/2026-08-15-IWM-price-1d.png", "notes/screenshots/gone.png")
    epath.write_text(text)
    got = evidence.stale_documents(path=epath)
    assert list(got["location"]) == ["notes/screenshots/gone.png"]


def test_stale_documents_ignores_links_and_private_files(tmp_path):
    """A URL has no local file, and a private PDF may be on another machine.
    Neither is 'stale' — only a committed file that vanished is."""
    epath = tmp_path / "evidence.csv"
    evidence.add_evidence(path=epath,
                          **_good_doc(storage="link",
                                      location="https://grannyshots.com/etf/"))
    evidence.add_evidence(path=epath, **_good_doc(title="A private PDF"))
    assert evidence.stale_documents(path=epath).empty


def test_holding_summary_carries_page_and_why():
    """/show must be able to print the page, not a bare path.

    Reads the real store: c0003 is IWM's finished claim, and its chart was
    migrated to e0001 on 2026-08-16.
    """
    from src import holding
    got = holding.summary("IWM", as_of="2026-08-15")
    c0003 = [c for c in got["claims"] if c["id"] == "c0003"]
    assert c0003, "c0003 should still be on IWM"
    docs = c0003[0]["evidence"]
    assert isinstance(docs, list)
    assert docs[0]["location"].endswith("IWM-price-1d.png")
    assert "page" in docs[0] and "why" in docs[0]
    assert got["screenshots"] == [docs[0]["location"]]


# ─── The summaries table ───


def _doc_for_summary(tmp_path):
    """A filed document to hang summaries off. Returns (evidence_path, eid)."""
    path = tmp_path / "evidence.csv"
    eid = evidence.add_evidence(path, **_good_doc())
    return path, eid


def _picks_file(tmp_path, name: str) -> str:
    """Write a markdown file under a tmp notes/picks/ and return its rel path."""
    md = tmp_path / "notes" / "picks" / name
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(f"# {name}\n")
    return f"notes/picks/{name}"


def test_add_summary_returns_sequential_ids(tmp_path):
    docs, eid = _doc_for_summary(tmp_path)
    summaries = tmp_path / "evidence_summaries.csv"
    first = evidence.add_summary(
        eid, _picks_file(tmp_path, "2026-08-17-13d-technology.md"),
        path=summaries, evidence_path=docs, project_root=tmp_path)
    second = evidence.add_summary(
        eid, _picks_file(tmp_path, "2026-08-17-13d-crypto.md"),
        path=summaries, evidence_path=docs, project_root=tmp_path)
    assert (first, second) == ("s0001", "s0002")


def test_a_summary_must_name_a_real_document(tmp_path):
    docs, _ = _doc_for_summary(tmp_path)
    with pytest.raises(ValueError, match="No document with id 'e0099'"):
        evidence.add_summary(
            "e0099", _picks_file(tmp_path, "x.md"),
            path=tmp_path / "s.csv", evidence_path=docs, project_root=tmp_path)


def test_a_summary_must_live_under_notes_picks(tmp_path):
    docs, eid = _doc_for_summary(tmp_path)
    md = tmp_path / "notes" / "positions" / "ASML.md"
    md.parent.mkdir(parents=True)
    md.write_text("# ASML\n")
    with pytest.raises(ValueError, match="notes/picks/"):
        evidence.add_summary(
            eid, "notes/positions/ASML.md",
            path=tmp_path / "s.csv", evidence_path=docs, project_root=tmp_path)


def test_a_summary_location_may_not_be_absolute(tmp_path):
    docs, eid = _doc_for_summary(tmp_path)
    with pytest.raises(ValueError, match="notes/picks/"):
        evidence.add_summary(
            eid, "/notes/picks/x.md",
            path=tmp_path / "s.csv", evidence_path=docs, project_root=tmp_path)


def test_a_summary_location_may_not_climb_out_of_notes_picks(tmp_path):
    """The write-time prefix rule was escapable with two dots.

    `notes/picks/../research/…` starts with `notes/picks/`, is not absolute, and
    lands in the gitignored folder the paid PDFs live in. Both directories are
    written here on purpose, so the path really does resolve to a file — what
    refuses the row is the traversal rule and not the does-it-exist one.
    """
    docs, eid = _doc_for_summary(tmp_path)
    _picks_file(tmp_path, "2026-08-17-13d-technology.md")
    pdf = tmp_path / "notes" / "research" / "2026-08-01-13d-ai-capex.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_text("a paid report\n")
    assert (tmp_path / "notes/picks/../research/2026-08-01-13d-ai-capex.pdf").is_file()
    with pytest.raises(ValueError, match="climbs out"):
        evidence.add_summary(
            eid, "notes/picks/../research/2026-08-01-13d-ai-capex.pdf",
            path=tmp_path / "s.csv", evidence_path=docs, project_root=tmp_path)


def _summaries_row(tmp_path, location: str):
    """A hand-edited summaries table carrying one location, as a commit would."""
    path = tmp_path / "evidence_summaries.csv"
    path.write_text(
        '"id","evidence_id","location","added"\n'
        f'"s0001","e0001","{location}","2026-08-17"\n'
    )
    return path


def test_check_summary_locations_refuses_a_path_into_the_paid_research_folder(tmp_path):
    """The row that actually committed cleanly before 2026-08-17.

    check_summaries() checked the foreign key and nothing else, so a location
    pointing at gitignored notes/research/ reached a committed table — the exact
    harm the storage=private bare-filename rule exists to prevent, by another
    door.
    """
    with pytest.raises(ValueError, match="notes/picks/"):
        evidence.check_summary_locations(
            _summaries_row(tmp_path, "notes/research/2026-08-01-13d-ai-capex.pdf"))


def test_check_summary_locations_refuses_an_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="notes/picks/"):
        evidence.check_summary_locations(
            _summaries_row(tmp_path, "/tmp/elsewhere/notes.md"))


def test_check_summary_locations_refuses_a_traversal(tmp_path):
    with pytest.raises(ValueError, match="climbs out"):
        evidence.check_summary_locations(
            _summaries_row(tmp_path,
                           "notes/picks/../research/2026-08-01-13d-ai-capex.pdf"))


def test_check_summary_locations_keeps_shape_rules_but_not_file_existence(tmp_path):
    """The same asymmetry check_locations() makes: shape at the gate, existence
    never — a write-up deleted months later must not brick every commit."""
    evidence.check_summary_locations(
        _summaries_row(tmp_path, "notes/picks/never-written.md"))     # no raise


def test_check_summary_locations_names_the_row_it_refused(tmp_path):
    """A message with no id in it makes the reader diff the whole table."""
    with pytest.raises(ValueError, match="s0001"):
        evidence.check_summary_locations(
            _summaries_row(tmp_path, "notes/research/2026-08-01-13d-ai-capex.pdf"))


def test_a_summary_needs_a_file_that_exists(tmp_path):
    docs, eid = _doc_for_summary(tmp_path)
    with pytest.raises(ValueError, match="does not point to a file"):
        evidence.add_summary(
            eid, "notes/picks/never-written.md",
            path=tmp_path / "s.csv", evidence_path=docs, project_root=tmp_path)


def test_two_summaries_cannot_share_one_file(tmp_path):
    """Pre-checked explicitly so the message names the file, and so the error is
    a ValueError rather than a pandera SchemaErrors (which is NOT a ValueError)."""
    docs, eid = _doc_for_summary(tmp_path)
    summaries = tmp_path / "evidence_summaries.csv"
    rel = _picks_file(tmp_path, "shared.md")
    evidence.add_summary(eid, rel, path=summaries, evidence_path=docs,
                         project_root=tmp_path)
    with pytest.raises(ValueError, match="one summary per file"):
        evidence.add_summary(eid, rel, path=summaries, evidence_path=docs,
                             project_root=tmp_path)


def test_a_duplicate_location_added_by_hand_is_refused_on_read(tmp_path):
    """A hand-edited CSV bypasses add_summary(); the schema still catches it."""
    summaries = tmp_path / "evidence_summaries.csv"
    summaries.write_text(
        '"id","evidence_id","location","added"\n'
        '"s0001","e0001","notes/picks/x.md","2026-08-17"\n'
        '"s0002","e0001","notes/picks/x.md","2026-08-17"\n'
    )
    with pytest.raises(pa_errors.SchemaErrors):
        evidence.load_summaries(summaries)


def test_check_summaries_refuses_a_dangling_parent(tmp_path):
    summaries = tmp_path / "evidence_summaries.csv"
    summaries.write_text(
        '"id","evidence_id","location","added"\n'
        '"s0001","e0099","notes/picks/x.md","2026-08-17"\n'
    )
    docs, _ = _doc_for_summary(tmp_path)
    with pytest.raises(ValueError, match="e0099"):
        evidence.check_summaries(summaries, evidence_path=docs)


def test_missing_summaries_file_is_an_empty_store(tmp_path):
    assert evidence.load_summaries(tmp_path / "nope.csv").empty


# ─── The type registry ───


def test_target_type_vocabulary_exists():
    assert claims.vocab("target_type") == [
        "claim", "cluster", "sector", "instrument", "macro",
    ]


def test_source_type_vocabulary_exists():
    assert claims.vocab("source_type") == ["document", "summary"]


def test_every_target_type_has_a_registry_entry():
    """The failure nobody can be relied on to prevent: a vocab line added with
    no lookup written for it, leaving that kind silently unchecked.

    `macro` is the one exemption and it is named here rather than waved
    through: there is no macro table to look an id up in. It is NOT
    unchecked — `check_target()` rejects every id but `macro` itself, which
    `test_macro_takes_only_the_id_macro` proves. Any OTHER new vocab line
    still has to bring a registry entry with it.
    """
    unbacked = set(claims.vocab("target_type")) - set(evidence.target_registry())
    assert unbacked == {evidence.MACRO_TARGET}


def test_every_source_type_has_a_registry_entry():
    assert set(claims.vocab("source_type")) == set(evidence.source_registry())


def test_an_unknown_target_type_is_refused():
    with pytest.raises(ValueError, match="not a known kind"):
        evidence.check_target("portfolio", "whatever")


def test_a_target_id_from_another_table_is_refused():
    """c0003 is a real id — of a claim, not a cluster."""
    with pytest.raises(ValueError, match="No cluster with id 'c0003'"):
        evidence.check_target("cluster", "c0003")


def test_a_real_cluster_is_accepted():
    evidence.check_target("cluster", "Technology")        # does not raise


def test_a_real_claim_is_accepted():
    evidence.check_target("claim", "c0003")               # does not raise


def test_a_real_instrument_is_accepted():
    evidence.check_target("instrument", "ASML")           # does not raise


def test_a_source_id_with_the_wrong_prefix_is_refused(tmp_path):
    """The polymorphic-pointer failure this design exists to prevent: a real
    id, sitting in the wrong table."""
    _, eid = _doc_for_summary(tmp_path)
    # The override has to be keyed "summary": that is the only table
    # check_source("summary", …) consults, and keying it "document" left this
    # reading the real data/evidence_summaries.csv.
    with pytest.raises(ValueError, match="summary ids start with 's'"):
        evidence.check_source("summary", eid,
                              paths={"summary": tmp_path / "no_summaries.csv"})


def test_a_blank_id_is_refused():
    with pytest.raises(ValueError, match="blank"):
        evidence.check_target("cluster", "")


# ─── Articles: a writer, a one-line gist, and a macro subject ───
#
# Added 2026-10-11 so WSJ and FT pieces can be filed. Four of the six fields
# he asked for already existed (id, published, source, title); `author` and
# `gist` did not, and macro had nowhere to attach.


def test_ft_is_an_allowed_provider():
    """FT sits beside wsj in the shared `origin` vocabulary."""
    assert "ft" in claims.vocab("origin")
    assert "wsj" in claims.vocab("origin")


def test_macro_is_an_allowed_subject():
    assert "macro" in claims.vocab("target_type")


def test_author_and_gist_are_stored_on_the_document(tmp_path):
    """Both are properties of the document, so they live beside `published`
    rather than once per link."""
    path = tmp_path / "evidence.csv"
    eid = evidence.add_evidence(
        path=path,
        kind="article", title="Why the Fed is stuck", source="wsj",
        published="2026-10-10", storage="private",
        location="2026-10-10-wsj-fed-stuck.pdf",
        author="Greg Ip", gist="Rates stay higher because the labour market will not break.",
    )
    row = evidence.load_evidence(path).set_index("id").loc[eid]
    assert row["author"] == "Greg Ip"
    assert row["gist"].startswith("Rates stay higher")


def test_author_and_gist_are_optional(tmp_path):
    """The 50 documents filed before these columns existed stay valid.

    Blank reads back as NaN, which is what `na_values=[""]` does to every
    optional column in this module — `published` included. Asserted rather
    than corrected, so the convention stays one thing.
    """
    import pandas as pd

    path = tmp_path / "evidence.csv"
    eid = evidence.add_evidence(path=path, **_good_doc())
    row = evidence.load_evidence(path).set_index("id").loc[eid]
    assert pd.isna(row["author"])
    assert pd.isna(row["gist"])


def test_articles_shows_a_missing_writer_as_blank_not_nan(tmp_path):
    """He reads this table. NaN is a pandas word, not an English one."""
    path = tmp_path / "evidence.csv"
    evidence.add_evidence(
        path=path, kind="article", title="Unsigned leader", source="ft",
        published="2026-10-09", storage="private",
        location="2026-10-09-ft-leader.pdf",
    )
    got = evidence.articles(path)
    assert got.loc[0, "author"] == ""
    assert got.loc[0, "gist"] == ""


def test_a_document_can_be_about_macro(tmp_path):
    """Macro names nothing in any table, so it takes the fixed id `macro` —
    the one target that is not looked up."""
    e_path = tmp_path / "evidence.csv"
    l_path = tmp_path / "links.csv"
    eid = evidence.add_evidence(
        path=e_path,
        kind="article", title="The dollar's quiet problem", source="ft",
        published="2026-10-09", storage="private",
        location="2026-10-09-ft-dollar.pdf",
    )
    evidence.link("macro", "macro", "document", eid, path=l_path,
                  paths={"document": e_path}, why="Rates and the dollar.")
    got = evidence.load_links(l_path)
    assert list(got["target_type"]) == ["macro"]


def test_macro_takes_only_the_id_macro(tmp_path):
    """Free text would let 'Macro', 'macro-rates' and 'MACRO' all mean the
    same thing, and the reader would never find them together."""
    e_path = tmp_path / "evidence.csv"
    l_path = tmp_path / "links.csv"
    eid = evidence.add_evidence(path=e_path, **_good_doc())
    with pytest.raises(ValueError, match="macro"):
        evidence.link("macro", "rates", "document", eid, path=l_path,
                      paths={"document": e_path})


def test_articles_lists_what_he_asked_for(tmp_path):
    """Date, provider, writer, headline, gist and id — newest first."""
    path = tmp_path / "evidence.csv"
    evidence.add_evidence(
        path=path, kind="article", title="Older piece", source="wsj",
        published="2026-10-01", storage="private",
        location="2026-10-01-wsj-older.pdf", author="A Writer", gist="One line.",
    )
    evidence.add_evidence(
        path=path, kind="article", title="Newer piece", source="ft",
        published="2026-10-09", storage="private",
        location="2026-10-09-ft-newer.pdf", author="B Writer", gist="Another line.",
    )
    # A chart is not an article and must not appear.
    evidence.add_evidence(
        path=path, kind="chart", title="SPX daily", source="mine",
        published="2026-10-09", storage="repo",
        location="notes/screenshots/2026-10-09-SPX-macro-1d.png",
    )
    got = evidence.articles(path)
    assert list(got["title"]) == ["Newer piece", "Older piece"]
    assert list(got.columns) == ["id", "published", "source", "author", "title", "gist"]


def test_check_links_accepts_a_macro_link(tmp_path):
    """The commit gate must survive a macro link.

    🚨 Regression test for the defect that nearly shipped on 2026-10-11:
    `check_links()` does NOT call `check_target()` — it reimplements the
    lookup via `_load_known()` so each table is read once per type. The macro
    exemption was added to `check_target()` only, so the gate raised
    "target_type 'macro' is not a known kind" on a link that `link()` had
    just accepted.

    That is worse than it sounds. `pre-commit:302` runs this against the
    STAGED copy, so one macro row in evidence_links.csv blocked **every
    future commit in the repo**, not just the one that added it. The whole
    suite stayed green because nothing called `check_links()` with a macro
    link — which is why this test exists.
    """
    e_path = tmp_path / "evidence.csv"
    l_path = tmp_path / "links.csv"
    eid = evidence.add_evidence(path=e_path, **_good_doc())
    evidence.link("macro", "macro", "document", eid, path=l_path,
                  paths={"document": e_path})
    evidence.check_links(l_path, paths={"document": e_path})  # must not raise


def test_check_links_still_refuses_a_bad_macro_id(tmp_path):
    """The gate is not simply skipping macro — it enforces the fixed id too,
    so a hand-edited CSV cannot smuggle in `macro`/`rates`."""
    e_path = tmp_path / "evidence.csv"
    l_path = tmp_path / "links.csv"
    eid = evidence.add_evidence(path=e_path, **_good_doc())
    l_path.write_text(
        '"target_type","target_id","source_type","source_id","page","why","added"\n'
        f'"macro","rates","document","{eid}","","hand-edited","2026-10-11"\n',
        encoding="utf-8")
    with pytest.raises(ValueError, match="takes the id 'macro' and nothing else"):
        evidence.check_links(l_path, paths={"document": e_path})
