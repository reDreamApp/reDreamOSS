"""Operator CLI: create users and reset passwords.

    python -m redream_api.cli create-user EMAIL [--password-stdin]
    python -m redream_api.cli set-password EMAIL [--password-stdin]

In a container: docker compose exec server python -m redream_api.cli ...;
locally: uv run python -m redream_api.cli ...

The password is asked for twice (not echoed), or read as one line from stdin
with --password-stdin. Same rules as the API; works in every
REGISTRATION_MODE. set-password signs the user out everywhere. Uses the app's
settings (DATABASE_URL) and session maker. Exit code 0 on success, 1 with a
short message otherwise; the password is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .core.passwords import password_problem
from .db.repositories.user_repo import UserRepo
from .services import accounts


class CliError(Exception):
    """A refusal; the message is printed as is."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m redream_api.cli", description="Manage reDream accounts."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("create-user", "create an account (in every REGISTRATION_MODE)"),
        ("set-password", "set an account's password and sign it out everywhere"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("email")
        command.add_argument(
            "--password-stdin", action="store_true", help="read the password as one line from stdin"
        )
    return parser


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        try:
            password = getpass.getpass("Password: ")
            repeated = getpass.getpass("Repeat password: ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise CliError("no password given") from exc
        if repeated != password:
            raise CliError("the passwords do not match")
    problem = password_problem(password)
    if problem is not None:
        raise CliError(problem)
    return password


async def _create_user(
    session_maker: async_sessionmaker[AsyncSession], email: str, password: str
) -> str:
    async with session_maker() as db:
        try:
            user = await accounts.create_user(db, email=email, password=password)
        except accounts.AccountError as exc:
            raise CliError(str(exc)) from exc
        return f"created {user.email} ({user.id})"


async def _set_password(
    session_maker: async_sessionmaker[AsyncSession], email: str, password: str
) -> str:
    async with session_maker() as db:
        user = await UserRepo(db).get_by_email(email)
        if user is None:
            raise CliError(f"no account with the email {email}")
        try:
            await accounts.set_password(db, user, password)
        except accounts.AccountError as exc:
            raise CliError(str(exc)) from exc
        return f"password set for {user.email}; all its sessions are signed out"


async def run(argv: Sequence[str], session_maker: async_sessionmaker[AsyncSession]) -> int:
    """Run one command against `session_maker`'s database; returns the exit code."""
    return await _execute(_parser().parse_args(argv), session_maker)


async def _execute(
    args: argparse.Namespace, session_maker: async_sessionmaker[AsyncSession]
) -> int:
    try:
        try:
            email = accounts.normalize_email(args.email)
        except ValueError as exc:
            raise CliError("invalid email") from exc
        password = _read_password(args.password_stdin)
        command = _create_user if args.command == "create-user" else _set_password
        print(await command(session_maker, email, password))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError) as exc:
        # The class only: the message may carry DATABASE_URL's parts.
        print(f"error: the database could not be used ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


async def _main(args: argparse.Namespace) -> int:
    # Imported here, after the arguments are parsed (--help works without a
    # .env): it loads the settings, which fail as at server startup.
    try:
        from .db.session import async_session_maker, engine
    except ValidationError as exc:
        print(f"error: the settings could not be loaded: {exc}", file=sys.stderr)
        return 1
    try:
        return await _execute(args, async_session_maker)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
