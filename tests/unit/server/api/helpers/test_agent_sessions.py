from strawberry.relay import GlobalID

from phoenix.db import models
from phoenix.db.types.agent_session_config import AgentBuiltinProviderConfig
from phoenix.db.types.model_provider import ModelProvider
from phoenix.server.agents.model_selection import (
    BuiltInProviderModelSelection,
    CustomProviderModelSelection,
)
from phoenix.server.api.helpers.agent_sessions import (
    get_agent_session_model,
    stamp_session_model,
)
from phoenix.server.encryption import EncryptionService
from phoenix.server.types import DbSessionFactory


async def _add_custom_provider(
    db: DbSessionFactory,
) -> tuple[int, str]:
    async with db() as session:
        provider = models.GenerativeModelCustomProvider(
            name="Custom OpenAI",
            provider="openai",
            sdk="openai",
            config=EncryptionService().encrypt(b"{}"),
        )
        session.add(provider)
        await session.flush()
        return provider.id, str(
            GlobalID(models.GenerativeModelCustomProvider.__name__, str(provider.id))
        )


async def _add_builtin_session(db: DbSessionFactory) -> int:
    async with db() as session:
        agent_session = models.AgentSession(
            project_name="assistant_agent",
            title="Model persistence",
            model_provider=ModelProvider.ANTHROPIC,
            model_name="claude-opus-4-6",
            builtin_provider=AgentBuiltinProviderConfig(),
        )
        session.add(agent_session)
        await session.flush()
        return agent_session.id


async def test_stamp_session_model_transitions_between_routing_modes(
    db: DbSessionFactory,
) -> None:
    provider_id, provider_gid = await _add_custom_provider(db)
    agent_session_id = await _add_builtin_session(db)

    async with db() as session:
        agent_session = await session.get(models.AgentSession, agent_session_id)
        assert agent_session is not None
        await stamp_session_model(
            session,
            agent_session=agent_session,
            model=CustomProviderModelSelection(
                provider_type="custom",
                provider_id=provider_gid,
                model_name="custom-model",
            ),
        )
        await session.flush()
        assert agent_session.model_provider is ModelProvider.OPENAI
        assert agent_session.model_name == "custom-model"
        assert agent_session.custom_provider_id == provider_id
        assert agent_session.builtin_provider is None

        await stamp_session_model(
            session,
            agent_session=agent_session,
            model=BuiltInProviderModelSelection(
                provider_type="builtin",
                provider=ModelProvider.AZURE_OPENAI,
                model_name="gpt-5.5",
                openai_api_type="chat_completions",
            ),
        )
        await session.flush()
        assert agent_session.model_provider.value == "AZURE_OPENAI"
        assert agent_session.model_name == "gpt-5.5"
        assert agent_session.custom_provider_id is None
        assert agent_session.builtin_provider == AgentBuiltinProviderConfig(
            openai_api_type="chat_completions"
        )


async def test_stamp_custom_model_populates_a_pending_session_before_flush(
    db: DbSessionFactory,
) -> None:
    provider_id, provider_gid = await _add_custom_provider(db)

    async with db() as session:
        agent_session = models.AgentSession(
            project_name="assistant_agent",
            title="Pending custom model",
        )
        session.add(agent_session)
        await stamp_session_model(
            session,
            agent_session=agent_session,
            model=CustomProviderModelSelection(
                provider_type="custom",
                provider_id=provider_gid,
                model_name="custom-model",
            ),
        )
        await session.flush()

        assert agent_session.model_provider is ModelProvider.OPENAI
        assert agent_session.model_name == "custom-model"
        assert agent_session.custom_provider_id == provider_id
        assert agent_session.builtin_provider is None


async def test_deleted_custom_provider_reads_as_builtin_fallback(
    db: DbSessionFactory,
) -> None:
    provider_id, provider_gid = await _add_custom_provider(db)
    agent_session_id = await _add_builtin_session(db)

    async with db() as session:
        agent_session = await session.get(models.AgentSession, agent_session_id)
        assert agent_session is not None
        await stamp_session_model(
            session,
            agent_session=agent_session,
            model=CustomProviderModelSelection(
                provider_type="custom",
                provider_id=provider_gid,
                model_name="custom-model",
            ),
        )
        await session.flush()
        provider = await session.get(models.GenerativeModelCustomProvider, provider_id)
        assert provider is not None
        await session.delete(provider)
        await session.flush()
        await session.refresh(agent_session)

        model, custom_provider_deleted = get_agent_session_model(agent_session)
        assert custom_provider_deleted is True
        assert model == BuiltInProviderModelSelection(
            provider_type="builtin",
            provider=ModelProvider.OPENAI,
            model_name="custom-model",
        )
