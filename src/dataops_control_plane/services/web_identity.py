import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from dataops_control_plane.domain.models import (
    AppUser,
    PlatformState,
    WebSession,
    Workspace,
    WorkspaceMember,
)

PASSWORD_N = 2**14
PASSWORD_R = 8
PASSWORD_P = 1
PASSWORD_DKLEN = 32


class BootstrapUnavailable(Exception):
    pass


class InvalidCredentials(Exception):
    pass


@dataclass(frozen=True)
class CreatedIdentity:
    user: AppUser
    session_token: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_N,
        r=PASSWORD_R,
        p=PASSWORD_P,
        dklen=PASSWORD_DKLEN,
    )
    return "$".join(
        (
            "scrypt",
            str(PASSWORD_N),
            str(PASSWORD_R),
            str(PASSWORD_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded.split("$", maxsplit=5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bootstrap_owner(
    session: Session,
    *,
    email: str,
    password: str,
    workspace_name: str,
    session_ttl: timedelta,
) -> CreatedIdentity:
    if session.get(PlatformState, 1) is not None:
        raise BootstrapUnavailable("Platform bootstrap has already been completed")

    now = datetime.now(UTC)
    user = AppUser(
        email=email,
        password_hash=hash_password(password),
        created_at=now,
    )
    session.add(user)
    session.flush()
    workspace = Workspace(name=workspace_name, created_by=user.id, created_at=now)
    session.add(workspace)
    session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role="OWNER",
            created_at=now,
        )
    )
    raw_token = secrets.token_urlsafe(32)
    session.add(
        WebSession(
            token_hash=hash_opaque_token(raw_token),
            user_id=user.id,
            created_at=now,
            expires_at=now + session_ttl,
        )
    )
    session.add(PlatformState(id=1, bootstrap_completed_at=now))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise BootstrapUnavailable("Platform bootstrap has already been completed") from exc
    session.refresh(user)
    return CreatedIdentity(user=user, session_token=raw_token)


def get_user_for_session(session: Session, token: str) -> AppUser | None:
    web_session = session.get(WebSession, hash_opaque_token(token))
    if web_session is None:
        return None
    expires_at = web_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        session.delete(web_session)
        session.commit()
        return None
    user = session.get(AppUser, web_session.user_id)
    if user is None or user.status != "ACTIVE":
        return None
    return user


def revoke_web_session(session: Session, token: str) -> None:
    web_session = session.get(WebSession, hash_opaque_token(token))
    if web_session is not None:
        session.delete(web_session)
        session.commit()


def authenticate_user(
    session: Session,
    *,
    email: str,
    password: str,
    session_ttl: timedelta,
) -> CreatedIdentity:
    user = session.exec(select(AppUser).where(AppUser.email == email)).first()
    if user is None or user.status != "ACTIVE" or not verify_password(password, user.password_hash):
        raise InvalidCredentials("Invalid email or password")
    now = datetime.now(UTC)
    raw_token = secrets.token_urlsafe(32)
    session.add(
        WebSession(
            token_hash=hash_opaque_token(raw_token),
            user_id=user.id,
            created_at=now,
            expires_at=now + session_ttl,
        )
    )
    session.commit()
    return CreatedIdentity(user=user, session_token=raw_token)


def list_user_workspaces(
    session: Session,
    user_id,
) -> list[tuple[Workspace, WorkspaceMember]]:
    statement = (
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at.asc())
    )
    return list(session.exec(statement).all())
