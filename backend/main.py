import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api import audit, batches, datasets, exceptions, health, metrics, reports, rules
from backend.config import get_settings
from backend.errors import DataGuardError
from backend.utils.logging_config import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("dataguard")

app = FastAPI(title=settings.app_name, version="0.6.0")
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(datasets.router, prefix=settings.api_prefix)
app.include_router(batches.router, prefix=settings.api_prefix)
app.include_router(rules.router, prefix=settings.api_prefix)
app.include_router(metrics.router, prefix=settings.api_prefix)
app.include_router(exceptions.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)
app.include_router(audit.router, prefix=settings.api_prefix)


@app.exception_handler(DataGuardError)
async def domain_error_handler(request: Request, exc: DataGuardError) -> JSONResponse:
    body = {"detail": exc.message, "code": exc.kind}
    body.update(exc.details)
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
