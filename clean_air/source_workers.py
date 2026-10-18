from __future__ import annotations

import logging
import os
import time

from .collection_notices import NAMES, CollectionNotices
from .collector import Collector
from .credentials import key_path
from .openaq import OpenAQCollector
from .provider_store import PROVIDERS, for_provider
from .settings import Settings

LOGGER = logging.getLogger(__name__)


class SourceWorkers:
    def __init__(self, settings: Settings, metrics=None) -> None:
        self.settings = settings
        self.notices = CollectionNotices(settings)
        self.collectors = {
            "eea": Collector(
                for_provider(settings, "eea"), metrics,
                report=lambda result: self.notices.report("eea", result),
            ),
        }
        if settings.options.openaq.enabled and (
            os.environ.get("OPENAQ_API_KEY") or key_path(settings.root).exists()
        ):
            self.collectors["openaq"] = OpenAQCollector(
                for_provider(settings, "openaq"),
                report=lambda result: self.notices.report("openaq", result),
            )
        elif settings.options.openaq.enabled:
            LOGGER.warning("OpenAQ collection needs a local key; its cached history remains separate and searchable.")

    @property
    def thread(self):
        return self.collectors["eea"].thread

    def available_sources(self) -> list[dict]:
        sources = []
        for provider in PROVIDERS:
            if provider == "bg_direct":
                reason = "No usable direct concentration feed is available. Saved direct-source archives, if any, are not replaced."
            elif not self.settings.options.collection.enabled:
                reason = "Explicit offline mode: no network collection is attempted."
            elif provider == "openaq" and not self.settings.options.openaq.enabled:
                reason = "OpenAQ collection is disabled in this installation."
            elif provider not in self.collectors:
                reason = "Configure a local OpenAQ key in Monitoring to enable automatic updates. Saved history is still searchable."
            else:
                reason = ""
            sources.append({"id": provider, "name": NAMES[provider], "enabled": not reason, "reason": reason})
        return sources

    def start(self) -> None:
        for collector in self.collectors.values():
            collector.start()

    def stop(self) -> None:
        for collector in self.collectors.values():
            collector.stop_event.set()
            collector.wake_event.set()
        deadline = time.monotonic() + 10
        for collector in self.collectors.values():
            if collector.thread:
                collector.thread.join(timeout=max(0, deadline - time.monotonic()))
                if collector.thread.is_alive():
                    LOGGER.warning("A source request is finishing; its committed checkpoints are safe.")

    def states(self) -> dict[str, bool]:
        return {
            provider: bool(collector.thread and collector.thread.is_alive())
            for provider, collector in self.collectors.items()
        }

    def refresh(self, settings: Settings, provider: str) -> str:
        if not settings.options.collection.enabled:
            raise ValueError("This installation is in offline mode; start it without --offline before collecting.")
        if provider == "openaq" and not settings.options.openaq.enabled:
            raise ValueError("OpenAQ collection is disabled in this installation.")
        if provider == "bg_direct":
            raise ValueError("No verified direct Bulgarian measurement connector is available.")
        if provider not in self.collectors:
            if provider != "openaq" or not (
                os.environ.get("OPENAQ_API_KEY") or key_path(settings.root).exists()
            ):
                raise ValueError("Configure the source credential before requesting collection.")
            self.collectors[provider] = OpenAQCollector(
                for_provider(settings, "openaq"),
                report=lambda result: self.notices.report("openaq", result),
            )
            self.notices.sources = self.available_sources()
            self.collectors[provider].start()
        else:
            collector = self.collectors[provider]
            if not collector.thread or not collector.thread.is_alive():
                raise ValueError("The source worker is stopped. Restart Clean Air Finder before requesting a refresh.")
            self.collectors[provider].wake_event.set()
        return "Refresh requested; an active collection is not duplicated and API limits remain enforced."
