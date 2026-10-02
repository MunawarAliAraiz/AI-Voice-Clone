"""Desktop-only, explicitly requested agent connection configuration."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ...agents.configure import AgentConfigError, AgentConnections
from ...config import Settings
from ..deps import get_settings

router = APIRouter(prefix="/agents", tags=["agents"])


def _connections(settings: Settings) -> AgentConnections:
    if settings.desktop_static_dir is None:
        raise HTTPException(404, "Agent configuration is available in the desktop app")
    return AgentConnections(settings.data_dir)


class ConnectInput(BaseModel):
    replace_existing: bool = False


class ActivityInput(BaseModel):
    tool: str = Field(min_length=1, max_length=128)


@router.get("")
def connections(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return _connections(settings).status()


@router.post("/configure/{client}")
def configure_client(
    client: str,
    body: ConnectInput,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    try:
        return _connections(settings).configure(client, replace_existing=body.replace_existing)
    except AgentConfigError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Cannot safely save agent configuration") from exc


@router.post("/activity", status_code=204)
def mcp_activity(
    body: ActivityInput, settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    try:
        _connections(settings).record_activity(body.tool)
    except OSError as exc:
        raise HTTPException(503, "Cannot save local MCP activity") from exc
    return Response(status_code=204)
