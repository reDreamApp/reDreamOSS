"""language_check — does the chat switch between English and German?

The backend calls this before every turn, BEFORE safety_classify, and on a
switch updates the chat's language, so the crisis check, the helpline text and
the turn itself all run in the new language. A cheap word check comes first;
only messages that pass it reach the small model (`irt_app.
resolve_language_switch`). Any failure keeps the language.
"""

from __future__ import annotations

from ..core import irt_app
from ..models import Language, LanguageCheckRequest, LanguageCheckResponse


async def language_check(req: LanguageCheckRequest) -> LanguageCheckResponse:
    switched_to = await irt_app.resolve_language_switch(req.message, req.language.value)
    if switched_to is None:
        return LanguageCheckResponse(language=req.language, switched=False)
    return LanguageCheckResponse(language=Language(switched_to), switched=True)
