from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings
from app.schemas import (
    CreateThreadResponse,
    DecisionRequest,
    ErrorResponse,
    MessageRequest,
    Plan,
    SettingsResponse,
    SettingsUpdate,
    TurnResponse,
)
from app.service import (
    AgentService,
    Conflict,
    InvalidSettings,
    ThreadNotFound,
    get_service,
)

app = FastAPI(title="Revenue Leakage Agent", separate_input_output_schemas=False)

# All routes live under /api so the Vite proxy forwards them as-is (no CORS needed).
api = APIRouter(prefix="/api")

Service = Annotated[AgentService, Depends(get_service)]

NOT_FOUND = {404: {"model": ErrorResponse, "description": "The thread does not exist"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "The request does not match the state"}}


@app.exception_handler(ThreadNotFound)
def _not_found(request: Request, exc: ThreadNotFound) -> JSONResponse:
    return JSONResponse({"detail": f"Thread not found: {exc}"}, status_code=404)


@app.exception_handler(Conflict)
def _conflict(request: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(InvalidSettings)
def _invalid(request: Request, exc: InvalidSettings) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


@api.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent_backend": settings.agent_backend}


@api.get("/plans")
def list_plans(service: Service) -> list[Plan]:
    return service.list_plans()


@api.get("/settings")
def get_settings(service: Service) -> SettingsResponse:
    return service.get_settings()


@api.put("/settings", responses=CONFLICT)
def update_settings(update: SettingsUpdate, service: Service) -> SettingsResponse:
    return service.update_settings(update)


@api.post("/threads")
def create_thread(service: Service) -> CreateThreadResponse:
    return service.create_thread()


@api.get("/threads/{thread_id}", responses=NOT_FOUND)
def get_thread(thread_id: str, service: Service) -> TurnResponse:
    return service.get_thread(thread_id)


@api.post("/threads/{thread_id}/messages", responses=NOT_FOUND | CONFLICT)
def send_message(thread_id: str, body: MessageRequest, service: Service) -> TurnResponse:
    return service.send_message(thread_id, body.text)


@api.post("/threads/{thread_id}/decisions", responses=NOT_FOUND | CONFLICT)
def decide(thread_id: str, body: DecisionRequest, service: Service) -> TurnResponse:
    return service.decide(thread_id, body.interrupt_id, body.decision)


@api.get("/graph", response_class=PlainTextResponse)
def graph(service: Service) -> str:
    return service.graph()


app.include_router(api)
