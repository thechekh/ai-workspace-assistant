"""Catch the model claiming it did something it has no tool to do.

Every tool is read-only, so a destructive request is unexecutable — but
unexecutable is not what the model *says*. Asked to erase documents, a small
model answered "The documents mentioning 'Qdrant' have been permanently erased
from the vector store. Confirmed." while having called only search tools. The
data was safe and the user was misinformed, which is the worse half.

The system prompt was fixed first and it does work: with the read-only
constraint stated before the tool list, six of six attempts against
gpt-4.1-nano refused correctly (two phrasings, three runs each), and the
direct request stopped calling tools at all.

This backstops it rather than replacing it. A prompt is evidence, not a
guarantee — the same words carry differently across a model swap, a
temperature change or a provider's next version, and the failure is silent:
the answer still looks confident. So the invariant is enforced where it can be
proved instead of sampled.

It *appends* a correction rather than rewriting the answer: a false positive
then costs one redundant sentence, where a rewrite would eat a legitimate one.
"""

import re
from collections.abc import Sequence

# Deliberately narrow — first-person or passive *completion* claims only.
# "you can delete a document with DELETE /api/documents" is a correct answer
# about the REST API and must survive untouched; only "I deleted it" is a lie.
_CLAIMED_MUTATION = re.compile(
    r"""
    \b(?:
        i (?:\s+ have | \s* 've) \s+ (?:now \s+)?(?:successfully \s+|permanently \s+)*
            (?:deleted|removed|erased|purged|wiped|updated|modified)
      | (?:has|have) \s+ been \s+ (?:successfully \s+|permanently \s+)*
            (?:deleted|removed|erased|purged|wiped)
      | (?:deletion|removal) \s+ (?:is \s+)?(?:complete|completed|done)
      | i \s+ (?:successfully \s+)?(?:deleted|erased|purged|wiped) \s
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

CORRECTION = (
    "\n\n_Correction: I have no tool that can delete, edit or otherwise change "
    "existing data — I can only search, read, and add repository documentation "
    "when asked. Nothing was modified. The knowledge base is managed outside "
    "this chat._"
)


# The tools that really do change state. A turn that called one of these may
# truthfully say "I have updated the knowledge base" — correcting it would be
# the guard telling the lie.
KB_WRITE_TOOLS = frozenset({"ingest_repo"})


def correct_unsupported_action_claims(text: str, *, tools_used: Sequence[str] = ()) -> str:
    """Append a correction when `text` claims a change this turn cannot have made.

    `tools_used` is the turn's actual tool calls: if a genuine write tool ran,
    a completion claim can be true and the guard stands down. Every other tool
    is read-only, so with none of KB_WRITE_TOOLS in the list a completion
    claim is false by construction — `tests/test_review_regressions.py` pins
    the tool surface this reasoning depends on.
    """
    if not text or not _CLAIMED_MUTATION.search(text):
        return text
    if any(tool in KB_WRITE_TOOLS for tool in tools_used):
        return text
    return text + CORRECTION
