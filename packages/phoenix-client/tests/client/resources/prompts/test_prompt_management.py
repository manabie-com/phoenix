"""Tests for the fork-owned prompt list / versions / delete methods."""

from typing import Any

import httpx
import pytest

from phoenix.client.resources.prompts import AsyncPrompts, Prompts
from phoenix.client.types.prompts import PromptVersion


def _prompt(id: str = "UHJvbXB0OjE", name: str = "my-prompt") -> dict[str, Any]:
    return {"id": id, "name": name}


def _version(
    id: str = "UHJvbXB0VmVyc2lvbjox", text: str = "Write about {{topic}}"
) -> dict[str, Any]:
    return {
        "id": id,
        "model_provider": "OPENAI",
        "model_name": "gpt-4o",
        "template": {
            "type": "chat",
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        },
        "template_type": "CHAT",
        "template_format": "MUSTACHE",
        "invocation_parameters": {"type": "openai", "openai": {}},
    }


def _sync(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def _async(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


class TestList:
    def test_returns_every_prompt(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/prompts"
            return httpx.Response(
                200, json={"data": [_prompt(name="a"), _prompt(name="b")], "next_cursor": None}
            )

        result = Prompts(_sync(handler)).list()
        assert [p["name"] for p in result] == ["a", "b"]

    def test_follows_pagination(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    200, json={"data": [_prompt(name="a")], "next_cursor": "cursor-2"}
                )
            return httpx.Response(200, json={"data": [_prompt(name="b")], "next_cursor": None})

        result = Prompts(_sync(handler)).list()

        assert [p["name"] for p in result] == ["a", "b"]
        assert "cursor" not in calls[0].url.params
        assert calls[1].url.params.get("cursor") == "cursor-2"

    def test_no_prompts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [], "next_cursor": None})

        assert Prompts(_sync(handler)).list() == []


class TestVersions:
    def test_returns_rich_prompt_versions(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/prompts/my-prompt/versions"
            return httpx.Response(200, json={"data": [_version()], "next_cursor": None})

        result = Prompts(_sync(handler)).versions(prompt_identifier="my-prompt")

        assert len(result) == 1
        # The same rich object `get()` returns, not a raw TypedDict.
        assert isinstance(result[0], PromptVersion)
        assert result[0].id == "UHJvbXB0VmVyc2lvbjox"
        content = result[0].messages[0]["content"]
        assert not isinstance(content, str)
        assert content[0]["text"] == "Write about {{topic}}"  # type: ignore[typeddict-item]

    def test_follows_pagination(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    200, json={"data": [_version(id="v1")], "next_cursor": "cursor-2"}
                )
            return httpx.Response(200, json={"data": [_version(id="v2")], "next_cursor": None})

        result = Prompts(_sync(handler)).versions(prompt_identifier="my-prompt")

        assert [v.id for v in result] == ["v1", "v2"]
        assert calls[1].url.params.get("cursor") == "cursor-2"

    def test_an_unknown_prompt_comes_back_empty_not_404(self) -> None:
        """What the server actually does: `list_prompt_versions` filters by
        identifier and has no not-found path, so a wrong identifier is an empty
        page. Documented on the method, because it is indistinguishable from a
        prompt that exists with no versions."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [], "next_cursor": None})

        assert Prompts(_sync(handler)).versions(prompt_identifier="nope") == []

    def test_a_404_is_mapped_to_value_error(self) -> None:
        """Defensive, not observed: the route declares a 404 response, so handle it
        the way the sibling `get()` does rather than leaking an HTTPStatusError."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Prompt not found"})

        with pytest.raises(ValueError, match="Prompt not found: nope"):
            Prompts(_sync(handler)).versions(prompt_identifier="nope")

    def test_other_errors_are_not_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "Invalid cursor format"})

        with pytest.raises(httpx.HTTPStatusError):
            Prompts(_sync(handler)).versions(prompt_identifier="my-prompt")

    def test_identifier_is_path_encoded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.raw_path.startswith(b"/v1/prompts/my%20prompt/versions")
            return httpx.Response(200, json={"data": [], "next_cursor": None})

        assert Prompts(_sync(handler)).versions(prompt_identifier="my prompt") == []


class TestDelete:
    def test_deletes_by_identifier(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(204)

        Prompts(_sync(handler)).delete(prompt_identifier="my-prompt")

        assert seen == {"method": "DELETE", "path": "/v1/prompts/my-prompt"}

    def test_unknown_prompt_raises_value_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Prompt not found"})

        with pytest.raises(ValueError, match="Prompt not found: nope"):
            Prompts(_sync(handler)).delete(prompt_identifier="nope")

    def test_other_errors_are_not_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "Invalid prompt name"})

        with pytest.raises(httpx.HTTPStatusError):
            Prompts(_sync(handler)).delete(prompt_identifier="!!")


class TestAsyncPromptManagement:
    @pytest.mark.anyio
    async def test_list_follows_pagination(self) -> None:
        calls: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    200, json={"data": [_prompt(name="a")], "next_cursor": "cursor-2"}
                )
            return httpx.Response(200, json={"data": [_prompt(name="b")], "next_cursor": None})

        result = await AsyncPrompts(_async(handler)).list()

        assert [p["name"] for p in result] == ["a", "b"]
        assert calls[1].url.params.get("cursor") == "cursor-2"

    @pytest.mark.anyio
    async def test_versions(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/prompts/my-prompt/versions"
            return httpx.Response(200, json={"data": [_version()], "next_cursor": None})

        result = await AsyncPrompts(_async(handler)).versions(prompt_identifier="my-prompt")

        assert isinstance(result[0], PromptVersion)
        assert result[0].id == "UHJvbXB0VmVyc2lvbjox"

    @pytest.mark.anyio
    async def test_delete(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(204)

        await AsyncPrompts(_async(handler)).delete(prompt_identifier="my-prompt")

        assert seen == {"method": "DELETE", "path": "/v1/prompts/my-prompt"}

    @pytest.mark.anyio
    async def test_delete_unknown_prompt_raises_value_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Prompt not found"})

        with pytest.raises(ValueError, match="Prompt not found: nope"):
            await AsyncPrompts(_async(handler)).delete(prompt_identifier="nope")


class TestClientWiring:
    def test_methods_are_reachable_from_the_client(self) -> None:
        from phoenix.client import AsyncClient, Client

        for prompts in (
            Client(base_url="http://test").prompts,
            AsyncClient(base_url="http://test").prompts,
        ):
            for name in ("list", "versions", "delete"):
                assert callable(getattr(prompts, name)), name

    def test_the_mixin_does_not_shadow_the_http_client(self) -> None:
        """`_client` is only annotated on the mixin, so the real attribute set by
        `Prompts.__init__` is what the methods use."""
        client = httpx.Client(base_url="http://test")
        assert Prompts(client)._client is client  # pyright: ignore[reportPrivateUsage]
