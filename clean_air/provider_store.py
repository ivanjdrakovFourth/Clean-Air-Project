from __future__ import annotations

from dataclasses import replace
from datetime import date

from .database import connect, get_metadata, initialize, set_metadata
from .settings import Provider, Settings, months_before, utc_today
from .station_catalog import apply_catalog

PROVIDERS: tuple[Provider, ...] = ("eea", "openaq", "bg_direct")
PROVIDER_NAMES = {
    "eea": "EEA - Bulgarian official observations",
    "openaq": "OpenAQ - source-labelled monitors and sensors",
    "bg_direct": "Bulgarian national portal - direct CSV observations",
}
BACKFILL_KEYS = {"openaq": "openaq_backfill_complete", "bg_direct": "bg_direct_backfill_complete"}


def for_provider(settings: Settings, provider: Provider) -> Settings:
    return replace(settings, provider=provider)


def collection_start(settings: Settings, months: int) -> date:
    configured = months_before(utc_today(), months)
    with connect(settings.database) as connection:
        previous = get_metadata(connection, "history_requested_start")
    if previous is None:
        return configured
    if not isinstance(previous, str):
        raise ValueError("The source's stored historical start date is invalid; inspect its collection metadata.")
    # Preserve the original floor so a long pause cannot leave a hole before a moving window.
    return min(configured, date.fromisoformat(previous))


def prepare_sources(settings: Settings) -> None:
    from .seeds import merge_seed

    for provider in PROVIDERS:
        configured = for_provider(settings, provider)
        configured.prepare_directories()
        initialize(configured.database)
        with connect(configured.database) as connection, connection:
            existing = get_metadata(connection, "data_provider", None)
            if existing is not None and existing != provider:
                raise RuntimeError("A source database belongs to another provider; source mixing was refused.")
            set_metadata(connection, "data_provider", provider)
        if provider == "eea":
            apply_catalog(configured.database, configured.root / "config" / "bulgarian-stations.json")
        merge_seed(configured)
