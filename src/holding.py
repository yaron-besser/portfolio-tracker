"""Everything the record knows about one holding, and what it knows about none.

Read-only. This module never writes — it exists so `/show` and `/note` can never
disagree about what the record says, because they both ask the same question of
the same code.

Two views:

    summary(instrument_id)   one holding, across every table
    unexplained(days=7)      traded recently, and nothing explains why

`unexplained()` is a join that did not exist before 2026-08-15. Every other view
in this project starts from `claims.csv` — `due_claims()`, `follow_through()`,
`evidence.unevidenced_claims()` — so a position with no claim row is invisible to
them. Yaron's words: "let's say I bought a few days ago something and it doesn't
have anything, and then you do the synchronisation and you see something has
been bought and there is no mention about it. Then your responsibility is to
flag me up."

⚠️ It reports; it never fixes. Same rule as `levels.orphaned_levels()`, and for
the same reason — a guess written into the record is worse than a gap.

Usage:
    from src import holding
    holding.summary("IWM")        # what do I know about IWM?
    holding.unexplained()         # what did I buy without saying why?
    holding.sources_line(…)       # the one way both commands print documents
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.claims import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"

# Every reader takes `data_dir` rather than reading a module constant, because
# a default argument is bound once at import: `def load(path=INSTRUMENTS_PATH)`
# cannot be redirected by reassigning the constant later. A test that tried
# would silently read the real portfolio — which is the exact trap this file's
# own docstring warns about, and it caught me writing these tests.

# How recent a trade must be to be worth flagging.
#
# Written 2026-08-15, when the claim store was three days old and 45 of 47
# holdings had no claim: flagging every one would have been noise nobody
# reads. That backlog is gone — `unexplained(all_holdings=True)` returns 2
# today — but the window is still right, and for a better reason: a trade he
# has not written up within a week is a trade he has forgotten, which is the
# thing worth surfacing.
#
# Seven days is Yaron's call and matches what he described: "I bought a few
# days ago". Run the function rather than trusting the count in this comment;
# the count it replaced sat here wrong for six weeks.
DEFAULT_DAYS = 7


def _read(path: Path, columns: list[str], required: bool = False) -> pd.DataFrame:
    """A CSV as text, or an empty frame with the right columns.

    `required=True` raises when the file is missing or zero-byte, because for
    this module those are not the same as "no rows". A sync interrupted between
    truncate and write leaves a zero-byte transactions.csv, and reading that as
    an empty table would make the very next report say "nothing unexplained" —
    the silent all-clear P2 exists to prevent (found by review 2026-08-15).
    """
    if not path.exists() or path.stat().st_size == 0:
        if required:
            raise ValueError(
                f"{path.name} is missing or empty — cannot tell whether anything "
                f"is unexplained.\n  Re-run the sync before trusting this answer."
            )
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return df if not df.empty else pd.DataFrame(columns=columns)


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _resolve(symbol: str) -> str | None:
    """A broker symbol to its instrument, or None if it cannot be resolved.

    ASML_USD and ASML_EUR are two lots of ONE instrument. Skipping this step
    would report a lot as unexplained while its sibling carries the claim.
    """
    from src import instruments

    try:
        return instruments.resolve(symbol)
    except Exception:
        return None


def unexplained(days: int = DEFAULT_DAYS, as_of: str | None = None,
                all_holdings: bool = False,
                data_dir: Path | None = None) -> pd.DataFrame:
    """Instruments traded recently that no claim explains.

    `all_holdings=True` widens it to every held instrument regardless of when it
    was traded — the full backlog, for querying rather than nagging.

    A CLOSED position still appears if it was traded inside the window: buying
    and selling something with no record of why is more worth surfacing than a
    position still sitting there, not less.

    Cash and FX never appear — there is no thesis to write about the euro
    balance.
    """
    from src import claims, instruments

    d = data_dir or DATA_DIR
    today = date.fromisoformat(as_of) if as_of else date.today()
    cutoff = (today - timedelta(days=days)).isoformat()

    inst = instruments.load(d / "instruments.csv").set_index("instrument_id")
    claimed = set(claims.load_claims(d / "claims.csv")["instrument_id"])

    tx = _read(d / "transactions.csv", ["trade_id", "trade_date", "symbol"],
               required=True)
    recent = tx[tx["trade_date"] >= cutoff] if not tx.empty else tx

    pos = _read(d / "positions.csv", ["instrument_id", "shares", "market_value"])
    if not pos.empty:
        pos = pos.assign(_sh=_num(pos["shares"]), _mv=_num(pos["market_value"]))

    candidates: dict[str, dict] = {}

    def consider(iid: str, why: str) -> None:
        if not iid or iid in claimed:
            return
        if iid not in inst.index:
            # NOT a silent drop. ibkr_sync writes a brand-new lot with
            # "AUTO-ADDED — add it to instruments.csv", so this is exactly how a
            # freshly bought ticker arrives — DRAM among them. Dropping it here
            # would hide the newest trades, which are the ones most worth
            # flagging (found by review 2026-08-15).
            candidates.setdefault(iid, {"instrument_id": iid,
                                        "why": "traded, and not in instruments.csv"})
            return
        if inst.at[iid, "instrument_type"] in ("cash", "fx"):
            return
        candidates.setdefault(iid, {"instrument_id": iid, "why": why})

    for sym in (recent["symbol"].unique() if not recent.empty else []):
        iid = _resolve(sym)
        if iid is None:
            # resolve() raises loudly on a blank/garbled symbol; swallowing that
            # would drop a real trade. Surface it instead.
            candidates.setdefault(f"?{sym}", {"instrument_id": f"?{sym}",
                                              "why": "traded, symbol unreadable"})
            continue
        consider(iid, "traded, no claim")

    if all_holdings and not pos.empty:
        for _, r in pos[pos["_sh"] > 0].iterrows():
            consider(r["instrument_id"], "held, no claim")

    cols = ["instrument_id", "name", "market_value", "shares", "last_trade",
            "has_note", "why"]
    if not candidates:
        return pd.DataFrame(columns=cols)

    held = (pos.groupby("instrument_id")[["_sh", "_mv"]].sum()
            if not pos.empty else pd.DataFrame())
    last_trade = {}
    if not tx.empty:
        for _, t in tx.iterrows():
            iid = _resolve(t["symbol"])
            if iid:
                last_trade[iid] = max(last_trade.get(iid, ""), t["trade_date"])

    rows = []
    for iid, c in candidates.items():
        known = iid in inst.index
        note = str(inst.at[iid, "note_ref"] or "").strip() if known else ""
        rows.append({
            "instrument_id": iid,
            "name": inst.at[iid, "name"] if known else "(not in instruments.csv)",
            "market_value": round(float(held.at[iid, "_mv"]), 2)
                            if iid in getattr(held, "index", []) else 0.0,
            "shares": float(held.at[iid, "_sh"])
                      if iid in getattr(held, "index", []) else 0.0,
            "last_trade": last_trade.get(iid, ""),
            "has_note": bool(note) and (PROJECT_ROOT / note).is_file(),
            "why": c["why"],
        })
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["last_trade", "market_value"],
                           ascending=[False, False]).reset_index(drop=True)


def what_is(name: str, data_dir: Path | None = None) -> str:
    """'cluster', 'instrument', 'sector', 'market', or 'unknown'.

    `/note Metals` and `/note IWM` are both valid and need different questions.
    Without this the skill called summary() on a cluster name and crashed on
    `instruments.get()` — a quarter of the queue is clusters, so that was a
    large slice of it (found by review, 2026-08-15). The exact count moves
    every time a cluster is added; `notes.status()` is the live answer.
    """
    from src import clusters, instruments

    d = data_dir or DATA_DIR
    # Clusters FIRST. resolve() is a pure string rule that strips spaces, so
    # "AI Infrastructure" would become "AIInfrastructure" and find nothing.
    if name in set(clusters.load(d / "clusters.csv")["cluster_id"]):
        return "cluster"
    known = set(instruments.load(d / "instruments.csv")["instrument_id"])
    if name in known:
        return "instrument"
    # ...then the spelling his broker uses. positions.csv carries lots whose
    # ticker is not the instrument id — ASML_EUR, NOV, "BRK B" — and matching
    # only the id told him a holding he owns does not exist, which invites
    # writing a second record for something already written up (audit
    # 2026-09-27). resolve() is the one rule that maps a lot to its instrument.
    try:
        if instruments.resolve(name) in known:
            return "instrument"
    except (ValueError, TypeError, AttributeError):
        pass
    # Sectors come AFTER clusters and instruments, deliberately. Seven names
    # are both a cluster and a sector — Technology, Healthcare, Financials,
    # Energy, Utilities, Consumer Staples, Real Estate — and the cluster is
    # what he means when he types one: the sector is a fact about exposure,
    # the cluster is the reason he bought. commentary.is_ambiguous() is how a
    # caller finds out the other reading exists.
    from src import sectors
    if name in set(sectors.load(d / "sectors.csv")["sector_id"]):
        return "sector"
    # The whole market. It names nothing, which is why it is a literal and not
    # a row: a pseudo-cluster for it would land in clusters.weights() and
    # corrupt every NAV percentage.
    if str(name).strip().lower() == "market":
        return "market"
    return "unknown"


def cluster_summary(cluster_id: str, data_dir: Path | None = None) -> dict:
    """Everything the record holds about one cluster.

    A cluster note answers a different question from a holding note — why the
    theme exists at all, and what would end it — so it needs its own view.
    ⚠️ The weight is computed here and must NEVER be written into the note:
    `crypto.md` recorded "8.1% ($9,240)" once and it was $4,100 by the time
    anyone read it again.
    """
    from src import clusters, instruments, notes
    from src import commentary as commentary_mod
    from src import evidence as evidence_mod

    d = data_dir or DATA_DIR
    row = clusters.get(cluster_id, d / "clusters.csv")     # raises if unknown

    inst = instruments.load(d / "instruments.csv")
    members = inst[inst["cluster_id"] == cluster_id]["instrument_id"].tolist()
    kids = clusters.children(cluster_id, d / "clusters.csv")
    parent = str(row.get("parent") or "").strip()

    w = clusters.weights(d / "clusters.csv", d / "positions.csv")
    mine = w[w["cluster_id"] == cluster_id]

    # A retired cluster is a theme he closed. Asking it to explain why it
    # exists, and calling it empty, are both answered by `retired` — and the
    # rename on 2026-09-28 made two of them permanent nags.
    retired = str(row.get("status") or "").strip() == "retired"
    # notes._state() is the ONE definition of note state. Re-deriving it here
    # let /show call a half-TODO note "ok" while notes.status() disagreed —
    # and CLAUDE.md promises the two can never disagree (review, 2026-08-15).
    note_ref = str(row.get("note_ref") or "").strip()
    state = notes._state(note_ref)["state"]

    gaps = []
    if retired:
        # Nothing is missing from a theme he closed on purpose. Retiring
        # Metals and Water on 2026-09-28 otherwise left two clusters
        # permanently asking to be explained (found by review the same day).
        pass
    elif state == "missing":
        gaps.append("no note — nothing says why this theme exists")
    elif state == "needs reshaping":
        gaps.append("the note is missing '## Why this cluster exists' or "
                    "'## What would end it'")
    elif state.startswith("TODO:"):
        gaps.append(f"the note still says TODO under: {state[6:]}")
    elif state == "broken link":
        gaps.append("the note_ref points at a file that does not exist")
    # A group legitimately holds no instruments — it holds clusters. What
    # would be wrong is a group with nothing inside it at all.
    if retired:
        pass
    elif row.get("layer") == "group":
        if not kids:
            gaps.append("a group with no clusters inside it")
    elif not members:
        gaps.append("named but nothing is filed under it")


    # The research that is about the theme itself, not about any one holding in
    # it (Yaron, 2026-08-17): "it's common to everyone". A broad AI report is
    # filed once against the cluster instead of twelve times against twelve
    # stocks, so it reads back here rather than nowhere.
    links = d / "evidence_links.csv"
    docs = evidence_mod.for_target(
        "cluster", cluster_id, links,
        paths={"document": d / "evidence.csv",
               "summary": d / "evidence_summaries.csv"})

    # A cluster inside a group inherits the same way a holding does: the group's
    # documents reach it as a count and NOTHING else (spec, Settled §2). Both
    # halves matter — merging them in would make one report read as research on
    # every cluster under it, and leaving them out makes a document filed on
    # Technology invisible from `/show Software`, which is the problem this
    # build exists to fix.
    up = evidence_mod.inherited("cluster", cluster_id, links,
                                paths={"cluster": d / "clusters.csv"})

    # What he has SAID about the theme, as opposed to what he read about it.
    # Both readings of an ambiguous name come back; the caller prints them
    # apart rather than merging two different questions.
    said = commentary_mod.about(cluster_id, scope="cluster",
                                path=d / "commentary.csv")

    return {"cluster": row, "members": members, "weights": mine,
            "parent": parent, "children": kids, "evidence": docs,
            "inherited": up, "said": said,
            "note_ref": note_ref, "note_state": state, "gaps": gaps}


def summary(instrument_id: str, data_dir: Path | None = None,
            as_of: str | None = None) -> dict:
    """Everything the record holds about one instrument.

    Raises on an unknown instrument rather than returning an empty shell — a
    typo should say so, not look like a holding with nothing written about it.

    Accepts a broker lot spelling as well as the instrument id. `what_is()`
    routes ASML_EUR, NOV and "BRK B" to "instrument", so this has to agree:
    resolving in only one of the two turned a wrong-but-polite "not a known
    ticker" into a KeyError halfway through a read (audit 2026-09-27). Asking
    by either lot returns the whole instrument — both ASML lots, never the one
    whose name was typed.
    """
    from src import claims, instruments, levels, notes
    from src import commentary as commentary_mod

    d = data_dir or DATA_DIR
    known = set(instruments.load(d / "instruments.csv")["instrument_id"])
    if instrument_id not in known:
        resolved = instruments.resolve(instrument_id)
        if resolved in known:
            instrument_id = resolved
    info = instruments.get(instrument_id, d / "instruments.csv")   # raises if unknown

    pos = _read(d / "positions.csv", ["ticker", "instrument_id", "shares",
                                 "cost_basis", "last_price", "market_value",
                                 "priced_at"])
    lots = pos[pos["instrument_id"] == instrument_id] if not pos.empty else pos

    c = claims.load_claims(d / "claims.csv")
    mine = c[c["instrument_id"] == instrument_id].sort_values("date")
    from src import evidence as evidence_mod

    rows = []
    for _, r in mine.iterrows():
        docs = evidence_mod.for_target(
            "claim", r["id"], d / "evidence_links.csv",
            paths={"document": d / "evidence.csv",
                   "summary": d / "evidence_summaries.csv"})
        rows.append({**r.to_dict(),
                     "setups": claims.setups_for(r["id"],
                                                 d / "claim_setups.csv"),
                     # Every document behind this claim, each carrying the page
                     # and the one-line why from its own link.
                     "evidence": docs.to_dict("records")})

    lv = levels.load_levels(d / "levels.csv")
    mine_lv = lv[lv["instrument_id"] == instrument_id].sort_values("date")

    tx = _read(d / "transactions.csv", ["trade_id", "trade_date", "symbol",
                                   "buy_sell", "quantity", "price", "currency"])
    trades = tx[tx["symbol"].map(lambda s: _resolve(s) == instrument_id)] \
        if not tx.empty else tx

    note_ref = str(info.get("note_ref") or "").strip()
    note_state = notes._state(note_ref)["state"]     # see cluster_summary()

    # Committed DOCUMENTS only: a URL and a private PDF are both real evidence
    # but neither is a screenshot sitting in the repo, and `gaps` reads this to
    # say "no chart filed against any claim".
    # `source_type` is half the rule, not a nicety. Every summary row carries
    # storage="repo" — truthfully, a notes/picks/ write-up IS committed — so
    # filtering on storage alone counted a markdown summary as a filed chart and
    # `_gaps()` then reported nothing missing: /show printed "the record is
    # complete" for a claim with no chart at all, which is the silent all-clear
    # gap-reporting exists to prevent (P2).
    shots = sorted({doc["location"]
                    for row in rows
                    for doc in row["evidence"]
                    if doc.get("source_type") == "document"
                    and doc.get("storage") == "repo"})

    links = d / "evidence_links.csv"
    source_paths = {"document": d / "evidence.csv",
                    "summary": d / "evidence_summaries.csv"}

    return {
        "instrument": info,
        "cluster": info.get("cluster_id", ""),
        # Documents about the company itself, with no decision attached — a
        # broker note on ASML is not a bet on ASML. A claim's own documents stay
        # on the claim, above.
        "evidence": evidence_mod.for_target("instrument", instrument_id, links,
                                            paths=source_paths),
        # The pointer, not the pile (Yaron, 2026-08-17): a holding shows its own
        # documents in full and only a COUNT of what sits above it — its
        # cluster, that cluster's group, its sector and that sector's parent —
        # so /show stays short. Nothing is stored, so nothing can go stale.
        # Keyed by (type, id), because a cluster and a sector can share a name,
        # and now a sector and its parent appear on the same line.
        "inherited": evidence_mod.inherited(
            "instrument", instrument_id, links,
            paths={"instrument": d / "instruments.csv",
                   "cluster": d / "clusters.csv",
                   "sector": d / "sectors.csv"}),
        # Thoughts and commentary filed on this holding. Distinct from
        # `claims`: a bet is scoreable and permanent, a comment is neither and
        # may be deleted.
        "said": commentary_mod.about(instrument_id, scope="instrument",
                                     path=d / "commentary.csv"),
        # Comments filed against one of this holding's BETS, as opposed to
        # against the holding itself. `/note` documents this as the way to
        # correct something in a claim that can no longer be edited — and
        # until 2026-10-01 nothing read those rows back, so it documented a
        # dead end. Zero existed when review found it; a trap, not a wound.
        "said_on_claims": _comments_on_claims(
            rows, d / "commentary.csv", commentary_mod),
        "lots": lots,
        "claims": rows,
        "levels": mine_lv,
        "trades": trades,
        "note_ref": note_ref,
        "note_state": note_state,
        "screenshots": shots,
        "price_warning": instruments.price_warning(
            path=d / "positions.csv"),
        "gaps": _gaps(rows, mine_lv, note_state, shots, as_of),
    }


def _gaps(claim_rows: list[dict], lv: pd.DataFrame, note_state: str,
          shots: list[str], as_of: str | None = None) -> list[str]:
    """What is missing. A gap he cannot see is a gap he will not fill (P2)."""
    out = []
    if not claim_rows:
        out.append("no claim — nothing on record says why this is held")
    if note_state == "missing":
        out.append("no note — no reasoning written down")
    elif note_state == "broken link":
        out.append("the note_ref points at a file that does not exist")
    elif note_state == "needs reshaping":
        out.append("the note is missing a required heading")
    elif note_state.startswith("TODO:"):
        out.append(f"the note still says TODO under: {note_state[6:]}")
    if claim_rows and not shots:
        out.append("no chart filed against any claim")
    today = as_of or date.today().isoformat()
    # A level was live AS OF that date if it existed by then and had not ended
    # by then. This used to ask only "is any level un-ended right now", which
    # ignored `as_of` completely — so ending a level in September retroactively
    # made a holding look incomplete in August, and the test pinning IWM as
    # finished on 2026-08-15 went red on a date it had nothing to do with.
    # Found 2026-09-28 by that test, when l0001 was ended.
    if lv.empty:
        live = 0
    else:
        started = lv["date"].fillna("").astype(str) <= today
        ended = lv["ended_date"].fillna("").astype(str)
        live = int((started & ((ended == "") | (ended > today))).sum())
    if live == 0:
        out.append("no active level — nothing would tell you if the price moved")
    for r in claim_rows:
        if pd.isna(r.get("outcome")) and str(r.get("check_date") or "") <= today \
           and str(r.get("check_date") or ""):
            out.append(f"{r['id']} was due {r['check_date']} and has no verdict")
    return out


# ─── how the two commands render what they read ───
#
# `/show` and `/note` both print a holding's documents and the count of what sits
# above it, and CLAUDE.md promises the two can never disagree about what the
# record says — because both stand on this module. For two commits that promise
# rested instead on ~30 lines copied byte-for-byte into both SKILL.md files,
# where nothing could notice one being edited and the other not. One definition,
# and tests that pin the output.


def sources_line(rows: list[dict] | pd.DataFrame) -> str:
    """Documents and summaries as one readable line, whatever they hang on.

    A summary is marked as one: it is a page-cited write-up of a report, not the
    report itself. `pd.notna`, not a plain truth test — a blank page reads back
    from CSV as NaN, which is truthy, and would print "p.nan". The title is
    guarded for the same class of reason, and twice over: `evidence.for_target()`
    left-joins, so a link whose source row is gone yields a NaN title and
    `NaN + str` is a bare TypeError exactly where P2 wants a sentence — while
    `_source_index()` yields "" (not NaN) for a summary whose parent document row
    was deleted, which `pd.notna` alone calls a title and prints as a bare page
    number with nothing in front of it. check_summaries() blocks that at commit;
    this is what says so in-session, on a file edited by hand.
    """
    records = rows if isinstance(rows, list) else rows.to_dict("records")
    out = []
    for r in records:
        title = (r["title"]
                 if pd.notna(r.get("title")) and str(r.get("title")).strip()
                 else f"⚠ {r['source_id']} — what this points at is missing")
        page = f" p.{r['page']}" if pd.notna(r.get("page")) else ""
        out.append(f"{title}{page}"
                   + (" (summary)" if r.get("source_type") == "summary" else ""))
    return "; ".join(out) or "(none)"


# What a price is quoted in. Blank means USD — 55 of 59 rows predate the
# column and are all dollars, so they must keep reading exactly as they did.
#
# Added 2026-10-01 after review found `claim_lines()` hard-coding `$`: c0027,
# his Samsung entry at ₩240,000 (about $173), read back as `$240000.0` on a
# $1,530 position. `levels.csv` had solved this on 2026-09-29 and the fix was
# never carried across to the table recording what he actually paid.
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "KRW": "₩"}


def _money(amount, currency) -> str:
    """A price with the right symbol in front of it."""
    code = str(currency or "").strip().upper()
    if not code or code == "NAN":
        code = "USD"
    sign = _SYMBOLS.get(code)
    return f"{sign}{amount}" if sign else f"{amount} {code}"


def _comments_on_claims(claim_rows, path, commentary_mod):
    """Every comment filed against any of these bets, newest first."""
    import pandas as _pd

    # `claims` is a list of dicts here, not a frame — summary() builds it row
    # by row so each bet can carry its own setups and evidence.
    if claim_rows is None or len(claim_rows) == 0:
        ids = []
    elif isinstance(claim_rows, _pd.DataFrame):
        ids = claim_rows["id"].tolist()
    else:
        ids = [r.get("id") for r in claim_rows if r.get("id")]
    frames = [commentary_mod.about(cid, scope="claim", path=path) for cid in ids]
    frames = [f for f in frames if len(f)]
    if not frames:
        cols = commentary_mod.COLUMNS if hasattr(commentary_mod, "COLUMNS") else []
        return _pd.DataFrame(columns=cols)
    return _pd.concat(frames, ignore_index=True).sort_values(
        "date", ascending=False).reset_index(drop=True)


def claim_lines(claim: dict) -> list[str]:
    """One bet as printable lines, with every optional cell guarded.

    `/show ASML` printed `watch · add · mine · nan · $nan · nan% of NAV` and
    `wrong if: nan` — a `watch` row carries no conviction or price and an
    `update` row carries no `wrong_if`, all of which read back as NaN. Shown to
    a man with $110K in the account, in a skill whose own rules say never to
    print a raw frame. Found by review 2026-09-27.

    Lives here for the same reason as `sources_line()` and `said_lines()`: both
    skills print this block, and two copies of a formatter are two things that
    drift. Call it; never re-inline it.
    """
    from src import commentary as commentary_mod

    def _or(key: str, dash: str = "—") -> str:
        text = commentary_mod._blank(claim.get(key))
        return text or dash

    head = f"{_or('date')}  {_or('id')}  {_or('kind')}"
    for field, label in (("intent", ""), ("origin", ""), ("said_by", "")):
        text = commentary_mod._blank(claim.get(field))
        if text:
            head += f" · {text}"
    conviction = commentary_mod._blank(claim.get("conviction"))
    if conviction:
        head += f" · {conviction} conviction"
    price = commentary_mod._blank(claim.get("price"))
    if price:
        head += f" · {_money(price, claim.get('currency'))}"
    nav = commentary_mod._blank(claim.get("nav_pct"))
    if nav:
        head += f" · {nav}% of NAV"

    # The call this bet came from, when one decision produced several trades.
    # Printed on the bet rather than instead of it: QQQ, MAGS and XLK were one
    # call and three different bets, and each keeps its own verdict.
    call = commentary_mod._blank(claim.get("decision"))
    if call:
        head += f"  ·  part of the {call} call"

    out = [head, f'    "{_or("claim", "(no words recorded)")}"']
    wrong_if = commentary_mod._blank(claim.get("wrong_if"))
    if wrong_if:
        out.append(f"    wrong if: {wrong_if}")
    out.append("    because : "
               + (", ".join(claim.get("setups") or []) or "(no setup recorded)"))
    # A judged bet must never print `next: <check_date>` — that is an
    # instruction to go and do work already done, and six resolved rows read
    # exactly like live ones until 2026-09-29, IWM's c0003 among them. His
    # verdict and his own words about it are the second of CLAUDE.md's three
    # jobs ("read it back"), and nothing displayed them anywhere.
    outcome = commentary_mod._blank(claim.get("outcome"))
    sources = sources_line(claim.get("evidence") or [])
    if outcome:
        judged = commentary_mod._blank(claim.get("resolved_date"))
        out.append(f"    verdict : {outcome}"
                   + (f", judged {judged}" if judged else "")
                   + f"   evidence: {sources}")
        said = commentary_mod._blank(claim.get("resolution_note"))
        if said:
            out.append(f'    his words: "{said}"')
    else:
        check = commentary_mod._blank(claim.get("check_date"))
        out.append(f"    next    : {check or 'nothing scheduled'}   evidence: "
                   f"{sources}")
    why_not = commentary_mod._blank(claim.get("why_not"))
    if why_not:
        out.append(f"    why not : {why_not}")
    return out


def said_lines(rows: pd.DataFrame) -> list[str]:
    """Thoughts and commentary as printable lines, newest first.

    Lives here and NOT inline in the skills, for the reason CLAUDE.md gives
    about `sources_line()`: /note and /show both print this block, and two
    copies of a formatter are two things that drift. Call it; never re-inline.

    `said_by` is always shown. Mark Newton and Tom Lee are both
    `origin=fundstrat`, so the name is the only thing separating them — and a
    blank one prints as "(nobody named)" rather than silently reading as his
    own words, which is the distinction `kind` exists to protect.
    """
    from src import commentary as commentary_mod

    out = []
    for r in (rows.to_dict("records") if isinstance(rows, pd.DataFrame) else rows):
        who = commentary_mod._blank(r.get("said_by"))
        if r.get("kind") == "commentary":
            who = who or "(nobody named)"
        else:
            who = "me"
        origin = commentary_mod._blank(r.get("origin"))
        tail = ""
        promoted = commentary_mod._blank(r.get("promoted_to"))
        if promoted:
            # Without this the same idea reads twice — once as a remark and
            # once as the bet it grew into.
            tail = f"  -> became {promoted}"
        scope = commentary_mod._blank(r.get("target_type"))
        where = commentary_mod._blank(r.get("target_id")) or "the whole market"
        out.append(
            f"{r['date']}  {r['id']}  {r.get('kind',''):10} {who:14} "
            f"\"{r.get('body','')}\""
            + (f"  [{origin}]" if origin and origin != "mine" else "")
            + f"  ({scope}: {where})" + tail
        )
    return out or ["(nothing said yet)"]


def inherited_line(counts: dict[tuple[str, str], int]) -> str:
    """What sits above this thing, as a count — never the documents themselves.

    The pointer, not the pile (Yaron, 2026-08-17). Keyed by (type, id), so the
    line says which kind each ancestor is: seven strings are both a cluster id
    and a sector id, and "Healthcare" alone would not say which one has the
    research. Empty in, empty out — the caller prints nothing rather than a line
    that says nothing.
    """
    if not counts:
        return ""
    return ", ".join(f"{n} more on the {ident} {where}"
                     for (where, ident), n in counts.items()) + " — ask to see them"
