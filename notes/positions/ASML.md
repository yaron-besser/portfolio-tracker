# ASML — ASML Holding NV (demo note)

> **Demo note.** Synthetic text, written to exercise this project's
> note-shape rules against a generated book. It is not investment advice
> and not anyone's real view of anything.

The tooling that the whole semiconductor layer depends on. Held in two
broker lots, one priced in dollars and one in euros, which is the reason
this project has a currency column at all.

## Bear case

A chokepoint is only a chokepoint until it is not. A second credible
supplier would change what this position is, not just what it is worth.

It is also the most politically exposed line in the book. What it may
sell, and to whom, is decided by governments rather than customers.

## Catalysts

- [ ] No second supplier of the same tooling appears
- [ ] Orders keep being placed rather than deferred

## The two lots

One instrument, two broker lots. `positions.csv` holds both; they share
one `instrument_id`, and the euro lot is quoted in euros while its
market value is in dollars. Selling one lot does not close the position,
and that bug is the reason the distinction is enforced in code.
