# Deferred Items — to discuss after Phase 5

**Project:** Enterprise Employee Analytics & Data Warehouse System
**Status:** parked by decision (2026-10-28): known points that are *not* worked on during the
phases; each is reviewed after Phase 5, together with the final diagram check.

None of these affects a required deliverable of Phases 1–5 as built. Each entry says what was
found, what it affects today, and the options with their cost.

---

## D1 — Promotions bunch up before the snapshot date

**Found:** in the generated job history, employees who stayed receive most of their promotions in
the last two years before the snapshot (2026-06-30). Promotions per fiscal year: 3,500–5,000 in
FY2019-20 … FY2023-24, then **17,349** in FY2024-25 and **28,288** in FY2025-26. Leavers' promotions
are spread evenly. Transfers, role changes and manager changes do not bunch up (checked).

**Cause:** IBM's `YearsSinceLastPromotion` is 0 for 39.5 % of employees. The synthesizer reproduces
it exactly: the last promotion of a stayer is dated back from the snapshot date, and a leaver's
from the exit date. A third of the stayers therefore get a promotion dated in the last year.

**Effect today:**
- In earlier years, "recently promoted" is mostly people who later leave. Among employees with
  3+ years at the company, those promoted within the last year left within a year at **7.9 %**,
  against 1.9–3.3 % for others — the opposite of real HR experience.
- A "promotions per year" or "time since last promotion" chart would mislead.
- *Not* affected: the attrition-risk score (promotion timing is deliberately not a factor), and
  the required dashboards (performance trends, top performers, attrition risk, projects).

**Options:**
1. Leave as is: don't chart promotion timing, or label it.
2. Move part of the snapshot-year promotions to earlier years. This breaks the 100 %
   reproduction of `YearsSinceLastPromotion` and the Phase 2 lossless-reconstruction proof, and
   regenerates the job history: all 308,986 versions, SCD Type 2 keys and facts, so every
   Phase 1–2 result is re-run and re-documented.

## D2 — No stand-alone salary revisions

**Found** during the Phase 1–3 requirements check: pay changes in 163,244 job versions, but always
together with a job event (promotion, role change, transfer, manager change). No version has the
reason "Salary Revision" (0 rows), although the schema and the application support it.

**Effect today:** the requirement's example "received a salary bump" is met through those pay
changes, but an annual raise *without* a job change never occurs in the generated history.

**Options:**
1. Leave as is.
2. Add yearly salary-revision versions in the synthesizer. That means more job versions, so the
   job history, SCD Type 2 versions, facts and every Phase 1–2 number change, as in D1.

## D3 — Attrition rate falls from 4.5 % to 2.6 % over the years

**Found:** leavers per fiscal year stay at about 2,150, while headcount grows from about 45,000
(FY2019-20) to 84,000 (FY2025-26). Headcount grows because IBM's median tenure is 5 years: most of
today's employees joined recently, so going back in time the company is smaller.

**Effect today:** the attrition trend chart shows a decline that comes from growth in headcount,
not from better retention. **Option:** explain it on the chart (Phase 4); no data change.

## D4 — Smaller patterns seen, not investigated

- One-year attrition at **9 years' tenure** is 5.7 %, against 2.2–2.9 % at 2–8 years (not used
  in the score).
- Satisfaction rises slightly over the years (job satisfaction 2.72 → 2.78), because earlier
  cycles hold more people who later left. This is consistent with D26 and needs no action.

## D5 — Faster analytics through a schema change (Kimball "Type 7")

**Found (Phase 3, backend_design.md §8.2):** the first, uncached call of the company-wide risk
score takes 18–20 s, and the back-test 20–22 s. Most of that is joining 463,015 facts to the
employee dimension.

**Option:** store the durable `employee_id` in `fact_performance_reviews` (Kimball Type 7), so
current-view analytics need no join. This changes the star-schema DDL, the fact load, the diagram,
dw_design.md and tests. Today the cache answers repeat calls in milliseconds.

## D6 — Small tidy-ups

- `src/synthesizer/validator.py` (`history_stats`): an unused local variable `e` (Phase 1 code,
  reported by pyflakes).

---

## Recorded for completeness — no action needed

Side effects of the review-history fix (PR #6, synthesizer_design.md D26–D27), both checked:
- **4 more reviews (463,011 → 463,015).** Review dates were drawn again within the same
  15 January – 15 March window, so a few people leaving during review season now fall on the
  other side of their exit date. Every review stays in its fiscal year, the ETL linked them all
  (0 rejected), and the documents use the new count.
- **Planted data-quality issues on different rows** (gender codes 979 → 1,027, labels
  6,289 → 6,172; duplicates 500 and resubmissions 1,389 unchanged). This is staging only. The
  ETL fixed every one, matching the manifest exactly, and after cleaning the employee and
  job-history tables are identical to before (table fingerprints).
