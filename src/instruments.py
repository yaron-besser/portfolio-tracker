"""The instrument reference table — one row per real-world instrument.

Design: the plan for Phase 2c. Every other table names an instrument by its
`instrument_id`, and this file is the only place that says what that name
means. It replaces five separate alias systems that used to disagree:

    IBKR_TO_AV_TICKER      -> the av_ticker column
    _notes_file_for()      -> the note_ref column
    contains("CASH")       -> instrument_type == "cash"
    SKIP_TICKERS           -> av_ticker being blank
    the CL=F convention    -> the price_ticker column

One instrument, many broker lots: `positions.csv` still holds one row per lot
(ASML_USD, ASML_EUR) because that is what IBKR sends, but both point at ASML.
That link is why "sold the EUR lot" no longer reads as "ASML is gone".

Usage:
    from src import instruments
    instruments.resolve("ASML_EUR")        # -> "ASML"
    instruments.get("ASML")["av_ticker"]   # -> "ASML"
    instruments.add_instrument("ABCD", name="New Co", instrument_type="stock")
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import PROJECT_ROOT, vocab, _vocab_check, _is_real_date, split_paths

INSTRUMENTS_PATH = PROJECT_ROOT / "data" / "instruments.csv"

COLUMNS = [
    "instrument_id", "name", "instrument_type",
    "broker_sub_category", "broker_sub_category_as_of",
    "issuer", "sector", "cluster_id",
    "provider_sector", "provider_sector_as_of", "provider_sector_source",
    "sector_settled",
    "isin", "av_ticker", "price_ticker", "note_ref",
    "status", "added",
]

# Fund family -> the words that identify it in a fund's own name. First match
# wins, so the list is ordered: "vaneck" must be tried before "vanguard" would
# ever be reached on a name containing both, and "ss " catches State Street's
# older spellings ("SS HEALTH CARE SELECT SECTOR") that never say SPDR.
#
# Why this is a column and not a lookup table of tickers: a holdings
# downloader is written once per ISSUER, so a fund bought next year from a
# family already here needs no code at all — only this cell, which the
# refresher fills from the name the broker already gave us.
_ISSUER_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("SPDR", ("spdr", "ss ", "state street", "select sector")),
    ("iShares", ("ishares",)),
    ("Invesco", ("invesco", "powershares")),
    ("Global X", ("global x",)),
    ("Roundhill", ("roundhill",)),
    ("VanEck", ("vaneck", "van eck")),
    ("Vanguard", ("vanguard",)),
    ("WisdomTree", ("wisdomtree", "wisdom tree")),
    ("Amplify", ("amplify",)),
    ("Fundstrat", ("fundstrat",)),
    ("Schwab", ("schwab",)),
    ("First Trust", ("first trust",)),
    ("ARK", ("ark ",)),
    ("ProShares", ("proshares",)),
    ("Grayscale", ("grayscale",)),
]

# Broker lot suffixes. IBKR appends the trading currency when one symbol is
# held on two exchanges; the suffix identifies the lot, never the company.
LOT_SUFFIXES = ("_USD", "_EUR", "_JPY", "_DKK", "_GBP", "_CHF", "_HKD")

# Broker symbol -> canonical id, where the two genuinely differ. Kept tiny and
# here rather than scattered: NOV is Novo Nordisk at IBKR but is also the real
# ticker of an unrelated US company, so the collision must be resolved once.
# NOVd is the same company again — IBKR's spelling on its dividend lines.
BROKER_ALIASES = {"NOV": "NVO", "NOVd": "NVO"}


def _schema() -> pa.DataFrameSchema:
    """Built per call so vocabulary edits apply without a reload."""
    text = lambda: pa.Column(str, nullable=True)
    return pa.DataFrameSchema(
        {
            "instrument_id": pa.Column(str, unique=True),
            "name": text(),
            # Non-nullable on purpose, same as `status` and `added` below:
            # this is the field `instrument_type == "stock"` elsewhere relies
            # on (CLAUDE.md), and `fill_instrument_type()` does not exist —
            # a blank cell here is a data error, not a state to wait out. A
            # review on 2026-10-04 found this made nullable to let
            # `ops/sync_instrument_types.py` "fill a blank cell"; that made a
            # real error (a blanked-out type) load, commit and disappear from
            # every gap report silently — exactly the failure the brief's new
            # `broker_sub_category` column exists to prevent for a DIFFERENT
            # reason. Reverted: `instrument_type` is always required, broker
            # sync or not, and the "fill where blank" behaviour the brief
            # described was never reachable under the real schema, so it was
            # deleted rather than kept as dead code.
            "instrument_type": pa.Column(str, _vocab_check("instrument_type")),
            # IBKR's own finer label for the same position — ETF, ADR, REIT,
            # NY REG SHRS, COMMON, or an option's "C" — read straight off the
            # broker's assetCategory/subCategory and never typed by hand.
            # Free text, not a vocabulary: `etf` and `adr` are not
            # alternatives to each other (ASML is a STOCK that happens to be
            # an ADR), so this column exists specifically so the broad type
            # in `instrument_type` never has to carry both ideas at once.
            # Nothing filters on it yet — `ops/sync_instrument_types.py` is
            # the only writer, and it writes this unconditionally, even when
            # `instrument_type` disagrees with the broker and is left alone.
            #
            # `;`-separated, like `isin` and `claims.trade_ids` — NOT a
            # single word. One company can carry more than one of IBKR's
            # labels at once: his real statement files ASML as `COMMON` on
            # AEB (EUR) and `NY REG SHRS` on NASDAQ (USD), the same company
            # under two labels. Read it with `claims.split_paths()`, never
            # `==`, or a multi-label row silently fails any equality filter.
            "broker_sub_category": text(),
            # Dated like `provider_sector_as_of` below, for the same reason:
            # a broker label with no date is of unknown age, and this column
            # has no mechanism yet to notice a sale makes it stale (nothing
            # clears it). `set_broker_sub_category()` is the only writer and
            # refuses to fill one without the other.
            #
            # Validated here as well as in the setter, for the reason given
            # under `sector_settled` below: the setter is not the only way a
            # cell reaches the file — a hand edit or a script that writes the
            # CSV directly goes straight past it, and a date column whose job
            # is to say how old a label is fails completely if it can hold
            # the word "nope".
            "broker_sub_category_as_of": pa.Column(
                str,
                pa.Check(
                    lambda s: s.map(lambda v: v == "" or _is_real_date(v)),
                    error="broker_sub_category_as_of must be blank or a "
                          "real ISO date",
                ),
                nullable=True,
            ),
            # Who runs the fund. Free text, not a vocabulary: a new family
            # must never be able to block a trade being recorded, and the
            # only thing that reads it degrades to a worse source rather
            # than failing. Blank is normal — a single stock has no issuer.
            "issuer": text(),
            "sector": text(),
            # Foreign key into data/clusters.csv. The value is the
            # cluster's readable name, which is also its key.
            "cluster_id": text(),
            "provider_sector": text(),
            # Dated and attributed deliberately as two separate nullable
            # cells, not one: a sector with no date is of unknown age, and
            # one with no source cannot be weighed against his own label.
            # set_provider_sector() is the only writer, and it refuses to
            # fill one without the other two (P2).
            #
            # Checked here as well, for the reason under `sector_settled`
            # below: the setter is not the only route into the file, and
            # this cell does more than record an age — a filled date is
            # what puts the row inside the `data/sectors.csv` key the
            # commit guard applies (`dated_provider_sectors()`), so a cell
            # holding "last tuesday" would both lie about the age and drag
            # a raw vendor word into a check it was never meant for.
            "provider_sector_as_of": pa.Column(
                str,
                pa.Check(
                    lambda s: s.map(lambda v: v == "" or _is_real_date(v)),
                    error="provider_sector_as_of must be blank or a real "
                          "ISO date",
                ),
                nullable=True,
            ),
            "provider_sector_source": text(),
            # Filled once, by hand, when Yaron has looked at a `sector` vs
            # `provider_sector` disagreement ops/data_gaps.py surfaced and
            # decided his own label stands. Permanent, not conditional —
            # ops/data_gaps.py skips the disagreement check for good once
            # this is filled, even if the vendor's classification later
            # changes (Yaron, 2026-10-04, commentary.csv cm0017, re: BMNR).
            #
            # UNLIKE `provider_sector_as_of`, this IS validated here, not
            # only by `set_sector_settled()` (review, Task 6 fix round 2):
            # every other free-text date cell is at worst stale if a hand
            # edit slips past the setter, but this one's whole job is to
            # make a check stop firing forever, and a cell that silences
            # something should not be settable to a word. `pa.Check`'s
            # `ignore_na=True` default skips the (very common) blank rows,
            # so this does not fight `nullable=True` below.
            "sector_settled": pa.Column(
                str,
                pa.Check(
                    lambda s: s.map(lambda v: v == "" or _is_real_date(v)),
                    error="sector_settled must be blank or a real ISO date",
                ),
                nullable=True,
            ),
            "isin": text(),
            "av_ticker": text(),
            "price_ticker": text(),
            "note_ref": text(),
            "status": pa.Column(str, _vocab_check("instrument_status")),
            "added": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="added must be a real calendar date, ISO YYYY-MM-DD"),
            ),
        },
        strict=True,
        ordered=True,
    )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Validate the reference table. Raises naming every failing row (P2)."""
    return _schema().validate(df, lazy=True)


