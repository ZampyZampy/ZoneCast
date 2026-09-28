"""
One error shape for the whole API, so the dashboard can show errors in
the viewer's language instead of whatever language the server code was
written in:

    {"detail": {"code": "zones.not_found", "message": "Zone not found.", "params": {...}}}

`code` is stable and looked up by the frontend as t('error.' + code,
params) (see app/static/js/i18n.js); `message` is an English fallback
for API clients and for codes the frontend doesn't know. Technical
details that can't be translated (a command's stderr, an ffmpeg error)
travel in params["detail"] and are appended as-is.
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core import PydanticCustomError
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **params):
        super().__init__(status_code=status_code, detail={"code": code, "message": message, "params": params})


class CodedError(Exception):
    """Base for domain errors raised below the routers (player, multicast
    addressing, bundles...): same code/params contract as AppError, the
    router only picks the HTTP status."""

    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code = code
        self.params = params

    def http(self, status_code: int) -> AppError:
        return AppError(status_code, self.code, str(self), **self.params)


_CODED = "_zonecast_code"


def invalid(code: str, message: str, **params) -> PydanticCustomError:
    """For field/model validators: the frontend reads `code` from the
    first validation error (see the handler below). The marker tells the
    handler this type is one of ours, whatever its prefix."""
    return PydanticCustomError(code, message, {**params, _CODED: True})


def _field(loc) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts) or "request"


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    code = str(first.get("type", ""))
    ctx = first.get("ctx") or {}
    if ctx.get(_CODED):
        params = {k: str(v) for k, v in ctx.items() if k != _CODED}
    else:
        code = "validation.generic"
        params = {"fields": ", ".join(sorted({_field(e.get("loc", ())) for e in errors}))}
    detail = {"code": code, "message": str(first.get("msg", "Invalid request")), "params": params,
              "errors": jsonable_encoder(errors, custom_encoder={Exception: str})}
    return JSONResponse(status_code=422, content={"detail": detail})


async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if not (isinstance(detail, dict) and "code" in detail):
        # Framework errors (unknown route, wrong method...) and anything
        # not converted to AppError yet: same shape, generic code.
        detail = {"code": f"http.{exc.status_code}", "message": str(detail), "params": {}}
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=getattr(exc, "headers", None))


def install(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
