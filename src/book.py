"""The book, and the three nested bases every percentage on the page is of.

Why this exists. `exposure.by_sector()` and `clusters.weights()` each read
`positions.csv` and summed their own NAV, and the page would have been a third
place. One definition of the number every panel divides by (P7) — otherwise the
page and the chat can disagree about how big his book is, which is the one
disagreement neither of them could win.

The three bases are **nested** (spec D6): each strips one more thing, so the
control is one setting with three positions rather than two switches and four
states. His own data draws both lines and neither is invented here:

    everything    every lot, cash included
    minus-cash    less cash and FX balances          (instrument_type)
    pure-equity   less what is not in the equity book (clusters.in_equity_base)

`pure-equity` is his phrase for the third one and it is not literally only
equities — crypto, bullion and the commodity funds stay in. `words()` is what
the page prints, and it names what is actually excluded rather than repeating
the label.

**Size and value are different questions.** A short carries a negative
`market_value`: it is drawn below the line and it is ranked by `size`, which is
magnitude. `claims.position_pct_of_nav()` made the same split for the same
reason — direction is already recorded in `intent`, and repeating it as a minus
sign in the ranking key makes the ranking unusable.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.claims import PROJECT_ROOT

POSITIONS_PATH = PROJECT_ROOT / "data" / "positions.csv"
TRANSACTIONS_PATH = PROJECT_ROOT / "data" / "transactions.csv"
CORPORATE_ACTIONS_PATH = PROJECT_ROOT / "data" / "corporate_actions.csv"
CASH_TRANSACTIONS_PATH = PROJECT_ROOT / "data" / "cash_transactions.csv"
BORROW_FEES_PATH = PROJECT_ROOT / "data" / "borrow_fees.csv"

BASES = ("everything", "minus-cash", "pure-equity")

#: Lot kinds that are a cash or FX balance rather than a position.
CASH_KINDS = {"cash", "fx"}

LOT_COLUMNS = ["ticker", "instrument_id", "market_value", "currency",
               "instrument_type", "sector", "provider_sector",
               "provider_sector_as_of", "cluster_id", "in_equity_base",
               "priced_at"]

HOLDING_COLUMNS = ["instrument_id", "market_value", "size", "instrument_type",
                   "sector", "provider_sector", "provider_sector_as_of",
                   "cluster_id"]

# Instrument-side fields that only LABEL a lot, read off the instruments row
# beyond `cluster_id` itself. A real `instruments.csv` row always has every
# one of these; a caller's own tiny store — `clusters.weights()`'s existing
# callers, which mock `instruments.load()` with only
# `instrument_id`/`cluster_id` — may not. Filled blank rather than raised:
# this is about not crashing on a narrower input, never about trusting a
# narrow one more than a full one. `instrument_type` is NOT in this list —
# it decides which basis a lot belongs to, not just how it is labelled, so a
# frame missing it is refused below rather than silently treating every lot
# in it as a non-cash position.
_INSTRUMENT_LABEL_FIELDS = ("sector", "provider_sector",
                           "provider_sector_as_of", "cluster_id")

_WORDS = {
    "everything": "a share of everything you hold, cash included",
    "minus-cash": "a share of everything except cash and FX balances",
    "pure-equity": ("a share of the equity book — cash, FX balances and "
                    "T-bills left out"),
}


def check_basis(basis: str) -> str:
    """Refuse an unknown basis by name, and say what the three are (P2)."""
    if basis not in BASES:
        raise ValueError(
            f"{basis!r} is not one of the three settings.\n"
            f"  They are: {', '.join(BASES)}.\n"
            f"  See spec D6 — they are nested, each stripping one more thing."
        )
    return basis


def words(basis: str) -> str:
    """The plain-English sentence the page prints under the control.

    Never the key. He is not a developer and `pure-equity` is a dict key, not
    a sentence — and the label alone would overstate the third setting, which
    keeps crypto, bullion and commodity funds.
    """
    return _WORDS[check_basis(basis)]


def lots(positions_path: Path | None = None,
         instruments_path: Path | None = None,
         clusters_path: Path | None = None) -> pd.DataFrame:
    """One row per broker lot, with everything a basis test needs to decide.

    Lots, not instruments: `currency` is a property of the lot — ASML_USD and
    ASML_EUR are two rows of one instrument — and collapsing first would file
    a euro lot under whatever currency the instrument's first lot happened to
    carry.

    A lot whose `instrument_id` is unknown is LEFT OUT and reported by
    `unclassified()`. Dropping it silently removes it from the numerator and
    the denominator, so every other holding in the book is written up.

    Requires the instruments frame to carry `instrument_type` — refuses
    rather than guessing, because it is what `in_basis()` uses to tell a cash
    balance from a position. A missing `sector`/`provider_sector`/
    `provider_sector_as_of`/`cluster_id` only blanks a label and is let
    through.
    """
    from src import clusters as clusters_mod
    from src import instruments as instruments_mod

    pos = pd.read_csv(positions_path or POSITIONS_PATH, dtype=str,
                      keep_default_na=False)
    # `ticker`/`currency`/`priced_at` are on every real positions.csv row —
    # the commit guard enforces the shape. A caller's own tiny store (the
    # same `clusters.weights()` callers above) may carry only `instrument_id`
    # and `market_value`; falling the lot's name back to its instrument_id
    # keeps the error message below meaningful without inventing a trade.
    if "ticker" not in pos.columns:
        pos["ticker"] = pos["instrument_id"]
    for col in ("currency", "priced_at"):
        if col not in pos.columns:
            pos[col] = ""

    # Loud, not coerced — same rule as claims.position_pct_of_nav(). Every
    # percentage on the page divides by this sum, and one blank row writes up
    # every holding in the book.
    mv = pd.to_numeric(pos["market_value"], errors="coerce")
    bad = pos.loc[mv.isna(), "ticker"].tolist()
    if bad:
        raise ValueError(
            f"{bad} have an unreadable market_value in "
            f"{Path(positions_path or POSITIONS_PATH).name}.\n"
            f"  Every figure on the page divides by the total, so it would be "
            f"computed against a short denominator and nothing would say so.\n"
            f"  Re-run the sync:  venv/bin/python -m src.ibkr_sync"
        )
    pos = pos.assign(market_value=mv.astype(float))

    inst = instruments_mod.load(
        instruments_path or instruments_mod.INSTRUMENTS_PATH
    ).set_index("instrument_id")
    if "instrument_type" not in inst.columns:
        raise ValueError(
            "the instruments store has no instrument_type column, so no "
            "lot can be placed in a basis — in_basis() reads it for every "
            "basis but 'everything'.\n"
            "  Pass a frame (or instruments_path) that carries instrument_type."
        )
    for col in _INSTRUMENT_LABEL_FIELDS:
        if col not in inst.columns:
            inst[col] = ""
    inst = inst.fillna("")
    cl = clusters_mod.load(clusters_path or clusters_mod.CLUSTERS_PATH)
    in_base = dict(zip(cl["cluster_id"], cl["in_equity_base"].astype(bool)))

    rows = []
    for _, p in pos.iterrows():
        iid = p["instrument_id"]
        if iid not in inst.index:
            continue
        cluster = str(inst.at[iid, "cluster_id"]).strip()
        rows.append({
            "ticker": p["ticker"],
            "instrument_id": iid,
            "market_value": float(p["market_value"]),
            "currency": (p.get("currency") or "USD").strip() or "USD",
            "instrument_type": inst.at[iid, "instrument_type"],
            "sector": inst.at[iid, "sector"],
            "provider_sector": inst.at[iid, "provider_sector"],
            "provider_sector_as_of": inst.at[iid, "provider_sector_as_of"],
            "cluster_id": cluster,
            # `.get(cluster, True)` is deliberate and matches what
            # clusters.weights() has always done: a holding with no cluster,
            # or one naming a cluster the file does not have, stays in the
            # equity base. Defaulting the other way would drop it out of the
            # denominator and write up every other holding — and the commit
            # guard already refuses the row, so this is the shape of a
            # hand-edit or a lot the sync has just auto-added.
            "in_equity_base": bool(in_base.get(cluster, True)),
            "priced_at": p.get("priced_at", ""),
        })
    return pd.DataFrame(rows, columns=LOT_COLUMNS)


def in_basis(row, basis: str) -> bool:
    """Does this lot count toward `basis`? The one place the lines are drawn."""
    check_basis(basis)
    kind = str(row["instrument_type"]).strip().lower()
    if basis == "everything":
        return True
    if kind in CASH_KINDS:
        return False
    if basis == "minus-cash":
        return True
    return bool(row["in_equity_base"])


def holdings(basis: str = "everything", **paths) -> pd.DataFrame:
    """One row per instrument within `basis`, its lots summed.

    `size` is magnitude, for ranking; `market_value` is signed, for drawing.
    """
    check_basis(basis)       # unconditional — an empty book must still refuse
                              # a typo'd basis rather than quietly answering 0.
    df = lots(**paths)
    if df.empty:
        return pd.DataFrame(columns=HOLDING_COLUMNS)
    keep = df[df.apply(lambda r: in_basis(r, basis), axis=1)]
    if keep.empty:
        return pd.DataFrame(columns=HOLDING_COLUMNS)
    out = (keep.groupby("instrument_id", as_index=False)
               .agg(market_value=("market_value", "sum"),
                    instrument_type=("instrument_type", "first"),
                    sector=("sector", "first"),
                    provider_sector=("provider_sector", "first"),
                    provider_sector_as_of=("provider_sector_as_of", "first"),
                    cluster_id=("cluster_id", "first")))
    out["size"] = out["market_value"].abs()
    return out[HOLDING_COLUMNS].sort_values(
        "size", ascending=False).reset_index(drop=True)


def nav(basis: str = "everything", **paths) -> float:
    """What `basis` is worth. The denominator of every percentage on the page.

    A related but deliberately different question from
    `claims.position_pct_of_nav()`, and the difference is not a bug to fix.
    That function is frozen at write time against "every dollar I had",
    where money under an instrument_id nobody has classified yet is still his
    money and stays in the total. This function excludes it, because a basis
    answers "what share of a theme/sector", and unclassified money has
    neither. The two are expected to agree except exactly when
    `unclassified()` is non-empty — a test in `tests/test_claims.py` pins
    that boundary.
    """
    return float(holdings(basis, **paths)["market_value"].sum())


def biggest(n: int = 5, basis: str = "everything", **paths) -> pd.DataFrame:
    """The n largest positions by size, with their share of `basis`."""
    held = holdings(basis, **paths)
    total = float(held["market_value"].sum())
    out = held.head(n).copy()
    out["pct_of_basis"] = (out["market_value"] / total * 100).round(2) if total \
        else 0.0
    return out.reset_index(drop=True)


def by_currency(basis: str = "everything", **paths) -> pd.DataFrame:
    """What the book is quoted in, by lot.

    `market_value` is always USD (D9); `currency` says what `cost_basis` and
    `last_price` are quoted in. This is the panel-7 currency split, and it is
    per LOT — an instrument with a dollar lot and a euro lot is in both rows.
    """
    check_basis(basis)       # unconditional — same reason as in holdings().
    df = lots(**paths)
    if df.empty:
        return pd.DataFrame(columns=["currency", "market_value", "pct_of_basis"])
    keep = df[df.apply(lambda r: in_basis(r, basis), axis=1)]
    total = float(keep["market_value"].sum())
    out = (keep.groupby("currency", as_index=False)["market_value"].sum()
               .sort_values("market_value", ascending=False))
    out["pct_of_basis"] = (out["market_value"] / total * 100).round(2) if total \
        else 0.0
    return out.reset_index(drop=True)


def unclassified(positions_path: Path | None = None,
                 instruments_path: Path | None = None) -> pd.DataFrame:
    """Money held under an instrument_id that instruments.csv does not know.

    Shown on the page as unclassified rather than silently redistributed (the
    honesty contract). Empty is the normal answer — the commit guard enforces
    the foreign key — but `ibkr_sync` writes a brand-new lot with "AUTO-ADDED"
    in its notes, so this is exactly how a ticker bought this week arrives.

    Explicit keyword parameters, not `**paths` — a misspelt keyword used to
    fall through to `.get()`'s default and silently answer from the real book
    instead of refusing.

    Deliberately does NOT raise on an unreadable `market_value`, unlike
    `lots()` on the same input — it coerces to $0 instead. `lots()` feeds
    `nav()`, the page's one denominator, where a wrong number must never look
    right. This function feeds a "visible, never blocking" gap report
    (`ops/data_gaps.py`'s `gaps()`, via `exposure.unknown_holdings()`), and
    the row most likely to land here — a brand-new auto-added lot with no
    price yet — is exactly the one that must not crash the whole report
    instead of naming the missing instruments.csv row that is the real
    problem.
    """
    from src import instruments as instruments_mod

    pos = pd.read_csv(positions_path or POSITIONS_PATH, dtype=str,
                      keep_default_na=False)
    pos["mv"] = pd.to_numeric(pos["market_value"], errors="coerce").fillna(0.0)
    known = set(instruments_mod.load(
        instruments_path or instruments_mod.INSTRUMENTS_PATH
    )["instrument_id"])
    out = (pos[~pos["instrument_id"].isin(known)]
           .groupby("instrument_id", as_index=False)["mv"].sum()
           .rename(columns={"mv": "market_value"}))
    return out.sort_values("market_value", ascending=False).reset_index(drop=True)


# ─── profit and loss ────────────────────────────────────────────────────
#
# Two different numbers, and neither is a share of a basis — a realised gain
# from a position he no longer holds has no basis to be a share OF, and his
# own decision on sizing the unrealised side (see `lot_cost_usd()` below)
# does not change if he happens to be looking at "minus cash" at the time.
# So, alone among this module's figures, the functions below take no
# `basis` argument at all.

#: `instruments_mod.load()`'s `instrument_type` values that are a cash or
#: FX balance rather than a position with a cost to recover — same set
#: `in_basis()` already excludes from every basis above. A cash balance was
#: never "bought" at a price, so it has no realised/unrealised split to make.
PNL_EXCLUDED_KINDS = CASH_KINDS

#: One row per open, non-cash lot. `method` says which of Yaron's two rules
#: (CLAUDE.md, 2026-10-08) produced `cost_usd`; `flagged` is True only for
#: the one case his decision calls out by name — a foreign lot priced at
#: TODAY's exchange rate because its own purchases could not be
#: reconstructed, which measures the share price's own gain, never dollars
#: in. `established` is False only when no number could be built at all —
#: `cost_usd`/`unrealized` are then `float("nan")`, never a guess.
PNL_LOT_COLUMNS = ["ticker", "instrument_id", "currency", "shares",
                   "market_value", "cost_usd", "unrealized", "method",
                   "flagged", "reason", "established"]


def _lot_symbol(ticker: str, currency: str) -> str:
    """The IBKR `symbol` a lot's ticker was built from — the inverse of
    `ibkr_sync._derive_new_ticker()`. A ticker like `ASML_EUR` is the bare
    symbol plus `_{currency}` ONLY when the suffix actually names this
    lot's own currency; a symbol that happens to contain its own underscore
    (none do today) or a space (`BRK B`) is returned unchanged, because
    nothing here may strip a character that was never a disambiguator."""
    if "_" in ticker:
        symbol, suffix = ticker.rsplit("_", 1)
        if suffix == currency:
            return symbol
    return ticker


def _numeric_cell(value) -> float:
    """One positions.csv cell, read as a float — NaN for blank/unreadable,
    never a coerced 0.0. `pd.read_csv(..., dtype=str)` is how every lot
    function in this module reads the file (so an id column survives
    untouched); this is the one-cell equivalent of `pd.to_numeric(...,
    errors="coerce")` for a value pulled out of the resulting Series."""
    text = str(value).strip()
    if not text:
        return float("nan")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def lot_cost_usd(positions_path: Path | None = None,
                 instruments_path: Path | None = None,
                 transactions_path: Path | None = None,
                 corporate_actions_path: Path | None = None) -> pd.DataFrame:
    """The USD cost of every open, non-cash lot — his decision (CLAUDE.md,
    2026-10-08), not a rule this function gets to revisit:

    **A — a foreign lot's cost is dollars in, at the rate on the day he
    actually paid.** Currency movement since then is part of the gain, not
    noise to strip out, so this only holds when `transactions.csv` can
    reconstruct the purchases — their quantities sum to the shares held
    (plus any corporate action on the symbol) and none of them is a SELL,
    because a sale means some of the cost belongs to shares no longer in
    this lot and a plain sum of every buy would overstate what is left.
    `cost_usd` is then the sum of each buy's own `net_cash × fx_rate_to_base`
    — `net_cash` already carries the commission IBKR's own total does, and
    each trade's own rate is this project's only record of what the dollar
    was worth that day.

    **B — everywhere else, IBKR's own total.** A dollar lot needs no FX
    conversion at all, so its own `cost_basis_money` (commission and splits
    already folded in — see `src/ibkr_sync.py`'s column comment) IS the USD
    cost, independent of whether the trade log reconciles; the log's
    completeness was never in question for a currency that does not move.
    A foreign lot whose purchases cannot be reconstructed gets the same
    total, translated at `fx_rate_to_base` — the rate on the day IBKR
    reported the position, not the day he bought it. `flagged` marks this
    one case: the figure then answers "what is the share price doing",
    not "how many dollars is he up", and `page.honesty()` names every lot
    where that happened.

    **Refuse rather than mislead.** A lot whose purchases reconstruct to the
    right share count is still checked against IBKR's own total, in the
    lot's native currency, before rule A is trusted — real money, on this
    book, and the two have always agreed to the cent. A disagreement beyond
    rounding is not silently resolved either way: `cost_usd` falls back to
    rule B and `flagged` fires, with `reason` naming the two figures that
    disagreed.

    A lot gets no figure at all (`established=False`, `cost_usd`/
    `unrealized` both `float("nan")`) only when the inputs themselves are
    missing — no `cost_basis_money`, or (for a foreign lot) no
    `fx_rate_to_base` — which today means a positions.csv row written before
    `ibkr_sync` carried either column. `uncosted_lots()` is where that is
    reported; nothing here invents a number to fill the gap.
    """
    from src import instruments as instruments_mod

    pos = pd.read_csv(positions_path or POSITIONS_PATH, dtype=str,
                      keep_default_na=False)
    inst = instruments_mod.load(
        instruments_path or instruments_mod.INSTRUMENTS_PATH
    ).set_index("instrument_id")

    tx_path = transactions_path or TRANSACTIONS_PATH
    tx_cols = ["symbol", "currency", "asset_category", "quantity",
              "net_cash", "fx_rate_to_base"]
    if Path(tx_path).exists():
        tx = pd.read_csv(tx_path, dtype=str, keep_default_na=False)
        for col in ("quantity", "net_cash", "fx_rate_to_base"):
            tx[col] = pd.to_numeric(tx[col], errors="coerce")
    else:
        tx = pd.DataFrame(columns=tx_cols)

    ca_path = corporate_actions_path or CORPORATE_ACTIONS_PATH
    if Path(ca_path).exists():
        ca = pd.read_csv(ca_path, dtype=str, keep_default_na=False)
        if len(ca):
            ca["quantity"] = pd.to_numeric(ca["quantity"], errors="coerce")
    else:
        ca = pd.DataFrame(columns=["symbol", "quantity"])

    rows = []
    for _, p in pos.iterrows():
        ticker = p["ticker"]
        iid = p["instrument_id"]
        kind = (str(inst.at[iid, "instrument_type"]).strip().lower()
               if iid in inst.index else "")
        if kind in PNL_EXCLUDED_KINDS:
            continue

        currency = (p.get("currency") or "").strip()
        shares = _numeric_cell(p.get("shares"))
        market_value = _numeric_cell(p.get("market_value"))
        cbm = _numeric_cell(p.get("cost_basis_money"))
        fx = _numeric_cell(p.get("fx_rate_to_base"))
        base = {"ticker": ticker, "instrument_id": iid, "currency": currency,
               "shares": shares, "market_value": market_value}

        if currency == "USD":
            if pd.isna(cbm):
                rows.append({**base, "cost_usd": float("nan"),
                            "unrealized": float("nan"), "method": "",
                            "flagged": False, "established": False,
                            "reason": (f"{ticker}: IBKR reports no cost "
                                      f"basis for this lot")})
                continue
            rows.append({**base, "cost_usd": cbm,
                        "unrealized": market_value - cbm,
                        "method": "ibkr_total", "flagged": False,
                        "established": True, "reason": ""})
            continue

        # A foreign lot. Rule A needs both a cost and a rate to fall back to
        # (rule B) the moment reconstruction is not possible — so both are
        # required before either rule can run, not only the one rule ends
        # up using.
        if pd.isna(cbm) or pd.isna(fx):
            rows.append({**base, "cost_usd": float("nan"),
                        "unrealized": float("nan"), "method": "",
                        "flagged": False, "established": False,
                        "reason": (f"{ticker}: IBKR reports no cost basis "
                                  f"or exchange rate for this lot")})
            continue

        symbol = _lot_symbol(ticker, currency)
        lot_tx = (tx[(tx["symbol"] == symbol) & (tx["currency"] == currency)
                     & (tx["asset_category"] == "STK")]
                 if len(tx) else tx)
        lot_ca = ca[ca["symbol"] == symbol] if len(ca) else ca

        traded = float(lot_tx["quantity"].sum()) if len(lot_tx) else 0.0
        acted = float(lot_ca["quantity"].sum()) if len(lot_ca) else 0.0
        reconciles = abs((traded + acted) - shares) <= 1e-6
        has_sells = bool((lot_tx["quantity"] < 0).any()) if len(lot_tx) else False
        has_buys = bool((lot_tx["quantity"] > 0).any()) if len(lot_tx) else False

        if reconciles and has_buys and not has_sells:
            native_cost = float((-lot_tx["net_cash"]).sum())
            # A cent or two of rounding is normal; both real foreign lots on
            # this book agree with IBKR's own total exactly. A relative
            # tolerance on top of the flat cent catches a genuinely large
            # lot without a hand-picked constant that only works at today's
            # position sizes.
            tolerance = max(0.01, abs(cbm) * 0.0005)
            if abs(native_cost - cbm) <= tolerance:
                usd_cost = float((-lot_tx["net_cash"] * lot_tx["fx_rate_to_base"]).sum())
                rows.append({**base, "cost_usd": usd_cost,
                            "unrealized": market_value - usd_cost,
                            "method": "trade_reconstruction", "flagged": False,
                            "established": True, "reason": ""})
                continue
            reason = (
                f"{ticker}: its trades reconcile to the {shares:g} shares "
                f"held, but their own cost ({native_cost:,.2f} {currency}) "
                f"disagrees with IBKR's own figure ({cbm:,.2f} {currency}) "
                f"by more than a rounding error — using IBKR's cost at "
                f"today's exchange rate rather than silently picking one."
            )
        elif reconciles and has_sells:
            # A sale means part of what was bought no longer belongs to this
            # lot — summing every buy's net_cash would charge the remaining
            # shares for stock that is already gone, which is exactly the
            # kind of confident-but-wrong number rule A must never produce.
            # This project's own book has no such lot today (`tests/
            # test_book.py` proves the fallback fires here with a fixture,
            # since real data cannot).
            reason = (
                f"{ticker}: its trades reconcile to the {shares:g} shares "
                f"held, but include a sale — a plain sum of every purchase "
                f"would overstate what is left in this lot — using IBKR's "
                f"cost at today's exchange rate instead, which measures the "
                f"share price's own gain, not dollars in."
            )
        elif reconciles:
            # Reconciles with no buy AND no sell on file at all — the whole
            # lot arrived through a corporate action alone, so there is no
            # purchase for rule A to reconstruct a rate from.
            reason = (
                f"{ticker}: its {shares:g} shares reconcile with no "
                f"purchase on file to reconstruct a rate from — using "
                f"IBKR's cost at today's exchange rate instead, which "
                f"measures the share price's own gain, not dollars in."
            )
        else:
            reason = (
                f"{ticker}: its trades on file do not reconcile to the "
                f"{shares:g} shares held, so each purchase's own exchange "
                f"rate cannot be reconstructed — using IBKR's cost basis "
                f"at today's exchange rate instead, which measures the "
                f"share price's own gain, not dollars in."
            )

        usd_cost = float(cbm * fx)
        rows.append({**base, "cost_usd": usd_cost,
                    "unrealized": market_value - usd_cost,
                    "method": "ibkr_report_date_fx", "flagged": True,
                    "established": True, "reason": reason})

    return pd.DataFrame(rows, columns=PNL_LOT_COLUMNS)


def uncosted_lots(positions_path: Path | None = None,
                  instruments_path: Path | None = None,
                  transactions_path: Path | None = None,
                  corporate_actions_path: Path | None = None) -> pd.DataFrame:
    """Lots named, never guessed at, because `lot_cost_usd()` could not
    build a cost for them at all. Empty is the normal answer — reported
    here rather than folded into `unrealized_pnl()`'s own total, the same
    "visible, never blocking" shape `unclassified()` already gives a
    different gap."""
    df = lot_cost_usd(positions_path, instruments_path, transactions_path,
                      corporate_actions_path)
    if df.empty:
        return df
    return (df.loc[~df["established"], ["ticker", "instrument_id", "reason"]]
             .reset_index(drop=True))


def fx_fallback_lots(positions_path: Path | None = None,
                     instruments_path: Path | None = None,
                     transactions_path: Path | None = None,
                     corporate_actions_path: Path | None = None) -> pd.DataFrame:
    """Lots priced at today's exchange rate instead of each purchase's own
    — rule B firing where rule A could not (`lot_cost_usd()`'s own
    docstring). Empty today, on his real book; `page.honesty()` reads this
    and says nothing when it is."""
    df = lot_cost_usd(positions_path, instruments_path, transactions_path,
                      corporate_actions_path)
    if df.empty:
        return df
    return (df.loc[df["flagged"], ["ticker", "instrument_id", "currency", "reason"]]
             .reset_index(drop=True))


def unrealized_pnl(positions_path: Path | None = None,
                   instruments_path: Path | None = None,
                   transactions_path: Path | None = None,
                   corporate_actions_path: Path | None = None) -> float | None:
    """Market value minus USD cost, summed over every lot that HAS a cost.
    A lot `uncosted_lots()` names is left out of this total rather than
    treated as a zero-cost windfall — the same reasoning `unclassified()`
    keeps unclassified money out of a sector total rather than scoring it
    as a confirmed zero for one.

    🚨 **Review, 2026-10-08 (I3): `None`, never `0.0`, when NOTHING could be
    costed.** `df.loc[df["established"], ...].sum()` on a selection that
    happens to be empty is `0.0` by pandas's own convention — indistinguishable
    from every real lot genuinely netting to zero. Demonstrated against an
    actual pre-migration `positions.csv` (no `cost_basis_money` column at
    all): every one of 45 lots reads `established=False`, and the old code
    here returned a perfectly plausible `$0.00` — `_fig()`'s whole "a
    missing figure is a dash, never a zero" rule (page.py's own trap 3),
    bypassed one layer below where that rule could ever see it. `uncosted_
    lots()` already names every lot behind this `None`; this function's
    only job is to stop asserting a number it does not have.

    A genuinely empty book (zero non-cash lots at all — `df.empty`, not
    "no established row among several") is a real `0.0`: there is nothing
    to have a profit or a loss on, which is a different fact from "every
    lot in a non-empty book failed to cost."
    """
    df = lot_cost_usd(positions_path, instruments_path, transactions_path,
                      corporate_actions_path)
    if df.empty:
        return 0.0
    established = df[df["established"]]
    if established.empty:
        return None
    return float(established["unrealized"].sum())


def realized_pnl(transactions_path: Path | None = None) -> float:
    """Every dollar IBKR has already banked for him, on a closed trade,
    summed once.

    🚨 **`fifo_pnl` is denominated in the TRADE'S OWN currency, never
    base** — review found this, 2026-10-08, and it was wrong here before:
    this docstring used to claim the opposite. Proof is in the raw Flex
    XML for every EUR trade (IMAE's close, `currency="EUR"`): `proceeds -
    cost - commission == fifoPnlRealized`, in euros, not dollars. Almost
    every one of his closed trades happens to be in USD, which is exactly
    why a bare `.sum()` read only $31 light on real data and not — his own
    warning, verbatim — "screw the program": the one foreign position he
    holds today is Samsung, in KRW, and a KRW sell's `fifo_pnl` is a
    six-figure won number that a bare sum would have shown as a six-figure
    DOLLAR gain. Each row is multiplied by THAT TRADE'S OWN
    `fx_rate_to_base` — the same per-trade column `lot_cost_usd()` already
    reads for rule A — before summing; never the report-date rate a
    position carries, which belongs to no particular trade. Most rows are
    0.0 (an opening trade realises nothing); loud, not coerced, on the
    rare row that will not parse at all (either column), the same
    refuse-rather-than-undercount shape `lots()` already uses for
    `market_value` — a realised total that quietly dropped one bad row
    would look exactly like a clean one."""
    tx = pd.read_csv(transactions_path or TRANSACTIONS_PATH, dtype=str,
                     keep_default_na=False)
    if tx.empty:
        return 0.0
    pnl = pd.to_numeric(tx["fifo_pnl"], errors="coerce")
    fx = pd.to_numeric(tx["fx_rate_to_base"], errors="coerce")
    bad = sorted(set(tx.loc[pnl.isna(), "trade_id"])
                | set(tx.loc[fx.isna(), "trade_id"]))
    if bad:
        raise ValueError(
            f"{bad} have an unreadable fifo_pnl or fx_rate_to_base in "
            f"{Path(transactions_path or TRANSACTIONS_PATH).name}.\n"
            f"  Re-run the sync:  venv/bin/python -m src.ibkr_sync"
        )
    return float((pnl * fx).sum())


def total_pnl(positions_path: Path | None = None,
             instruments_path: Path | None = None,
             transactions_path: Path | None = None,
             corporate_actions_path: Path | None = None) -> float | None:
    """Realised plus unrealised — "how is this book doing, all in."
    Each half is still its own authoritative call (never a cached pair
    added together here by coincidence of two calls agreeing): this
    function exists so THIS sum is taken exactly once, in the one place
    that owns both halves, rather than by a page that is forbidden from
    adding anything at all (D2).

    `None` whenever `unrealized_pnl()` is — a "total" that quietly dropped
    its entire unrealised half to zero and called the realised-only
    remainder a complete answer is the exact I3 failure (review,
    2026-10-08): on a book where every lot happens to be uncosted, the
    old code here still printed a perfectly plausible dollar figure."""
    realized = realized_pnl(transactions_path)
    unrealized = unrealized_pnl(positions_path, instruments_path,
                                transactions_path, corporate_actions_path)
    if unrealized is None:
        return None
    return float(realized + unrealized)


def fx_choice_effect(positions_path: Path | None = None,
                     instruments_path: Path | None = None,
                     transactions_path: Path | None = None,
                     corporate_actions_path: Path | None = None) -> float:
    """How much lower (positive) or higher (negative) `unrealized_pnl()`
    reads than IBKR's own number would, purely because of decision A —
    each `method="trade_reconstruction"` lot's dollars-in cost, at the
    rate he actually paid, instead of today's report-date rate applied to
    the same `cost_basis_money`. Review, 2026-10-08 (I2): his figure is
    correct for what it measures and the gap is real, not a bug — but
    nothing said so anywhere on the page, which is the one shape of
    silence his own FX worry was about. `page.honesty()` is where this
    number reaches him, in a sentence that fires for every lot rule A
    actually prices — the ordinary case, not an edge case.

    0.0 when no lot was priced by rule A at all (nothing to explain) —
    never confused with "the two methods happened to agree to the cent",
    which would also be `0.0` and is a real, if coincidental, possibility
    this function cannot and does not try to tell apart from the first.
    """
    df = lot_cost_usd(positions_path, instruments_path, transactions_path,
                      corporate_actions_path)
    if df.empty:
        return 0.0
    reconstructed = df[df["method"] == "trade_reconstruction"]
    if reconstructed.empty:
        return 0.0
    pos = pd.read_csv(positions_path or POSITIONS_PATH, dtype=str,
                      keep_default_na=False).set_index("ticker")
    total = 0.0
    for _, r in reconstructed.iterrows():
        cbm = _numeric_cell(pos.at[r["ticker"], "cost_basis_money"])
        fx = _numeric_cell(pos.at[r["ticker"], "fx_rate_to_base"])
        broker_cost = cbm * fx
        total += float(r["cost_usd"]) - broker_cost
    return float(total)


#: Cash-transaction types that are an owner's own cash flow — a deposit or
#: a withdrawal — never income or cost, and already handled on their own
#: terms by `ibkr_sync.sync_nav_history()`. Every other type (dividends,
#: withholding tax, interest, payment-in-lieu, commission adjustments) is
#: real money moving independently of any position's market value.
_OWNER_CASH_FLOW_TYPES = {"Deposits/Withdrawals"}


def excluded_cash_flows(cash_transactions_path: Path | None = None,
                        borrow_fees_path: Path | None = None) -> float:
    """Net dividends, withholding tax, interest, commission adjustments and
    short-borrow fees — real money, already in `data/`, that neither
    `realized_pnl()` nor `unrealized_pnl()` counts at all. Review,
    2026-10-08 (I2, "while you are there"): roughly a tenth of `total_pnl()`
    on his real book today, silently. `page.honesty()` says so, with this
    number — never folded into `total_pnl()` itself, which stays exactly
    what CLAUDE.md asked for: realised trades plus unrealised mark-to-
    market, nothing else quietly mixed in.

    Positive means net income (dividends dominate on his book); negative
    would mean net cost. Loud, not coerced, on a row that will not parse —
    the same shape every other total in this module already refuses on.
    """
    total = 0.0
    bad: list[str] = []

    ct_path = cash_transactions_path or CASH_TRANSACTIONS_PATH
    if Path(ct_path).exists():
        ct = pd.read_csv(ct_path, dtype=str, keep_default_na=False)
        ct = ct[~ct["type"].isin(_OWNER_CASH_FLOW_TYPES)]
        if len(ct):
            amount = pd.to_numeric(ct["amount"], errors="coerce")
            fx = pd.to_numeric(ct["fx_rate_to_base"], errors="coerce")
            bad += ct.loc[amount.isna() | fx.isna(), "transaction_id"].tolist()
            total += float((amount * fx).sum())

    bf_path = borrow_fees_path or BORROW_FEES_PATH
    if Path(bf_path).exists():
        bf = pd.read_csv(bf_path, dtype=str, keep_default_na=False)
        if len(bf):
            fee = pd.to_numeric(bf["borrow_fee"], errors="coerce")
            fx = pd.to_numeric(bf["fx_rate_to_base"], errors="coerce")
            bad += [f"{d}/{s}" for d, s in
                   zip(bf.loc[fee.isna() | fx.isna(), "date"],
                       bf.loc[fee.isna() | fx.isna(), "symbol"])]
            total += float((fee * fx).sum())

    if bad:
        raise ValueError(
            f"{bad} have an unreadable amount or fx_rate_to_base in "
            f"cash_transactions.csv or borrow_fees.csv.\n"
            f"  Re-run the sync:  venv/bin/python -m src.ibkr_sync"
        )
    return total
