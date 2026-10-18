# Verified Bulgarian sources and what they supply

Checked on **2026-10-18**. This is a record of actual public responses, not a
promise that every endpoint will remain available or that a search-engine answer
will match this application's defined comparison.

## 1. Bulgarian Executive Environment Agency (ИАОС / ExEA)

- [Official national map](https://www.eea.government.bg/kav/)
- Public map data:
  `https://www.eea.government.bg/kav/ajax/getStationsForMapJson?hours=24`
- Also checked the same endpoint with `hours=1`.

Both responses contained **32 official sites**, including Bulgarian names,
station addresses, latitude/longitude and station/area classifications.
The portal itself uses that endpoint.

However, all returned values were zero, with empty timestamps, pollutant names
and display units and zero pollutant identifiers. This is **not evidence of clean
air**. Such records are rejected as measurement candidates.

The endpoint is used here as an attributed factual **location catalog**, not as
a live-reading service. Its station facts were matched one-to-one to EEA IDs by
coordinates; every matched distance was zero metres. No match by guessed street
name or fuzzy city spelling was used.

The local [station catalog](../config/bulgarian-stations.json) is versioned and
dated. It contains identities and location facts, not copied website artwork,
code or an unsupported claim of a licensed independent sensor feed.
Address whitespace is normalized. A monitor's site is not a measurement at every
nearby house, and street-wide coverage is not interpolated.

The portal's certificate chain did not validate with the default certifi bundle.
Windows certificate validation and Python's secure platform certificate context
could retrieve the metadata. TLS verification was **not** disabled. Runtime
searches do not depend on that portal or a network request: the verified catalog
is bundled. Metadata freshness must be considered when sites are renamed/added.

## 2. EEA-delivered Bulgarian official measurements

- [Official download service documentation](https://eeadata.github.io/AQ.Portal/pages/download-data.html)
- [Public API contract](https://eeadmz1-downloads-api-appservice.azurewebsites.net/swagger/index.html)
- [Official station metadata CSV](https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv)

These are the Bulgarian official observations reported through the European
system, not a competing unofficial network. Dataset 2 is validated E1a history;
dataset 1 is provisional E2a. Only PM10 and PM2.5 are currently ingested.

The station metadata CSV is tab-delimited despite its extension. The retrieved
copy had 594 Bulgarian sampling-point rows and 48 physical station IDs; its
reported Last-Modified was 2024-03-11. It supplies the cross-check, not a claim of
complete current station coverage. The 32 national map locations uniquely
matched its station identities at identical coordinates.

Actual measurement files were refreshed separately and contain pollutant codes,
timestamps, units, validity and source revision fields. The application keeps
source provenance and quality flags. Real timestamped source-valid zero
concentrations remain valid values; they are distinguished from blank placeholders.

## 3. Other official pages examined

- [Sofia municipality air portal](https://air.sofia.bg/)
- [Its European AQI page](https://air.sofia.bg/airpublic/air/data/european/index/list/)
- [ИАОС organization on Bulgaria's Open Data portal](https://data.egov.bg/organisation/profile/e3d162cf-10ab-4808-a065-cd5380fbafca)
- The national daily bulletin, which says it publishes **registered exceedances**.

The Sofia European-index page links to the European AQI; this did not establish
an independent complete national historical-measurement API. An exceedance-only
bulletin cannot fairly rank clean locations because silence is not a zero or a
complete measurement series. The Open Data agency page confirms the national
monitoring role but does not itself supply a complete timestamped concentration
feed suitable for this implementation.

No unsupported provider is silently merged, and the project does not claim to
ingest "every possible official source." A future connector needs verified
coverage, units, timestamps, quality, reuse terms and physical-station identity
before it can safely contribute. The same observations republished by multiple
sites must not be counted twice.

## Source versus view

The **Bulgarian official station** and **EEA city-average** settings are different
geographical aggregations of the same official observations. They are not
independent measurement sources. The UI labels this explicitly.

Differences from Google, a public AQI page or an AI answer can come from:

- latest hour versus yesterday versus a multi-month mean;
- PM10 versus PM2.5, or a multi-pollutant index;
- one roadside/background station versus city averages;
- measured data versus modelled/gap-filled estimates;
- source revisions and reporting delays;
- absolute cleanliness versus relative improvement or threshold-day shares.

Collecting more copies of the same station data does not remove those differences.
No AI API, search scraping or generated "measurement" is part of runtime operation.

## Attribution and reuse

Measurement dataset attribution remains EEA / European Commission, CC BY 4.0,
with local filtering/aggregation disclosed. See [THIRD_PARTY.md](THIRD_PARTY.md).
Do not infer that every national/municipal website has the same license as EEA.
The catalog records attributed factual station identities and locations; website
content, logos and code are not incorporated.
