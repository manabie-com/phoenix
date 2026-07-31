from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.relay import GlobalID
from typing_extensions import assert_never

from phoenix.db import models
from phoenix.db.types.agent_session_config import AgentBuiltinProviderConfig
from phoenix.db.types.model_provider import model_provider_from_generative_model_sdk
from phoenix.server.agents.exceptions import ProviderNotFoundError
from phoenix.server.agents.model_selection import (
    AgentModelSelection,
    BuiltInProviderModelSelection,
    CustomProviderModelSelection,
)
from phoenix.server.api.types.node import from_global_id_with_expected_type

TURN_LOCK_STALENESS = timedelta(seconds=60)
"""How long after its last heartbeat a turn lock is considered abandoned."""


def get_otel_session_id(*, project_name: str, agent_session_rowid: int) -> str:
    agent_session_gid = GlobalID(type_name="AgentSession", node_id=str(agent_session_rowid))
    return f"{project_name}:{agent_session_gid}"


def is_turn_active(heartbeat_at: Optional[datetime], *, now: datetime) -> bool:
    """Whether a turn with a live (non-stale) heartbeat holds the session's lock.

    Shared by the REST session read and the GraphQL ``AgentSession`` type so
    every surface derives the busy state from one definition.
    """
    return heartbeat_at is not None and heartbeat_at >= now - TURN_LOCK_STALENESS


async def stamp_session_model(
    session: AsyncSession,
    *,
    agent_session: models.AgentSession,
    model: AgentModelSelection,
) -> None:
    """Persist the model selected for a session.

    This is the sole writer for the four model-routing columns, ensuring each
    transition is emitted as one UPDATE and always satisfies the routing CHECK.
    """
    if isinstance(model, CustomProviderModelSelection):
        try:
            provider_id = from_global_id_with_expected_type(
                GlobalID.from_id(model.provider_id),
                models.GenerativeModelCustomProvider.__name__,
            )
        except ValueError as exc:
            raise ProviderNotFoundError("Custom provider not found.") from exc
        # A newly created AgentSession is already pending when this helper is
        # called. The provider lookup must not autoflush that incomplete row
        # before the four routing columns below are populated.
        with session.no_autoflush:
            provider = await session.get(models.GenerativeModelCustomProvider, provider_id)
        if provider is None:
            raise ProviderNotFoundError("Custom provider not found.")
        agent_session.model_provider = model_provider_from_generative_model_sdk(provider.sdk)
        agent_session.model_name = model.model_name
        agent_session.custom_provider_id = provider.id
        agent_session.builtin_provider = None
        return
    if isinstance(model, BuiltInProviderModelSelection):
        agent_session.model_provider = model.provider
        agent_session.model_name = model.model_name
        agent_session.custom_provider_id = None
        agent_session.builtin_provider = AgentBuiltinProviderConfig(
            openai_api_type=model.openai_api_type
        )
        return
    assert_never(model)


def get_agent_session_model(
    agent_session: models.AgentSession,
) -> tuple[AgentModelSelection, bool]:
    """Return the persisted selection and whether its custom provider was deleted."""
    if agent_session.builtin_provider is not None:
        return (
            BuiltInProviderModelSelection(
                provider_type="builtin",
                provider=agent_session.model_provider,
                model_name=agent_session.model_name,
                openai_api_type=agent_session.builtin_provider.openai_api_type,
            ),
            False,
        )
    if agent_session.custom_provider_id is not None:
        return (
            CustomProviderModelSelection(
                provider_type="custom",
                provider_id=str(
                    GlobalID(
                        models.GenerativeModelCustomProvider.__name__,
                        str(agent_session.custom_provider_id),
                    )
                ),
                model_name=agent_session.model_name,
            ),
            False,
        )
    return (
        BuiltInProviderModelSelection(
            provider_type="builtin",
            provider=agent_session.model_provider,
            model_name=agent_session.model_name,
        ),
        True,
    )
