"""Listing and deleting prompts, on top of endpoints the client did not reach.

Fork-owned. ``GET /v1/prompts``, ``GET /v1/prompts/{identifier}/versions`` and
``DELETE /v1/prompts/{identifier}`` are all upstream endpoints that upstream's
client happens to expose no method for, so consumers reached through
``client._client.get(...)`` instead.

Mixins rather than methods added to ``Prompts``: upstream's
``resources/prompts/__init__.py`` is where upstream adds its own prompt methods,
so a block of fork methods in that class body is a conflict on every upstream
change to it. As mixins the whole footprint there is an import and two base
classes, which git merges on its own.
"""

from __future__ import annotations

import builtins
from typing import Optional, cast

import httpx
from httpx import HTTPStatusError

from phoenix.client.__generated__ import v1
from phoenix.client.types.prompts import PromptVersion
from phoenix.client.utils.encode_path_param import encode_path_param

__all__ = [
    "AsyncPromptsManagementMixin",
    "PromptsManagementMixin",
]

# `def list` shadows the builtin for the rest of each class body, so every return
# annotation in these classes has to say which `list` it means. Spelling it out
# beats ordering the methods so that `list` comes last, which nothing enforces and
# the next method appended would silently break.
_List = builtins.list


class PromptsManagementMixin:
    """Adds :meth:`list`, :meth:`versions` and :meth:`delete` to ``Prompts``.

    The annotation below is not an assignment — it tells the type checker what
    ``Prompts`` supplies without creating a class attribute that could shadow it.
    """

    _client: httpx.Client

    def list(self) -> _List[v1.Prompt]:
        """
        Lists every prompt.

        Returns:
            list[v1.Prompt]: All prompts, each with its ``id``, ``name``, and any
            ``description`` and ``metadata``. Pagination is followed for you.

        Raises:
            httpx.HTTPStatusError: If the HTTP request returned an unsuccessful
                status code.

        Example::

            from phoenix.client import Client
            client = Client()

            for prompt in client.prompts.list():
                print(f"{prompt['id']}: {prompt['name']}")
        """
        prompts: _List[v1.Prompt] = []
        cursor: Optional[str] = None
        while True:
            response = self._client.get("v1/prompts", params=_page_params(cursor))
            response.raise_for_status()
            data = cast(v1.GetPromptsResponseBody, response.json())
            prompts.extend(data["data"])
            if not (cursor := data.get("next_cursor")):
                return prompts

    def versions(self, *, prompt_identifier: str) -> _List[PromptVersion]:
        """
        Lists every version of a prompt, newest first.

        Args:
            prompt_identifier (str): The name or ID of the prompt.

        Returns:
            list[PromptVersion]: The prompt's versions, newest first, as the same
            rich objects :meth:`~phoenix.client.resources.prompts.Prompts.get`
            returns — so each one can be formatted or read through
            :attr:`~phoenix.client.types.prompts.PromptVersion.messages`.
            Pagination is followed for you.

        Raises:
            ValueError: If the prompt does not exist.
            httpx.HTTPStatusError: If the HTTP request returned an unsuccessful
                status code.

        Example::

            from phoenix.client import Client
            client = Client()

            versions = client.prompts.versions(prompt_identifier="my-prompt")
            print(f"{len(versions)} versions, latest is {versions[0].id}")
        """
        url = f"v1/prompts/{encode_path_param(prompt_identifier)}/versions"
        versions: _List[PromptVersion] = []
        cursor: Optional[str] = None
        while True:
            try:
                response = self._client.get(url, params=_page_params(cursor))
                response.raise_for_status()
            except HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ValueError(f"Prompt not found: {prompt_identifier}")
                raise
            data = cast(v1.GetPromptVersionsResponseBody, response.json())
            versions.extend(PromptVersion._loads(version) for version in data["data"])  # pyright: ignore[reportPrivateUsage]
            if not (cursor := data.get("next_cursor")):
                return versions

    def delete(self, *, prompt_identifier: str) -> None:
        """
        Deletes a prompt along with all of its versions, tags, and labels.

        Args:
            prompt_identifier (str): The name or ID of the prompt to delete.

        Raises:
            ValueError: If the prompt does not exist.
            httpx.HTTPStatusError: If the HTTP request returned an unsuccessful
                status code.

        Warning:
            There is no per-version delete, server-side or here, and
            :meth:`~phoenix.client.resources.prompts.Prompts.create` **appends** a
            version to an existing name rather than replacing it. Removing one bad
            version therefore means deleting the prompt and its whole history.

        Example::

            from phoenix.client import Client
            client = Client()

            client.prompts.delete(prompt_identifier="my-prompt")
        """
        url = f"v1/prompts/{encode_path_param(prompt_identifier)}"
        try:
            response = self._client.delete(url)
            response.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError(f"Prompt not found: {prompt_identifier}")
            raise


