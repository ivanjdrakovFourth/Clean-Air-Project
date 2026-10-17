# Methodology

## Question and scope

The application ranks **eligible measured Bulgarian cities** by the lowest
period-average concentration of one selected pollutant. It cannot establish the
cleanest city in the entire country where no comparable measurements exist.

The selected pollutant is explicit: PM2.5 or PM10. The application never
substitutes a pollutant or averages unlike pollutant concentrations into an
undocumented composite score.

## Source

Measurements come from the public EEA Parquet download API:

- Dataset selector **2**: validated E1a history.
- Dataset selector **1**: provisional E2a current observations.

The current-data container is named `airquality-p`, not `airquality-p-e2a`.
Official city-to-sampling-point mappings come from the same service. Sampling
points are grouped by the physical Bulgarian station identifier.

An actual probe found that server-side start/end filters returned an empty
current-data inventory despite available overlapping files. Accordingly,
inventory requests omit date filters; the application downloads the selected
Bulgarian pollutant files and filters timestamps locally. Transient source files
are removed after processing. Real application data never comes from test fixtures.

Only recognized PM units, one-hour or 24-hour intervals aligned to UTC boundaries,
and source-valid nonnegative finite concentrations contribute. Invalid and
missing in-window values can remain in raw storage for traceability but do not
enter averages. Unsupported rows are counted and logged. Unknown units or
malformed file schemas fail that file explicitly.

## Calendar days

Validated Bulgarian history includes daily averages timestamped from 00:00 to
00:00 UTC. Current files contain hourly values. The project owner explicitly
approved **UTC calendar days** so both use the same averaging windows.

User date strings are treated as dates, never converted through browser local
time. End dates are inclusive. The current, incomplete UTC day and future dates
are not queryable. Bulgarian local days are offset by two or three hours; daily
means cannot be accurately shifted without the underlying hourly measurements.

## Daily normalization and source precedence

1. Store observations keyed by sampling point, pollutant, start and aggregation.
2. Validated records supersede provisional records for the same key. Within one
   source class, the latest source result timestamp wins, including invalidating
   revisions.
3. For a physical station/day, prefer a valid source-reported daily mean.
   Underlying hourly coverage stays `null` when the provider does not specify it.
   A provider-valid daily value is not misrepresented as 24 observed hours.
4. Otherwise, use one value per distinct UTC hour. Prefer the stronger source
   class and latest revision when sampling methods overlap.
5. Compute the hourly-derived station daily mean using observed valid hours.
   Default eligibility requires at least 18 of 24 hours. Missing hours are not zero.
6. Sampling methods at the same physical station never receive independent
   city-level weight.

## Station and city eligibility

For the requested inclusive period:

- Apply the hourly/daily coverage rule.
- A station must provide eligible daily values for at least
  `ceil(requested_days * minimum_period_coverage)` days, default 90%.
- This establishes a qualifying station cohort for the selected period.
- For each date, the city mean is the arithmetic mean of the available qualifying
  physical station means.
- The period city mean is the arithmetic mean of eligible city daily means.
- Require the city itself to meet the period-coverage rule.

Every available qualifying station has equal daily weight, and every valid day
has equal period weight. Additional stations do not give a city additional
country-ranking weight. Missing days do not enter the mean or become zeros.

The city mapping includes different station types. It is **not** restricted to
urban-background stations and is not population-weighted. Station placement,
remaining gaps and differing valid-day sets can bias comparisons. These are
explicit limitations, not guarantees of representative personal exposure.

Stations without an official city mapping are retained locally but excluded.
Cities without eligible observations remain visible in the excluded list.

## Winner and ties

The lowest unrounded period mean wins. Ties use absolute tolerance
`1e-9 ug/m3`, with no relative tolerance. All tied winners are returned. Display
rounding never determines the winner.

## Configurable daily reference

WHO 2021 references:

| Pollutant | 24-hour reference | Annual guideline, not applied to arbitrary periods |
|---|---:|---:|
| PM2.5 | 15 ug/m3 | 5 ug/m3 |
| PM10 | 45 ug/m3 | 15 ug/m3 |

A custom reference must be finite and nonnegative. A day is descriptively within
the selected reference when its **city daily mean is less than or equal to** it.
The denominator for percentages is the number of valid city days, not all
requested days. Coverage is displayed separately.

Changing a threshold changes the reference statistics, not the ranking formula.
Changing the pollutant can change the winner. Profile and methodology settings
are returned with a deterministic policy identifier. The last complete import
time and newest observed timestamp provide data-version context.

WHO daily recommendations use an annual 99th-percentile convention, approximately
3-4 exceedance days per year. City averages, short periods and descriptive
exceedance counts **do not establish WHO or legal compliance**. A low mean can
also hide individual high-pollution days. No result guarantees an absence of risk.

The revised European AQI is an **hourly** index. Its colour bands are deliberately
not applied to arbitrary historical period averages.

## Storage and retention

SQLite is an intentional single-host choice: transactions, indexed local queries,
WAL readers alongside one writer, short import transactions, duplicate-safe
upserts and built-in consistent backup. Daily rollups avoid scanning raw hourly
measurements for each search.

Backfill covers at least twelve months; incremental imports extend retained
history. Records older than 36 calendar months are compressed into checksummed
archives, read back and counted before active records are removed. Incremental
vacuum and WAL checkpointing reclaim available storage. Restoration is explicit,
verified and pauses automatic archiving for 24 hours.

## References

- [EEA downloads](https://eeadata.github.io/AQ.Portal/pages/download-data.html)
- [WHO 2021 guidelines and table](https://www.who.int/news-room/feature-stories/detail/what-are-the-who-air-quality-guidelines)
- [Current European Air Quality Index](https://airindex.eea.europa.eu/AQI/index.html)
- [Revised European AQI methodology](https://www.eionet.europa.eu/etcs/etc-he/products/etc-he-products/etc-he-reports/etc-he-report-2024-17-eeas-revision-of-the-european-air-quality-index-bands)