def load(path: Path = INSTRUMENTS_PATH) -> pd.DataFrame:
    """Read and validate. A missing/empty file is an empty table, not an error."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    return validate(df) if not df.empty else pd.DataFrame(columns=COLUMNS)


def resolve(broker_ticker: str) -> str:
    """Broker lot identifier -> canonical instrument_id.

    Pure string rules, no file read: cash keeps its own id, a currency suffix
    is stripped, spaces are removed ('BRK B' -> 'BRKB'), then the alias map
    resolves the one genuine collision.

    A missing ticker raises rather than resolving. `str(None)` is `'None'` and
    a blank cell read by pandas is `nan` -> `'nan'`; either would sail through
    as a plausible-looking id and only surface later as "unknown instrument
    'nan'", pointing at the lookup instead of at the empty cell that caused it.
    """
    if broker_ticker is None or (
        isinstance(broker_ticker, float) and pd.isna(broker_ticker)
    ):
        raise ValueError("resolve() got an empty ticker — the source cell is blank.")
    t = str(broker_ticker).strip()
    if not t:
        raise ValueError("resolve() got an empty ticker — the source cell is blank.")
    if t.endswith("_CASH"):
        return t
    for suffix in LOT_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
            break
    t = t.replace(" ", "")
    return BROKER_ALIASES.get(t, t)


def issuer_from_name(name) -> str:
    """Guess the fund family from the fund's own name. "" when nothing matches.

    Pure string rules, no file read, and deliberately a GUESS that is written
    down rather than re-derived on every run: the cell it fills can be
    corrected by hand and nothing here will overwrite it. A wrong issuer costs
    one fund a better source for one run; a missing one costs the same. Either
    way the fund falls back to the floor and the gap report names it, so this
    never needs to be right to stay safe.
    """
    if not isinstance(name, str):
        return ""
    text = name.strip().lower()
    if not text:
        return ""
    for issuer, words in _ISSUER_WORDS:
        # On a word boundary, never a bare substring. "ss " as a substring
        # makes "Express Inc" a State Street fund, and "ark " makes "Clark"
        # an ARK one — a wrong issuer is worse than none, because the right
        # answer to "who runs this" is usually "nobody, it is a company".
        if any(text.startswith(w) or f" {w}" in text for w in words):
            return issuer
    return ""


def set_issuer(instrument_id: str, issuer: str,
               path: Path = INSTRUMENTS_PATH) -> None:
    """Record (or correct) who runs a fund.

    Separate from `add_instrument()` for the same reason `set_cluster()` and
    `set_note_ref()` are: the alternative is hand-editing a file the commit
    guard watches, and a typo then sits in the tree until a commit is refused.

    Blank is allowed here, unlike `set_cluster()` — clearing an issuer loses
    nothing but a shortcut to a better data source, where clearing a cluster
    silently drops the holding out of every cluster total.
    """
    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='etf')"
        )
    # None/NaN stringify to 'None'/'nan', which read as real issuers — the
    # same trap set_cluster(), set_status() and set_provider_sector() guard.
    if issuer is None or (not isinstance(issuer, str) and pd.isna(issuer)):
        text = ""
    else:
        text = str(issuer).strip()

    candidate = df.copy()
    candidate.loc[candidate["instrument_id"] == instrument_id, "issuer"] = text
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def set_broker_sub_category(instrument_id: str, broker_sub_category: str,
                            as_of: str, path: Path = INSTRUMENTS_PATH) -> None:
    """Record IBKR's own finer label for a position — ETF, ADR, REIT,
    NY REG SHRS, COMMON, or an option's "C" — dated.

    Written by `ops/sync_instrument_types.py` for every instrument the latest
    statement covers, unconditionally — including one whose hand-typed
    `instrument_type` disagrees with the broker, because nothing filters on
    this column yet and overwriting it carries none of the risk that
    silently correcting `instrument_type` would.

    The two move together or not at all, the same pairing
    `set_provider_sector()` enforces for its three: a label with no date is
    of unknown age, and this column has no mechanism yet to notice a sale
    makes it stale. Blank/blank is allowed, to clear a value that no longer
    applies — the same allowance `set_issuer()` makes.
    """
    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='stock')"
        )

    def _text(value) -> str:
        # None/NaN stringify to 'None'/'nan', which read as real values —
        # the same trap set_issuer(), set_cluster() and set_status() guard.
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ""
        return str(value).strip()

    sub_t, as_of_t = _text(broker_sub_category), _text(as_of)
    if bool(sub_t) != bool(as_of_t):
        raise ValueError(
            f"set_broker_sub_category({instrument_id!r}, "
            f"broker_sub_category={sub_t!r}, as_of={as_of_t!r}) — these two "
            f"move together or not at all. A label with no date is of "
            f"unknown age. Pass both, or neither to clear the cell."
        )
    if as_of_t and not _is_real_date(as_of_t):
        raise ValueError(
            f"as_of {as_of_t!r} is not a real calendar date, ISO YYYY-MM-DD."
        )

    candidate = df.copy()
    mask = candidate["instrument_id"] == instrument_id
    candidate.loc[mask, "broker_sub_category"] = sub_t
    candidate.loc[mask, "broker_sub_category_as_of"] = as_of_t
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def get(instrument_id: str, path: Path = INSTRUMENTS_PATH) -> dict:
    """One instrument's row as a dict. Raises if unknown, and the message
    carries the exact command to add it (P2 — a rejection teaches the fix)."""
    df = load(path)
    row = df[df["instrument_id"] == instrument_id]
    if row.empty:
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument("
            f"{instrument_id!r}, name='...', instrument_type='stock')\n"
            f"  Allowed types: {vocab('instrument_type')}"
        )
    return row.iloc[0].where(row.iloc[0].notna(), "").to_dict()


def add_instrument(
    instrument_id: str,
    name: str,
    instrument_type: str,
    path: Path = INSTRUMENTS_PATH,
    **optional,
) -> str:
    """Add a new instrument with the minimum needed to record a trade tonight.

    Only id, name and type are required. Sector, ISIN, cluster and note may be
    filled in later; the row is marked `incomplete` and surfaces in gaps()
    until they are. This is deliberate (plan §2): a hard requirement means that
    on a night the paperwork cannot be finished, the *trade* goes unrecorded —
    losing the decision to protect the metadata. The constraint blocks
    mislabelling, never writing.
    """
    df = load(path)
    if (df["instrument_id"] == instrument_id).any():
        raise ValueError(f"Instrument {instrument_id!r} already exists.")
    unknown = set(optional) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Unknown field(s) {sorted(unknown)}. Columns are: {COLUMNS}")

    row = {c: optional.get(c, "") for c in COLUMNS}
    row.update(
        instrument_id=instrument_id,
        name=name,
        instrument_type=instrument_type,
        status=optional.get("status", "incomplete"),
        added=optional.get("added", date.today().isoformat()),
    )
    _check_note_ref(row["note_ref"])

    candidate = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    validate(candidate)
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return instrument_id


def _check_note_ref(cell) -> None:
    """A note_ref must point at a file that exists.

    Shared by add_instrument() and set_note_ref(), so the two cannot disagree
    about what a valid link is.
    """
    for p in split_paths(cell):
        if not (PROJECT_ROOT / p).is_file():
            raise ValueError(
                f"note_ref path {p!r} does not point to a file that exists."
            )


def set_cluster(instrument_id: str, cluster_id: str,
                path: Path = INSTRUMENTS_PATH,
                clusters_path: Path | None = None) -> None:
    """Move a holding to a different theme.

    `add_instrument()` can set this at creation; for a row that already exists
    there was no writer, so the only route was hand-editing a file the commit
    guard watches — and the guard checks `cluster_id` against `clusters.csv`
    only at COMMIT, so a typo sat in the tree until he tried to save. Checking
    here makes it refuse immediately.

    Written for the 2026-09-28 rename of `Metals` to `Precious Metals`: the
    cluster held only gold and silver while copper sat in Critical Minerals,
    so the contents were already right and only the name invited confusion.
    """
    from src import clusters as clusters_mod

    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='stock')"
        )
    # pd.isna() before any truth test: `cluster_id or ""` raises
    # "boolean value of NA is ambiguous" on pd.NA, and stringifies float NaN
    # into the literal "nan", which then fails the FK naming the wrong problem.
    if cluster_id is None or (not isinstance(cluster_id, str) and pd.isna(cluster_id)):
        text = ""
    else:
        text = str(cluster_id).strip()
    if not text:
        # A blank leaves the holding in NAV but in NO cluster row, so
        # clusters.weights() silently stops adding up: blanking PHO drops
        # $1,322.60 out of every cluster total while positions.csv still
        # carries it. add_instrument() may leave it blank because that row is
        # stamped `incomplete` and shows in gaps(); moving one to nowhere has
        # no such marker. Found by review 2026-09-28.
        raise ValueError(
            f"set_cluster({instrument_id!r}, '') would leave it in no cluster "
            f"at all, and clusters.weights() would stop summing to NAV with "
            f"nothing saying so. Name the cluster it moves TO."
        )
    # The FK the commit guard would otherwise catch hours later.
    if clusters_path is not None:
        clusters_mod.check_known([text], clusters_path)
    else:
        clusters_mod.check_known([text])
    row = (clusters_mod.get(text, clusters_path) if clusters_path is not None
           else clusters_mod.get(text))
    if str(row.get("layer")) == "group":
        raise ValueError(
            f"{text!r} is a group, and a group holds no positions of its "
            f"own — name one of the clusters inside it instead."
        )
    if str(row.get("status")) == "retired":
        # Otherwise a rename can be silently undone by filing back into the
        # slot that was just abolished.
        raise ValueError(
            f"{text!r} is retired. Filing into it would quietly bring back a "
            f"theme that was closed — revive it with "
            f"clusters.set_status({text!r}, 'active') if that is what you mean."
        )
    candidate = df.copy()
    candidate.loc[candidate["instrument_id"] == instrument_id, "cluster_id"] = text
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def set_provider_sector(instrument_id: str, sector: str, source: str,
                        as_of: str, path: Path = INSTRUMENTS_PATH) -> None:
    """Record a vendor's sector for an instrument, with who said it and when.

    Three columns move together or not at all: a sector with no date is of
    unknown age, and one with no source cannot be weighed against his own
    label. Writing any of them alone is how a stale value starts looking fresh.

    `sector` still wins everywhere today (CLAUDE.md) — this is a second,
    attributed opinion alongside it, for the page's sector view to use later.
    Written by `ops/refresh_classification.py`; not an instrument FK or a
    `data/sectors.csv` FK, because the column has carried an older vendor's
    raw vocabulary ("FINANCIAL SERVICES") since before that mapping existed,
    and this setter must not refuse to date what is already there.
    """
    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='stock')"
        )

    def _text(value) -> str:
        # None/NaN stringify to 'None'/'nan', which read as real values —
        # the same trap `set_cluster()` and `set_status()` guard against.
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ""
        return str(value).strip()

    sector_t, source_t, as_of_t = _text(sector), _text(source), _text(as_of)
    filled = (bool(sector_t), bool(source_t), bool(as_of_t))
    if any(filled) and not all(filled):
        raise ValueError(
            f"set_provider_sector({instrument_id!r}, sector={sector_t!r}, "
            f"source={source_t!r}, as_of={as_of_t!r}) — these three move "
            f"together or not at all. A sector with no date is of unknown "
            f"age; one with no source cannot be weighed against his own "
            f"label. Pass all three, or none to clear the row."
        )
    if as_of_t and not _is_real_date(as_of_t):
        raise ValueError(
            f"as_of {as_of_t!r} is not a real calendar date, ISO YYYY-MM-DD."
        )

    candidate = df.copy()
    mask = candidate["instrument_id"] == instrument_id
    candidate.loc[mask, "provider_sector"] = sector_t
    candidate.loc[mask, "provider_sector_as_of"] = as_of_t
    candidate.loc[mask, "provider_sector_source"] = source_t
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def set_sector_settled(instrument_id: str, settled_on: str,
                       path: Path = INSTRUMENTS_PATH) -> None:
    """Record that he has looked at a `sector` vs `provider_sector`
    disagreement and decided his own label stands.

    Written for BMNR, 2026-10-04 (`commentary.csv` cm0017): Yahoo files it as
    Financials because its balance sheet holds crypto; he files it as crypto
    because holding crypto is what the business does. His own words: "the
    vendor is not right... most of the time they are — this is really
    specific." He chose case-by-case over any standing rule, and he chose
    permanent over conditional — once this is filled, `ops/data_gaps.py`
    skips the disagreement check for this instrument for good, even if the
    vendor's classification changes later. Re-opening it is still possible:
    clear the cell by hand and the next report will compare again.

    Blank is allowed, to clear a settlement made in error — the same
    allowance `set_issuer()` makes and `set_cluster()` deliberately does not,
    because an unsettled disagreement just resumes showing in the report,
    where a cluster-less holding would drop out of every total with nothing
    saying so.
    """
    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='stock')"
        )
    # None/NaN stringify to 'None'/'nan' — the same trap set_issuer(),
    # set_cluster() and set_status() guard against.
    if settled_on is None or (not isinstance(settled_on, str) and pd.isna(settled_on)):
        text = ""
    else:
        text = str(settled_on).strip()
    if text and not _is_real_date(text):
        raise ValueError(
            f"sector_settled {text!r} is not a real calendar date, ISO YYYY-MM-DD."
        )

    candidate = df.copy()
    candidate.loc[candidate["instrument_id"] == instrument_id, "sector_settled"] = text
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def set_note_ref(instrument_id: str, note_ref: str,
                 path: Path = INSTRUMENTS_PATH) -> None:
    """Point an instrument at its note.

    `add_instrument()` can set it at creation; for a row that already exists
    there was no writer at all, so the only route was hand-editing a file the
    commit guard watches. `notes.status()` and `holding.summary()` read note
    state from THIS column, not from the file being on disk, so a note written
    without it reports as "missing" for ever.

    Validates the whole table before writing, so a refused link leaves the file
    exactly as it was.
    """
    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='stock')"
        )
    _check_note_ref(note_ref)
    candidate = df.copy()
    candidate.loc[candidate["instrument_id"] == instrument_id, "note_ref"] = note_ref
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def set_status(instrument_id: str, status: str,
               path: Path = INSTRUMENTS_PATH,
               positions_path: Path | None = None) -> None:
    """Close a position, or bring one back onto the book.

    `clusters.set_status()` existed and this did not, so marking RSP closed on
    2026-09-28 meant hand-editing a file the commit guard watches — the same
    hole `set_cluster()` and `set_note_ref()` were written to fill.

    The guard is the reason it is worth a function. `gaps()` skips a row that
    says `closed`, so marking one closed while its lots are still in
    `positions.csv` retires it from the incomplete list while the money is
    still there — and nothing else would say so. (`notes.status()` is NOT
    affected: it builds its holding rows from `positions.csv` and never reads
    this column. An earlier draft of this docstring claimed otherwise.)

    `benchmark` (added Task 7, for SPY) gets the identical guard: `gaps()`
    skips it exactly like `closed`, so marking a real holding a benchmark has
    the same silent-retirement effect — found by review, where
    `set_status('ANET', 'benchmark')` was accepted while
    `set_status('ANET', 'closed')` correctly refused.

    Vocabulary lives in `ops/vocab/instrument_status`, so adding a value is an
    appended line rather than a code change.
    """
    from src import levels as levels_mod

    df = load(path)
    if instrument_id not in set(df["instrument_id"]):
        raise KeyError(
            f"Unknown instrument {instrument_id!r}.\n"
            f"  Add it with:  instruments.add_instrument({instrument_id!r}, "
            f"name='...', instrument_type='stock')"
        )
    # pd.isna() before any truth test — `status or ""` raises on pd.NA and
    # stringifies float NaN into the literal "nan". Third time this bit
    # (commentary._blank, claims._normalise_decision, set_cluster).
    if status is None or (not isinstance(status, str) and pd.isna(status)):
        text = ""
    else:
        text = str(status).strip()

    if text in ("closed", "benchmark"):
        # Summed across every broker lot on purpose: ASML_EUR going to zero is
        # not ASML closing, and reading one lot would let a half-closed
        # position be marked gone. Only the two statuses `gaps()` treats as
        # "stop asking" are guarded — reopening is always safe.
        pos_path = (Path(positions_path) if positions_path is not None
                    else PROJECT_ROOT / "data" / "positions.csv")
        # `levels.shares_held()` answers 0.0 for a file it cannot read — absent,
        # zero-byte, or missing any of instrument_id/shares/last_price
        # (levels._read_positions). Writing that silence as "nothing held" is
        # how a live position gets marked gone by a truncated sync. Refuse
        # instead, the way claims.position_pct_of_nav() refuses rather than
        # size against a short denominator (CLAUDE.md). Found by review
        # 2026-09-28.
        if not pos_path.exists() or pos_path.stat().st_size == 0:
            raise ValueError(
                f"Cannot confirm {instrument_id!r} holds nothing: "
                f"{pos_path} is missing or empty. Re-sync the broker first — "
                f"an unreadable positions file reads as zero shares held."
            )
        have = set(pd.read_csv(pos_path, nrows=0).columns)
        absent = {"instrument_id", "shares", "last_price"} - have
        if absent:
            raise ValueError(
                f"Cannot confirm {instrument_id!r} holds nothing: "
                f"{pos_path} has no {sorted(absent)} column, so every holding "
                f"in it reads as zero shares. Re-sync the broker first."
            )
        held = levels_mod.shares_held(instrument_id, pos_path)
        if held:
            raise ValueError(
                f"{instrument_id!r} still holds {held:g} shares. Marking it "
                f"{text!r} would retire it from instruments.gaps() while "
                f"positions.csv still carries the money. Sell the rest "
                f"first, re-sync the broker, or pick a status "
                f"instruments.gaps() does not skip."
            )

    candidate = df.copy()
    candidate.loc[candidate["instrument_id"] == instrument_id, "status"] = text
    validate(candidate)                    # vocabulary, before anything lands
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def check_known(ids, path: Path = INSTRUMENTS_PATH) -> None:
    """Raise unless every id exists — the foreign key other tables call."""
    known = set(load(path)["instrument_id"])
    # Blank means "not set", which is legal wherever this key is optional.
    # `clusters.check_known` and `sectors.check_known` have always skipped
    # blanks; this one did not, so one empty cell failed three different ways
    # depending on which table was being validated. Found by audit 2026-09-29.
    def _set(i) -> bool:
        # None and NaN stringify to "None"/"nan", which are not blank — so a
        # naive str().strip() test reports a missing value as a missing
        # INSTRUMENT, and the error tells him to go add a ticker called None.
        return i is not None and not pd.isna(i) and str(i).strip() != ""

    missing = sorted({str(i).strip() for i in ids
                      if _set(i) and str(i).strip() not in known})
    if missing:
        raise ValueError(
            f"Unknown instrument(s) {missing} — not in data/instruments.csv.\n"
            f"  Add one with:  instruments.add_instrument('{missing[0]}', "
            f"name='...', instrument_type='stock')"
        )


def dated_provider_sectors(df: pd.DataFrame) -> list[str]:
    """The `provider_sector` labels that must exist in `data/sectors.csv`.

    This column holds two vocabularies, which is why the key is conditional
    rather than applied to the whole column:

      * a row WITH a `provider_sector_as_of` was written by
        `set_provider_sector()`, which maps the vendor's answer onto his own
        taxonomy. "Technology", "Financials" — all real `sectors.csv` labels.
      * a row WITHOUT one predates that mapping and holds the vendor's own
        raw word: "FINANCIAL SERVICES", "REAL ESTATE". Every closed position
        is in this group, and none of those words is in `sectors.csv`.

    So the date is what says which vocabulary a cell is speaking, and only
    the first group can be key-checked. Keying the whole column would refuse
    his real table; keying none of it is what let `provider_sector` be
    load-bearing (spec D13 makes it the page's sector authority) and
    enforced nowhere, so "Blockchain Widgets" committed cleanly.

    Blank dates read back from CSV as NaN, not "", so both spellings of
    empty have to count as undated.
    """
    if df.empty:
        return []
    dated = df["provider_sector_as_of"].map(
        lambda v: v is not None and not pd.isna(v) and str(v).strip() != "")
    return [str(v).strip() for v in df.loc[dated, "provider_sector"]
            if v is not None and not pd.isna(v) and str(v).strip() != ""]


def gaps(path: Path = INSTRUMENTS_PATH) -> pd.DataFrame:
    """Everything incomplete, in one place. Visible always, blocking never.

    ⚠️ Only things that are actually incomplete. An ETF with no sector is
    CORRECT — its exposure is decomposed in etf_compositions.csv instead —
    and a cash or FX balance has no thesis to write. Until 2026-09-29 both
    were listed here with "(expected for an ETF)" appended, which put 36
    permanently unclearable rows in a 47-row list. A queue that cannot reach
    zero stops being read, and then the real gaps in it go unread too.

    A `benchmark` row (SPY, added Task 7) is skipped the same way `closed`
    is: it is never held and never will be, so "no thesis note" would sit in
    this list forever for a fund he did not buy and has no view on.
    """
    df = load(path)
    out = []
    for _, r in df.fillna("").iterrows():
        if r["status"] in ("closed", "benchmark"):
            continue
        why = []
        if r["status"] == "incomplete":
            why.append("never completed after being added")
        if not r["sector"] and r["instrument_type"] not in ("etf", "cash", "fx"):
            why.append("no sector")
        # Cash and FX have no thesis to write — holding.unexplained() already
        # excludes them for the same reason.
        if not str(r["note_ref"]).strip():
            if r["instrument_type"] not in ("cash", "fx"):
                why.append("no thesis note")
        elif not (PROJECT_ROOT / str(r["note_ref"]).strip()).is_file():
            why.append(f"note_ref points at a missing file ({r['note_ref']})")
        if not r["isin"] and r["instrument_type"] == "stock":
            why.append("no ISIN")
        if why:
            out.append({"instrument_id": r["instrument_id"],
                        "type": r["instrument_type"], "missing": "; ".join(why)})
    return pd.DataFrame(out, columns=["instrument_id", "type", "missing"])


def stale_refs(path: Path = INSTRUMENTS_PATH) -> pd.DataFrame:
    """Rows whose `note_ref` points at a file that no longer exists.

    A view, never a blocking check — the same decision `claims.stale_refs()`
    makes. Archiving a note is a normal act; if path existence were enforced
    on every load, moving one closed position's note would brick the whole
    table. Nine notes moved to `_archive/` in Phase 2f and broke five links,
    which is exactly why this needs to be visible somewhere.
    """
    df = load(path).fillna("")
    out = [
        {"instrument_id": r["instrument_id"], "note_ref": ref}
        for _, r in df.iterrows()
        if (ref := str(r["note_ref"]).strip())
        and not (PROJECT_ROOT / ref).is_file()
    ]
    return pd.DataFrame(out, columns=["instrument_id", "note_ref"])


def price_age_days(path: Path = PROJECT_ROOT / "data" / "positions.csv") -> int | None:
    """How old the prices in positions.csv are, in days. None if never stamped.

    IBKR is the only price source in this project, which is fine — the danger
    is not the single source, it is the source going quiet without anyone
    noticing. Callers that show a price should call this and say so out loud
    rather than returning a confident wrong number (P2).
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if "priced_at" not in df.columns:
        return None
    stamped = df["priced_at"].dropna().astype(str)
    stamped = stamped[stamped.str.len() == 10]
    if stamped.empty:
        return None
    # The OLDEST stamp, not the freshest. `sync_positions` leaves an
    # ambiguous ticker untouched, so one lot can fall weeks behind while
    # the rest refresh — and `max()` reported the book as current while a
    # stop was being compared against a stale price.
    return (date.today() - date.fromisoformat(stamped.min())).days


def price_warning(max_age_days: int = 1, **kw) -> str:
    """A sentence to show the user, or "" when the prices are fresh."""
    age = price_age_days(**kw)
    if age is None:
        return ("Prices in positions.csv carry no date — they may be from any time. "
                "Refresh with: venv/bin/python -m src.ibkr_sync")
    if age > max_age_days:
        return (f"Prices are {age} days old. "
                f"Refresh with: venv/bin/python -m src.ibkr_sync")
    return ""