class AsyncPromptsManagementMixin:
    """Adds :meth:`list`, :meth:`versions` and :meth:`delete` to ``AsyncPrompts``.

    See :class:`PromptsManagementMixin`.
    """

    _client: httpx.AsyncClient

    async def list(self) -> _List[v1.Prompt]:
        """
        Asynchronously lists every prompt.

        Returns:
            list[v1.Prompt]: All prompts, each with its ``id``, ``name``, and any
            ``description`` and ``metadata``. Pagination is followed for you.

        Raises:
            httpx.HTTPStatusError: If the HTTP request returned an unsuccessful
                status code.

        Example::

            from phoenix.client import AsyncClient
            async_client = AsyncClient()

            for prompt in await async_client.prompts.list():
                print(f"{prompt['id']}: {prompt['name']}")
        """
        prompts: _List[v1.Prompt] = []
        cursor: Optional[str] = None
        while True:
            response = await self._client.get("v1/prompts", params=_page_params(cursor))
            response.raise_for_status()
            data = cast(v1.GetPromptsResponseBody, response.json())
            prompts.extend(data["data"])
            if not (cursor := data.get("next_cursor")):
                return prompts

    async def versions(self, *, prompt_identifier: str) -> _List[PromptVersion]:
        """
        Asynchronously lists every version of a prompt, newest first.

        Args:
            prompt_identifier (str): The name or ID of the prompt.

        Returns:
            list[PromptVersion]: The prompt's versions, newest first. Pagination
            is followed for you.

        Raises:
            ValueError: If the prompt does not exist.
            httpx.HTTPStatusError: If the HTTP request returned an unsuccessful
                status code.

        Example::

            from phoenix.client import AsyncClient
            async_client = AsyncClient()

            versions = await async_client.prompts.versions(prompt_identifier="my-prompt")
        """
        url = f"v1/prompts/{encode_path_param(prompt_identifier)}/versions"
        versions: _List[PromptVersion] = []
        cursor: Optional[str] = None
        while True:
            try:
                response = await self._client.get(url, params=_page_params(cursor))
                response.raise_for_status()
            except HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ValueError(f"Prompt not found: {prompt_identifier}")
                raise
            data = cast(v1.GetPromptVersionsResponseBody, response.json())
            versions.extend(PromptVersion._loads(version) for version in data["data"])  # pyright: ignore[reportPrivateUsage]
            if not (cursor := data.get("next_cursor")):
                return versions

    async def delete(self, *, prompt_identifier: str) -> None:
        """
        Asynchronously deletes a prompt along with all of its versions, tags, and labels.

        Args:
            prompt_identifier (str): The name or ID of the prompt to delete.

        Raises:
            ValueError: If the prompt does not exist.
            httpx.HTTPStatusError: If the HTTP request returned an unsuccessful
                status code.

        Warning:
            There is no per-version delete. See
            :meth:`PromptsManagementMixin.delete`.

        Example::

            from phoenix.client import AsyncClient
            async_client = AsyncClient()

            await async_client.prompts.delete(prompt_identifier="my-prompt")
        """
        url = f"v1/prompts/{encode_path_param(prompt_identifier)}"
        try:
            response = await self._client.delete(url)
            response.raise_for_status()
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError(f"Prompt not found: {prompt_identifier}")
            raise


def _page_params(cursor: Optional[str]) -> dict[str, str]:
    """Query parameters for one page, omitting the cursor on the first request.

    The server rejects an empty ``cursor`` as an invalid cursor format rather than
    treating it as absent, so it has to be left out entirely rather than sent blank.
    """
    return {"cursor": cursor} if cursor else {}
