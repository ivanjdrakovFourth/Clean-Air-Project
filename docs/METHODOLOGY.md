# Methodology

## The question is explicit

The default question is: **which official Bulgarian monitoring station has the
lowest measured concentration of the selected pollutant for the selected time?**
Lower pollutant concentration means cleaner measured air, not fewer quality points.

The application compares measured locations, not every Bulgarian street.
PM2.5 and PM10 are separate choices; it never substitutes pollutants or combines
unlike concentrations into an undocumented score.

## Official measurement delivery and Bulgarian location detail

- EEA dataset **2**: Bulgaria's validated E1a measurements.
- EEA dataset **1**: Bulgaria's provisional E2a measurements.
- National ИАОС map: station names, addresses, coordinates and station classifications.
- EEA official station metadata: identifier/coordinate cross-check.

All 32 national map sites were uniquely matched to EEA station IDs at the same
coordinates on 2026-10-18. The catalog preserves that verification date.
Measurement data and station metadata have separate provenance.

The national map currently returned zero values with empty pollutant and
timestamp fields. Those are unusable placeholders and are **not measurements**.
The existing EEA feed already distributes the Bulgarian official observations.
The UI therefore offers a station view and the original city-average view,
not a fictional independent direct-national measurement feed.

The versioned catalog is local and works offline. Its names/addresses are a dated
snapshot; renamed/new stations need re-verification, not invented locations.
Details and primary sources are in [DATA_SOURCES.md](DATA_SOURCES.md).

## Two time modes

### Both dates blank: latest comparable reported hour

1. Read real hourly observations from local storage.
2. Require source-valid, finite, nonnegative readings ending no later than now.
3. Deduplicate methods at each physical station/hour by source priority/revision.
4. Find the latest common hour with at least two eligible locations. With an
   explicit single-location filter, one location is allowed and clearly scoped.
5. The hour must end within `current_max_age_hours`, default six hours.
6. Compare the same hour everywhere, and disclose the exact UTC interval.

Daily WHO thresholds are not applied to hourly values. "Current" is a recent
reported hour, not instant sensor streaming. If the source/stored data is too old
or not comparable, return `NO_FRESH_COMPARABLE_HOUR`; never silently reuse stale
values as a current winner.

### Both dates set: inclusive historical UTC days

Validated historical daily values and current hourly values must use matching
windows. Calendar dates are UTC, not Bulgarian local midnight; the owner
explicitly approved this. A daily mean cannot be shifted accurately without its
underlying hourly observations.

The current incomplete UTC day and future days are not accepted as historical
days. A single-day search sets both dates to the same date. One missing date is
an invalid request, not permission to silently choose another period.

## Source normalization and correction rules

- Inventory URLs use the public EEA Parquet API. Verified server date filters
  omitted overlapping current files, so inventory omits those filters and data
  is filtered locally by timestamp.
- Only supported particulate units, aligned one-hour/24-hour intervals and the
  selected pollutants are accepted.
- Invalid/missing/negative concentrations do not enter averages. Some are kept
  in raw storage for traceability; malformed schemas/unknown units fail explicitly.
- A legitimate source-valid timestamped zero remains a zero. It is different
  from a placeholder with no pollutant/time, and is not a safety guarantee.
- Observation identity is sampling point + pollutant + start + aggregation.
- Validated source records supersede provisional ones. Within a source class,
  the latest result timestamp wins, including invalidating corrections.
- Sampling methods at one physical station never receive multiple location weights.

## Daily normalization and historical eligibility

1. Prefer a source-valid daily mean when supplied. Retain its capture information;
   unspecified hourly coverage stays NULL, not 100%.
2. Otherwise average distinct valid UTC hours. Default minimum eligibility is
   18 of 24 hours, or 75%. Missing hours are not zero.
3. For a period, each station needs at least
   `ceil(requested_days * minimum_period_coverage)` eligible daily values;
   default period coverage is 90%.
4. That establishes a qualifying station cohort for the selected period.
5. A station view compares those physical station daily means.
6. A city view averages available qualifying physical stations within each day,
   then gives each valid city day equal weight over the period.
7. A city itself must also satisfy the period-day requirement.

For example, 30 days require 27 qualifying days; 365 days require 329.
Coverage of 100% means every day qualified, not that every hour was observed.
Station types are disclosed but not restricted to background-only, and city
averages are not population-weighted. Remaining gaps and station placement can
bias comparisons.

