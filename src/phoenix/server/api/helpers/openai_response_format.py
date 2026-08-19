"""
What ``strict`` means for a response format Phoenix did not author.

OpenAI's Responses API applies strict structured-output validation to a
``text.format`` of type ``json_schema`` unless told otherwise, and strict mode
accepts only a subset of JSON Schema: every object has to carry
``additionalProperties: false``, and every property has to be listed in
``required``.

Almost no schema Phoenix replays satisfies that, because almost none of them were
written for OpenAI. Replaying a Gemini span is the case that surfaced it: the
recorded ``config.response_schema`` is promoted to a response format named
``response`` (``googleAdapter.ts``), and Gemini has no notion of
``additionalProperties`` at all, so switching the model to an OpenAI one failed the
whole run::

    Invalid schema for response_format 'response': In context=(),
    'additionalProperties' is required to be supplied and to be false.

Phoenix cannot vouch for a schema it is only passing through, so it stops opting
that schema into a guarantee it was never written to meet. The schema itself is sent
exactly as recorded — rewriting it would mean adding ``additionalProperties: false``
and promoting every optional property to required, which changes what the model was
asked to produce.

A format that explicitly asks for ``strict`` keeps it. Only the unspecified case is
decided here, and it is decided the way the Chat Completions API decides it, so the
two OpenAI paths behave alike rather than diverging on a default neither caller set.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai.types.responses.response_format_text_json_schema_config_param import (
        ResponseFormatTextJSONSchemaConfigParam,
    )


def default_openai_strict(fmt: "ResponseFormatTextJSONSchemaConfigParam") -> None:
    """
    Fill in ``strict`` when the recorded response format did not specify it.

    Mutates in place, and only when the key is absent: a caller that copied an
    explicit ``strict`` off the recording has already decided, and that decision is
    the user's rather than this default's.

    Args:
        fmt: The ``text.format`` config being built, with ``strict`` already set
            from the recording if it carried one.
    """
    fmt.setdefault("strict", False)
