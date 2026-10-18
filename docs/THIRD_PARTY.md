# Attribution and third-party notices

## Air-quality data

Source: **European Environment Agency, Air Quality e-Reporting**, based on
measurements reported by participating countries, filtered here to Bulgaria.

Copyright holder identified by the dataset record: **European Commission**.
License: **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

- [Dataset and license record](https://www.eea.europa.eu/en/datahub/datahubitem-view/3b390c9c-f321-490a-b25a-ae93b2ed80c1)
- [Official download documentation](https://eeadata.github.io/AQ.Portal/pages/download-data.html)
- [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/)
- [EEA general reuse policy and exceptions](https://www.eea.europa.eu/en/legal-notice)

**Changes made:** selection of supported Bulgarian particulate and gas measurements, validation,
unit/interval normalization, city mapping, daily aggregation, coverage filtering
and period ranking. These derived outputs are not official EEA assessments and
do not imply endorsement by the EEA or European Commission.

The public dataset record was inspected during implementation and identifies
CC BY 4.0. Recheck source terms before adding another dataset or provider; the
EEA's general site policy has third-party and item-specific exceptions.

## OpenAQ and original upstream data owners

[OpenAQ](https://openaq.org/) is a separate delivery source, not the owner of
every observation it redistributes. Per-location provider, owner, license name,
URL, permission fields and effective date windows returned by its
[license API](https://docs.openaq.org/resources/licenses) are retained in each
source database's `station_provider:*` metadata and in source-aware archives.
They can be inspected in the Data Explorer.

The verified catalog includes Open Data Commons Attribution (ODC-BY) and
Creative Commons Attribution licenses. Keep the actual location-specific
notices with redistributed databases and derived results; do not replace every
owner's terms with one assumed OpenAQ license.

- [Open Data Commons Attribution License 1.0](https://opendatacommons.org/licenses/by/1-0/)
- [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/)

Only established modification/redistribution permissions are imported.
Changes are disclosed: Bulgaria/support/quality filtering, mass-unit and UTC-hour
normalization, local daily aggregation, coverage filtering and source-specific
rankings. No OpenAQ/owner endorsement is implied. EEA-origin OpenAQ monitors
retain EEA provenance rather than being described as independent confirmation.

API credentials are not part of the data license. No key is redistributed with
the repository or portable package. Recipients need their own local credential
for future OpenAQ collection, not for queries of the prefilled measurements.

## Bulgarian official station location facts

The bundled catalog records 32 attributed station names, site addresses,
coordinates and classifications from the Bulgarian Executive Environment Agency:
[official national map](https://www.eea.government.bg/kav/). Station IDs were
cross-checked against EEA official station metadata at identical coordinates.
The factual catalog was verified on 2026-10-18 and normalizes address whitespace.

This does not incorporate the national website's code, graphics or logos, and
does not assert that the entire website has the EEA dataset's CC BY license.
The map's unlabelled zero placeholders were not imported as measurements.
See [DATA_SOURCES.md](DATA_SOURCES.md) for actual responses and provenance.
Measurement attribution remains separate from location attribution.

## Guideline facts

Daily and annual reference values are attributed to the
[WHO Global Air Quality Guidelines, 2021](https://www.who.int/news-room/feature-stories/detail/what-are-the-who-air-quality-guidelines).
They are used as factual, explicitly labelled references, not as a medical
recommendation or compliance certification.

## Bundled software

- **CPython 3.13.16**: Python Software Foundation License. Original runtime
  license and copyright notices remain in `runtime\python`.
- **Prometheus 3.15.0**: Apache License 2.0. Original `LICENSE` and `NOTICE`
  remain in `runtime\prometheus`.
- **Python packages**, including FastAPI, Starlette, Uvicorn, HTTPX, Jinja2,
  Pydantic, Apache Arrow, the Prometheus client and development tools: original
  license files remain in each package's `.dist-info` metadata directory under
  `runtime\packages`. Native dependency notices supplied by the wheels are retained.

`runtime\manifest.json` records exact component/package versions, download URLs,
SHA-256 hashes and reported license metadata. `requirements.lock` pins the
Windows x64 / CPython 3.13 wheels. The builder verifies official binary checksums
before extraction and wheel hashes during a locked rebuild.

The application uses original HTML/CSS/SVG artwork and no remotely hosted fonts,
images, JavaScript libraries or analytics.

The original assignment images are preserved in `The Task` as user-supplied
project context; no public redistribution rights to those images are asserted.
Project source ownership/licensing remains a decision for the project owner;
third-party licenses are not changed by inclusion in this workspace.
