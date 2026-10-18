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

**Changes made:** selection of Bulgarian PM2.5/PM10 measurements, validation,
unit/interval normalization, city mapping, daily aggregation, coverage filtering
and period ranking. These derived outputs are not official EEA assessments and
do not imply endorsement by the EEA or European Commission.

The public dataset record was inspected during implementation and identifies
CC BY 4.0. Recheck source terms before adding another dataset or provider; the
EEA's general site policy has third-party and item-specific exceptions.

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
