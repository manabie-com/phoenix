"""A supported way to read a prompt version's message templates.

Fork-owned. ``PromptVersion``'s public surface is ``format``, ``from_*`` and
``id`` — all of which either render the template or identify it, and none of which
hand back the templates themselves. A consumer that wants to *read* what a prompt
says has had only ``prompt._template["messages"]``, which is private and free to
change shape under it.

The converters are not a substitute. ``to_genai`` and ``to_openai`` resolve media
while they convert, so on a prompt that declares a media variable they raise for a
caller who only wanted the text.
"""

from __future__ import annotations

from typing import Sequence

from phoenix.client.__generated__ import v1

__all__ = ["PromptMessagesMixin"]


class PromptMessagesMixin:
    """Adds :attr:`messages` to ``PromptVersion``.

    A mixin so that upstream's ``types/prompts.py`` carries an import and a base
    class rather than a property in the class body. The annotation below is not an
    assignment — it tells the type checker what ``PromptVersion`` supplies without
    creating a class attribute that could shadow it.
    """

    _template: v1.PromptChatTemplate

    @property
    def messages(self) -> Sequence[v1.PromptMessage]:
        """
        The version's message templates, unrendered.

        Each message is a ``role`` and its ``content`` — either a string or a
        sequence of content parts (``text``, ``tool_call``, ``tool_result``,
        ``image``, ``file``). Variables are still in template form: ``{{topic}}``
        under ``MUSTACHE``, and a media variable is still
        ``{"type": "image", "image": {"variable": "image"}}``.

        Returns:
            Sequence[v1.PromptMessage]: The messages in template order. The
            sequence is a copy, so appending to it does not alter the version; the
            message mappings inside it are not copied, so treat them as read-only.

        Note:
            Use this to *inspect* a prompt — count its messages, find the media
            variables it declares, show it to a reviewer. To *run* one, use
            ``format()`` or the ``to_openai`` / ``to_genai`` converters, which
            render variables and resolve media.

        Example::

            from phoenix.client import Client
            client = Client()

            prompt = client.prompts.get(prompt_identifier="my-prompt")
            for message in prompt.messages:
                print(message["role"], message["content"])

            # Which media variables does this template declare?
            declared = [
                part[part["type"]]["variable"]
                for message in prompt.messages
                if not isinstance(message["content"], str)
                for part in message["content"]
                if part["type"] in ("image", "file")
                and "variable" in part[part["type"]]
            ]
        """
        return tuple(self._template["messages"])
