from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import SecretStr

from .alerts import AlertRule, alert_connection, change_rule, rules_and_events, save_rule
from .comparison import ComparisonError, status_snapshot
from .credentials import save_openaq_key
from .data_explorer import TABLES, DataQuery, browse, csv_rows, display_cell
from .health import prometheus_summary, source_health
from .maintenance import JobRequest, job_list, submit_job
from .provider_store import PROVIDER_NAMES
from .settings import POLLUTANTS, Pollutant, Provider, Settings, StrictModel, timestamp_now


class CredentialRequest(StrictModel):
    api_key: SecretStr


class ToggleRequest(StrictModel):
    enabled: bool


def local_action(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in (
        f"http://127.0.0.1:{settings.options.app.port}",
        f"http://localhost:{settings.options.app.port}",
    ):
        raise ComparisonError("LOCAL_ACTION_ONLY", "This action requires the application's own local page.", 403)


def attach_ui(app: FastAPI, settings: Settings, templates, workers, alerts, maintenance) -> None:
    templates.env.globals.update(provider_names=PROVIDER_NAMES, pollutant_names=list(POLLUTANTS))
    templates.env.filters["cell"] = display_cell

    @app.get("/api/collection-notices", tags=["Monitoring"])
    def collection_notices(after: int = Query(default=0, ge=0)):
        return workers.notices.status(after)

    @app.post("/api/settings/collection-desktop", tags=["Monitoring"])
    def collection_desktop(payload: ToggleRequest, request: Request):
        local_action(request, settings)
        try:
            workers.notices.set_desktop(payload.enabled)
        except ValueError as error:
            raise ComparisonError("NOTIFICATION_SETTING_UNAVAILABLE", str(error), 409) from error
        return {"enabled": payload.enabled, "message": "Automatic source-update desktop notification preference saved locally."}

    @app.get("/monitoring", include_in_schema=False)
    def monitoring_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="monitoring.html",
            context={"settings": settings},
        )

    @app.get("/api/monitoring", tags=["Monitoring"])
    def monitoring_data(
        pollutant: Pollutant = "PM10",
        window: Literal["15m", "1h", "24h", "7d"] = "1h",
    ):
        worker_states = workers.states()
        return {
            "health": source_health(settings, pollutant, worker_states=worker_states),
            "prometheus": prometheus_summary(settings, window),
            "workers": worker_states,
            "jobs": job_list(settings),
        }

    @app.post("/api/sources/{source}/refresh", tags=["Monitoring"], status_code=202)
    def refresh_source(source: Provider, request: Request):
        local_action(request, settings)
        try:
            return {"message": workers.refresh(settings, source)}
        except ValueError as error:
            raise ComparisonError("REFRESH_NOT_AVAILABLE", str(error), 409) from error

    @app.post("/api/settings/openaq-key", tags=["Monitoring"])
    def configure_key(payload: CredentialRequest, request: Request):
        local_action(request, settings)
        try:
            save_openaq_key(settings.root, payload.api_key.get_secret_value())
            message = (
                workers.refresh(settings, "openaq")
                if settings.options.collection.enabled and settings.options.openaq.enabled
                else "Collection is disabled/offline; start online to collect this source."
            )
        except (ValueError, RuntimeError, OSError) as error:
            raise ComparisonError(
                "CREDENTIAL_SETUP_FAILED",
                "Could not configure the local OpenAQ credential. Check the Windows user/session and logs.",
                503,
            ) from error
        return {
            "configured": True,
            "message": "Credential saved encrypted for this Windows user and excluded from handovers. "
            + message + " An OPENAQ_API_KEY environment variable, if set, takes precedence.",
        }

    @app.get("/data", include_in_schema=False)
    def data_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="data.html",
            context={"settings": settings, "tables": TABLES},
        )

    @app.get("/api/data", tags=["Data explorer"])
    def table_data(query: Annotated[DataQuery, Query()]):
        return browse(settings, query)

    @app.get("/api/data/export", tags=["Data explorer"])
    def export_data(query: Annotated[DataQuery, Query()]):
        return StreamingResponse(
            csv_rows(settings, query), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{query.source}-{query.table}.csv"'},
        )

    @app.post("/api/jobs", tags=["Data maintenance"], status_code=202)
    def create_job(payload: JobRequest, request: Request):
        local_action(request, settings)
        try:
            identity = submit_job(settings, payload)
        except ValueError as error:
            raise ComparisonError("INVALID_DATA_JOB", str(error), 422) from error
        return {"id": identity, "message": "Operation queued. Inspect its status; completion is not assumed."}

    @app.get("/api/jobs", tags=["Data maintenance"])
    def jobs():
        return {"jobs": job_list(settings)}

    @app.get("/api/jobs/{identity}/download", tags=["Data maintenance"])
    def download_backup(identity: int):
        matches = [row for row in job_list(settings) if row["id"] == identity]
        if not matches or matches[0]["status"] != "completed" or matches[0]["request"]["action"] != "backup":
            raise ComparisonError("BACKUP_NOT_READY", "This backup has not completed successfully.", 404)
        path = Path(matches[0]["artifact"]).resolve()
        if not path.is_relative_to((settings.var / "backups").resolve()) or not path.is_file():
            raise ComparisonError("BACKUP_UNAVAILABLE", "The verified backup file is no longer available.", 404)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.get("/alerts", include_in_schema=False)
    def alerts_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="alerts.html", context={"settings": settings},
        )

    @app.get("/api/alerts", tags=["Air-quality alerts"])
    def alert_data(after: int = Query(default=0, ge=0)):
        data = rules_and_events(settings, after)
        data["source_status"] = status_snapshot(settings)["provider_statuses"]
        return data

    @app.post("/api/alerts", tags=["Air-quality alerts"], status_code=201)
    def create_alert(payload: AlertRule, request: Request):
        local_action(request, settings)
        try:
            return {"id": save_rule(settings, payload), "message": "Alert saved. A first baseline is recorded before change notifications."}
        except ValueError as error:
            raise ComparisonError("ALERT_NOT_SAVED", str(error)) from error

    @app.put("/api/alerts/{identity}", tags=["Air-quality alerts"])
    def edit_alert(identity: int, payload: AlertRule, request: Request):
        local_action(request, settings)
        try:
            return {"id": save_rule(settings, payload, identity), "message": "Alert updated; previous criteria baselines were reset."}
        except ValueError as error:
            raise ComparisonError("ALERT_NOT_SAVED", str(error)) from error

    @app.post("/api/alerts/{identity}/enabled", tags=["Air-quality alerts"])
    def enable_alert(identity: int, payload: ToggleRequest, request: Request):
        local_action(request, settings)
        try:
            change_rule(settings, identity, enabled=payload.enabled)
        except ValueError as error:
            raise ComparisonError("ALERT_NOT_FOUND", str(error), 404) from error
        return {"message": "Alert state updated; it will establish a fresh baseline."}

    @app.delete("/api/alerts/{identity}", tags=["Air-quality alerts"])
    def delete_alert(identity: int, request: Request):
        local_action(request, settings)
        try:
            change_rule(settings, identity, delete=True)
        except ValueError as error:
            raise ComparisonError("ALERT_NOT_FOUND", str(error), 404) from error
        return {"message": "Rule removed. Existing alert history was retained."}

    @app.post("/api/alerts/events/{identity}/read", tags=["Air-quality alerts"])
    def read_alert(identity: int, request: Request):
        local_action(request, settings)
        with alert_connection(settings) as connection, connection:
            if not connection.execute(
                "UPDATE alert_events SET unread=0 WHERE id=?", (identity,)
            ).rowcount:
                raise ComparisonError("EVENT_NOT_FOUND", "This alert event does not exist.", 404)
        return {"message": "Event marked read."}

    @app.post("/api/alerts/test-desktop", tags=["Air-quality alerts"], status_code=202)
    def test_desktop(request: Request):
        local_action(request, settings)
        if not settings.options.alerts.enabled:
            raise ComparisonError("ALERTS_DISABLED", "Alert evaluation/delivery is disabled in this installation.", 409)
        with alert_connection(settings) as connection, connection:
            identity = connection.execute(
                "INSERT INTO alert_events(source,created,title,message,details,event_key,desktop_status) "
                "VALUES (?,?,?,?,?,?,'pending')",
                ("application", timestamp_now(), "Clean Air Finder test notification",
                 "You requested this desktop test. Real events also remain in the in-app alert history.",
                 json.dumps({"kind": "user_requested_test"}), uuid.uuid4().hex),
            ).lastrowid
        return {"id": identity, "message": "Test queued. Windows can suppress popups; inspect the delivery status in the event history."}
