# Catdash notes

Decision log / gotchas that git history can't tell you. Read before proposing approaches.

## Per-cat data (2026-10-07)

Account: Satoshi (~11 lb) + Lilybug (~7.5 lb, joined ~Oct 2026), ONE shared
Litter-Robot ("Poodini"), ONE feeder ("Foodboi"). Lilybug is fed by hand, so a cat
can have no feeder at all; the UI then hides the food series.

- **Litter-box activity has no pet field.** `getLitterRobot4Activity` rows are just
  "Pet Weight Recorded: N lbs". Per-cat visits are inferred by matching Whisker's
  curated per-pet readings (`weight_readings`), which are the same weigh-in stamped
  a few seconds later. See `db._attribute_weighins`, tested in `tests/test_pet_attribution.py`.
- **Whisker's curated pet weight history is rolling (~5 days).** On 2026-10-07,
  `fetch_weight_history(limit=5000)` returned only 5–7 readings per cat. Our DB is
  the only long-term copy. Older raw weigh-ins fall back to the nearest weight,
  but only among cats already tracked at that time. Without that guard, a 6.3 lb
  January reading went to Lilybug, who wasn't around yet.
- **Feedings had no feeder_id before this change.** NULL never collides in
  UNIQUE(feeder_id, timestamp, type), so each feeder's first full re-fetch would
  duplicate the legacy rows. `upsert_feedings` instead claims a matching NULL row
  (same timestamp + type). Tried first and dropped: bulk-claiming every NULL row
  for the sole feeder. It still duplicates when 2+ feeders exist at upgrade, and
  it mislabels a replaced unit's history. Hopper snapshots can't be re-fetched,
  so those are still bulk-claimed, and only when there's exactly one feeder.
  Verified on a copy of the real DB: 6445 legacy + 271 new = 6716 rows, 0 NULL, 0 dupes.
- Unassigned feeder = shared (it shows for every cat) rather than "nobody's", so
  the single-cat / never-configured case behaves the same as before.

## Change log
- 2026-10-07 → per-cat feeders + weigh-in attribution → (see `git log --grep=feeder`)
