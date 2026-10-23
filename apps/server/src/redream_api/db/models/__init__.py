"""ORM models. Imported by the test conftest's `Base.metadata.create_all` AND
by tests/pg's drift check against migrations/*.sql — keep imports complete.
Columns are Text where the migrations say TEXT (String would be VARCHAR):
the drift check compares the two schemas' definitions verbatim."""

from .auth_token import AuthToken
from .chat import Chat
from .chat_message import ChatMessage
from .daily_mood import DailyMood
from .dream import Dream
from .user import User
from .user_consent import UserConsent

__all__ = [
    "AuthToken",
    "Chat",
    "ChatMessage",
    "DailyMood",
    "Dream",
    "User",
    "UserConsent",
]