National station views can show known sites outside the EEA city mapping.
The original city view still excludes unmapped stations and explains exclusions.

## Definitions available in Search settings

| Definition | Primary order | What it does not mean |
|---|---|---|
| Lowest pollution (`average`) | Lowest mean concentration. | Not the single lowest hour in a period unless hour search was selected. |
| Most days within reference (`reference_days`) | Highest percentage of valid daily means at/below the reference; lower mean breaks ties. | Not necessarily the lowest period-average pollution. |
| Largest improvement (`improvement`) | Greatest percentage decrease from earlier stored history; lower current mean breaks ties. | Not necessarily the cleanest absolute air. |

### Earlier-history baseline

The baseline is all locally stored eligible days **before** the selected start,
never the selected period itself. Each contributing physical station needs at
least `baseline_minimum_days`, default 30, of earlier valid data. Both the current
period and baseline use the same qualifying physical-station cohort; daily city
averages retain equal station/day weighting.

`improvement_percent = 100 * (baseline_mean - selected_mean) / baseline_mean`.
The baseline must be positive. Insufficient/zero baselines do not become fabricated
scores. Positive means a reduction; negative means an increase. If all results
are negative, a warning identifies that the first is merely the smallest increase.
This uses stored earlier history, not a universal "all time" climatology.

## Cleanest-day and cleanest-hour searches

The normal target is locations for the selected time. Advanced targets can instead
rank location/day or location/hour combinations within historical dates, optionally
restricted to one explicit station/city.

- Both use the lowest concentration definition, not the incompatible period
  reference-share or improvement objectives.
- The chosen period must establish a qualifying daily station cohort as above.
- For hourly searches, actual hourly data must exist. Only hours from station-days
  with at least the configured 75% distinct-hour coverage contribute.
- Daily historical means cannot reconstruct hours. Lack of actual hourly coverage
  returns an explicit `NO_HOURLY_HISTORY` error.
- Exact winning UTC intervals and the compared location are shown.
- At most the best 50 results are returned and shown, with the full count disclosed.
  Additional ties can fall outside that explicit time-result limit; narrowing dates
  or selecting a location lets them be inspected. Normal location rankings retain
  all tied winners.

## Reference values, ties and reproducibility

| Pollutant | Daily WHO 2021 reference | Annual guideline, not applied to arbitrary periods |
|---|---:|---:|
| PM2.5 | 15 ug/m3 | 5 ug/m3 |
| PM10 | 45 ug/m3 | 15 ug/m3 |

A custom daily reference must be finite and nonnegative. Equality counts as
within-reference. Percentages use valid days as their denominator; requested-day
coverage is shown separately. Under the normal lowest-concentration definition,
changing a threshold changes context statistics, not the winner. Under the
explicit reference-day objective, changing it can change the winner.

WHO daily guidance uses an annual 99th-percentile convention. These descriptive
station/city statistics do not establish WHO or legal compliance or freedom from
health risk. European AQI hourly bands are not attached to period averages.

Unrounded objective values determine rank; absolute tie tolerance is `1e-9`.
Secondary mean tie-breaks apply only where stated. A deterministic policy ID
identifies method/reference settings, not the entire database. Reproduction also
requires the same snapshot, dates and source revisions.

## Storage and operational boundaries

SQLite remains a local, single-writer WAL database with indexed observations and
daily rollups. Schema 1 is backed up before migration to schema 2, which adds
station metadata and an hourly-search index without deleting history. Old archives
remain restorable; new archives retain station metadata too.

Initial backfill covers at least a twelve-month span. Coverage gaps are reported,
not invented away. The app collects while running; searches use only local data.
Records older than 36 calendar months use verified compressed archives.
Automatic archiving still follows successful collection, so prolonged source
failure can delay retention; the explicit archive command remains available.

No AI API is called. Google/AI answers may use different stations, pollutants,
models, periods or definitions; identical results are not guaranteed.

## Primary references

- [Bulgarian national monitoring map](https://www.eea.government.bg/kav/)
- [EEA downloads](https://eeadata.github.io/AQ.Portal/pages/download-data.html)
- [WHO 2021 guidance](https://www.who.int/news-room/feature-stories/detail/what-are-the-who-air-quality-guidelines)
- [Current European AQI](https://airindex.eea.europa.eu/AQI/index.html)
