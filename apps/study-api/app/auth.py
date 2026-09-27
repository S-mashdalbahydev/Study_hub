"""Accounts, sign-in sessions and the per-request principal.

Passwords are hashed with Argon2id. The browser holds an opaque random token in an
httpOnly cookie; only its SHA-256 is stored, so a database leak does not leak sessions and
any session can be revoked. Every signup gets a personal workspace that owns their data.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import time
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, HTTPException, Request, Response
import psycopg
from pydantic import BaseModel, Field, field_validator

from .db import Context, one, rows
from .ids import public

router = APIRouter(prefix='/api/auth')
COOKIE = 'studyhub_session'
EMAIL = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$')
hasher = PasswordHasher()
# Verifying against this keeps "no such email" as slow as "wrong password".
DUMMY_HASH = hasher.hash('studyhub-timing-equaliser')


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    workspace_id: UUID
    role: str
    is_admin: bool
    session_id: UUID

    @property
    def ctx(self):
        return Context(self.user_id, self.workspace_id)


class RateLimiter:
    """Per-process sliding window. A multi-replica deployment needs a shared store."""

    def __init__(self, limit, window):
        self.limit, self.window, self.hits = limit, window, {}

    def check(self, key):
        now = time.monotonic()
        recent = [t for t in self.hits.get(key, []) if now-t < self.window]
        if len(recent) >= self.limit:
            self.hits[key] = recent
            raise HTTPException(429, 'Too many attempts. Wait a few minutes and try again.')
        recent.append(now)
        self.hits[key] = recent


attempts = RateLimiter(10, 300)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def client_ip(request):
    return request.client.host if request.client else None


class Signup(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=256)

    @field_validator('display_name')
    @classmethod
    def name(cls, value):
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Enter your name')
        return value

    @field_validator('email')
    @classmethod
    def email(cls, value):
        value = value.strip().lower()
        if not EMAIL.match(value):
            raise ValueError('Enter a valid email address')
        return value


class Login(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=256)


class ConfirmPassword(BaseModel):
    password: str = Field(min_length=1, max_length=256)


async def start_session(conn, settings, request, response, user_id, workspace_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc)+timedelta(days=settings.session_days)
    await conn.execute('''INSERT INTO auth_sessions (user_id, token_hash, active_workspace_id, ip, user_agent, expires_at)
                          VALUES (%s, %s, %s, %s, %s, %s)''',
                       (user_id, token_hash(token), workspace_id, client_ip(request),
                        (request.headers.get('user-agent') or '')[:400], expires))
    response.set_cookie(COOKIE, token, max_age=settings.session_days*86400, httponly=True,
                        secure=settings.cookie_secure, samesite='lax', path='/')


async def audit(conn, action, *, actor=None, workspace=None, target_type=None, target_id=None, request=None, **metadata):
    from psycopg.types.json import Jsonb
    await conn.execute('''INSERT INTO audit_events (workspace_id, actor_id, action, target_type, target_id, metadata, ip)
                          VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                       (workspace, actor, action, target_type, target_id, Jsonb(metadata),
                        client_ip(request) if request else None))


async def create_account(conn, *, display_name, email, password_hash):
    """User, identity, personal workspace, owner membership and settings, in one transaction."""
    # Serialize the "first account becomes instance admin" decision.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('studyhub:first-admin'))")
    first = (await one(conn, 'SELECT NOT EXISTS (SELECT 1 FROM users) AS first'))['first']
    user = await one(conn, 'INSERT INTO users (display_name, is_instance_admin) VALUES (%s, %s) RETURNING id',
                     (display_name, first))
    await conn.execute('''INSERT INTO auth_identities (user_id, provider, issuer, subject, email, password_hash)
                          VALUES (%s, 'password', 'local', %s, %s, %s)''', (user['id'], email, email, password_hash))
    workspace = await one(conn, '''INSERT INTO workspaces (kind, name, slug, created_by)
                                   VALUES ('personal', %s, %s, %s) RETURNING id''',
                          (display_name[:100]+"'s library", 'p-'+uuid4().hex[:16], user['id']))
    await conn.execute("INSERT INTO memberships (workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
                       (workspace['id'], user['id']))
    await conn.execute('INSERT INTO user_settings (user_id) VALUES (%s)', (user['id'],))
    return user['id'], workspace['id'], first


@router.post('/signup', status_code=201)
async def signup(body: Signup, request: Request, response: Response):
    settings, db = request.app.state.runtime.settings, request.app.state.db
    if not settings.signup_open:
        raise HTTPException(403, 'Sign-up is closed on this StudyHub. Ask the administrator for an account.')
    attempts.check(('signup', client_ip(request)))
    password_hash = await asyncio.to_thread(hasher.hash, body.password)
    try:
        async with db.tx() as conn:
            existing = await one(conn, '''SELECT u.status FROM auth_identities i JOIN users u ON u.id = i.user_id
                                         WHERE i.email = %s AND i.revoked_at IS NULL AND i.provider = 'password' ''', (body.email,))
            if existing:
                if existing['status'] == 'pending_deletion':
                    raise HTTPException(409, 'This account is scheduled for deletion. Log in to cancel the deletion and keep it.')
                raise HTTPException(409, 'An account with this email already exists. Log in instead.')
            user_id, workspace_id, admin = await create_account(conn, display_name=body.display_name,
                                                                email=body.email, password_hash=password_hash)
            await start_session(conn, settings, request, response, user_id, workspace_id)
            await audit(conn, 'account.created', actor=user_id, request=request, instance_admin=admin)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, 'An account with this email already exists. Log in instead.') from None
    return await me_payload(db, user_id, workspace_id)


@router.post('/login')
async def login(body: Login, request: Request, response: Response):
    settings, db = request.app.state.runtime.settings, request.app.state.db
    email = body.email.strip().lower()
    attempts.check(('login-ip', client_ip(request)))
    attempts.check(('login-email', email))
    async with db.tx() as conn:
        found = await one(conn, '''SELECT i.id AS identity_id, i.password_hash, u.id AS user_id, u.status, u.purge_after
                                   FROM auth_identities i JOIN users u ON u.id = i.user_id
                                   WHERE i.email = %s AND i.provider = 'password' AND i.revoked_at IS NULL''', (email,))
    stored = found['password_hash'] if found else DUMMY_HASH
    try:
        await asyncio.to_thread(hasher.verify, stored, body.password)
        valid = bool(found) and found['status'] in ('active', 'pending_deletion')
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid:
        raise HTTPException(401, 'Email or password is incorrect.')
    cancelled = False
    async with db.tx() as conn:
        if hasher.check_needs_rehash(stored):
            await conn.execute('UPDATE auth_identities SET password_hash = %s WHERE id = %s',
                               (await asyncio.to_thread(hasher.hash, body.password), found['identity_id']))
        if found['status'] == 'pending_deletion':
            # Signing in during the grace period is how a deletion is cancelled.
            await conn.execute('''UPDATE users SET status = 'active', purge_after = NULL, deletion_requested_at = NULL
                                  WHERE id = %s AND status = 'pending_deletion' ''', (found['user_id'],))
            await conn.execute('''UPDATE deletion_operations SET state = 'done', stage = 'done', completed_at = now(),
                                  progress = progress || '{"cancelled": true}'
                                  WHERE target_type = 'user' AND target_id = %s AND state = 'pending' ''', (found['user_id'],))
            cancelled = True
        workspace = await one(conn, '''SELECT m.workspace_id FROM memberships m JOIN workspaces w ON w.id = m.workspace_id
                                       WHERE m.user_id = %s AND m.status = 'active' AND w.status = 'active'
                                       ORDER BY (w.kind = 'personal') DESC, m.created_at LIMIT 1''', (found['user_id'],))
        if not workspace:
            raise HTTPException(403, 'This account has no active workspace. Contact the administrator.')
        await start_session(conn, settings, request, response, found['user_id'], workspace['workspace_id'])
        await audit(conn, 'session.created', actor=found['user_id'], request=request, deletion_cancelled=cancelled)
    return {**await me_payload(db, found['user_id'], workspace['workspace_id']), 'deletion_cancelled': cancelled}


@router.post('/logout', status_code=204)
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        async with request.app.state.db.tx() as conn:
            await conn.execute('UPDATE auth_sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL',
                               (token_hash(token),))
    response.delete_cookie(COOKIE, path='/')


async def me_payload(db, user_id, workspace_id):
    async with db.tx() as conn:
        user = await one(conn, '''SELECT u.id, u.display_name, u.is_instance_admin, u.created_at, i.email
                                  FROM users u JOIN auth_identities i ON i.user_id = u.id AND i.revoked_at IS NULL
                                  WHERE u.id = %s LIMIT 1''', (user_id,))
        workspaces = await rows(conn, '''SELECT w.id, w.name, w.kind, m.role FROM memberships m
                                         JOIN workspaces w ON w.id = m.workspace_id
                                         WHERE m.user_id = %s AND m.status = 'active' AND w.status = 'active'
                                         ORDER BY (w.kind = 'personal') DESC, w.name''', (user_id,))
    active = next((w for w in workspaces if w['id'] == workspace_id), None)
    return public({'user': user, 'workspace': active, 'workspaces': workspaces})


@router.get('/me')
async def me(request: Request):
    principal = await require_user(request)
    return await me_payload(request.app.state.db, principal.user_id, principal.workspace_id)


@router.delete('/account', status_code=202)
async def request_deletion(body: ConfirmPassword, request: Request, response: Response):
    """Start the grace period. Access ends now; data is purged when the grace period ends."""
    principal = await require_user(request)
    settings, db = request.app.state.runtime.settings, request.app.state.db
    async with db.tx() as conn:
        found = await one(conn, '''SELECT password_hash FROM auth_identities
                                   WHERE user_id = %s AND provider = 'password' AND revoked_at IS NULL''', (principal.user_id,))
    try:
        await asyncio.to_thread(hasher.verify, found['password_hash'] if found else DUMMY_HASH, body.password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, 'That password is not correct.') from None
    purge_after = datetime.now(timezone.utc)+timedelta(days=settings.account_deletion_grace_days)
    async with db.tx() as conn:
        await conn.execute('''UPDATE users SET status = 'pending_deletion', deletion_requested_at = now(), purge_after = %s
                              WHERE id = %s''', (purge_after, principal.user_id))
        await conn.execute('UPDATE auth_sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL',
                           (principal.user_id,))
        await conn.execute('''INSERT INTO deletion_operations (target_type, target_id, requested_by, not_before)
                              VALUES ('user', %s, %s, %s)
                              ON CONFLICT (target_type, target_id, target_generation)
                              DO UPDATE SET state = 'pending', stage = 'revoke', not_before = EXCLUDED.not_before,
                                            completed_at = NULL''',
                           (principal.user_id, principal.user_id, purge_after))
        await audit(conn, 'account.deletion_requested', actor=principal.user_id, request=request)
    response.delete_cookie(COOKIE, path='/')
    return {'purge_after': purge_after.isoformat(), 'grace_days': settings.account_deletion_grace_days}


async def require_user(request: Request) -> Principal:
    cached = getattr(request.state, 'principal', None)
    if cached:
        return cached
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, 'Please log in.')
    async with request.app.state.db.tx() as conn:
        found = await one(conn, '''
            SELECT s.id, s.user_id, s.active_workspace_id, s.last_seen_at < now() - interval '5 minutes' AS stale,
                   u.status, u.is_instance_admin, m.role
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN memberships m ON m.workspace_id = s.active_workspace_id AND m.user_id = s.user_id AND m.status = 'active'
            LEFT JOIN workspaces w ON w.id = m.workspace_id AND w.status = 'active'
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()
              AND (m.role IS NULL OR w.id IS NOT NULL)''', (token_hash(token),))
        if not found or found['status'] != 'active':
            raise HTTPException(401, 'Your session has ended. Please log in again.')
        workspace_id, role = found['active_workspace_id'], found['role']
        if not role:
            # Removed from the workspace this session pointed at: fall back to another membership.
            fallback = await one(conn, '''SELECT m.workspace_id, m.role FROM memberships m JOIN workspaces w ON w.id = m.workspace_id
                                          WHERE m.user_id = %s AND m.status = 'active' AND w.status = 'active'
                                          ORDER BY (w.kind = 'personal') DESC LIMIT 1''', (found['user_id'],))
            if not fallback:
                raise HTTPException(403, 'This account has no active workspace.')
            workspace_id, role = fallback['workspace_id'], fallback['role']
            await conn.execute('UPDATE auth_sessions SET active_workspace_id = %s WHERE id = %s', (workspace_id, found['id']))
        if found['stale']:
            await conn.execute('UPDATE auth_sessions SET last_seen_at = now() WHERE id = %s', (found['id'],))
    principal = Principal(found['user_id'], workspace_id, role, found['is_instance_admin'], found['id'])
    request.state.principal = principal
    return principal


async def require_admin(request: Request) -> Principal:
    principal = await require_user(request)
    if not principal.is_admin:
        raise HTTPException(403, 'Only the StudyHub administrator can do that.')
    return principal
