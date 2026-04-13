"""
middleware/auth.py — Autenticación JWT + inyección de tenant
============================================================
Verifica tokens JWT emitidos por Keycloak y extrae:
  - user_id    → sub claim del JWT
  - tenant_id  → custom claim fm_tenant_id
  - roles      → realm_access.roles
  - user_type  → determinado a partir de roles
  
El tenant_id se inyecta en el contexto de la request para que
get_db() lo use automáticamente en el SET LOCAL de RLS.
"""

from fastapi import Request, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import jwt
from jwt import PyJWKClient
from typing import Optional
import uuid
from dataclasses import dataclass
from functools import lru_cache

from config import settings

# HTTP Bearer scheme
security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    name: str
    user_type: str        # staff | technician | vendor | tenant_contact | admin
    roles: list[str]
    scopes: list[str]


class JWTMiddleware(BaseHTTPMiddleware):
    """
    Middleware que verifica el JWT en cada request y almacena
    el usuario autenticado en request.state.user.
    
    NO rechaza requests sin token (eso lo hace el Depends individual).
    Permite que endpoints públicos (/health) funcionen sin auth.
    """
    
    SKIP_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}
    
    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)
        
        authorization = request.headers.get("Authorization")
        if not authorization or not authorization.startswith("Bearer "):
            request.state.user = None
            return await call_next(request)
        
        token = authorization.split(" ")[1]
        
        try:
            user = await _verify_and_decode_jwt(token)
            request.state.user = user
        except Exception as e:
            request.state.user = None
        
        return await call_next(request)


async def _verify_and_decode_jwt(token: str) -> CurrentUser:
    """
    Verifica firma del JWT contra la clave pública de Keycloak
    y extrae los claims del usuario.
    """
    try:
        # En producción: verificar con JWKS endpoint de Keycloak
        # jwks_client = PyJWKClient(f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/certs")
        # signing_key = jwks_client.get_signing_key_from_jwt(token)
        
        # Decodificar JWT
        payload = jwt.decode(
            token,
            settings.JWT_PUBLIC_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},  # Audience verificado por Keycloak
        )
        
        # Extraer tenant_id del claim custom
        tenant_id_str = payload.get("fm_tenant_id")
        if not tenant_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token no contiene fm_tenant_id claim"
            )
        
        # Extraer roles de Keycloak
        realm_access = payload.get("realm_access", {})
        roles = realm_access.get("roles", [])
        
        # Determinar user_type desde roles
        user_type = _extract_user_type(roles)
        
        # Extraer scopes del token
        scope_str = payload.get("scope", "")
        scopes = scope_str.split() if scope_str else []
        
        return CurrentUser(
            id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(tenant_id_str),
            email=payload.get("email", ""),
            name=payload.get("name", ""),
            user_type=user_type,
            roles=roles,
            scopes=scopes,
        )
    
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_user_type(roles: list[str]) -> str:
    """Mapea roles de Keycloak a user_type del sistema."""
    role_priority = {
        "fm-admin":          "admin",
        "fm-staff":          "staff",
        "fm-technician":     "technician",
        "fm-vendor":         "vendor",
        "fm-tenant-contact": "tenant_contact",
    }
    for role_key, user_type in role_priority.items():
        if role_key in roles:
            return user_type
    return "tenant_contact"  # Default mínimo


# ── DEPENDENCIES ──────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """
    FastAPI dependency que devuelve el usuario autenticado.
    Lanza 401 si no hay token válido.
    """
    user = getattr(request.state, "user", None)
    if not user:
        if credentials:
            # Intentar verificar el token
            user = await _verify_and_decode_jwt(credentials.credentials)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "type": "https://api.fmplatform.io/errors/unauthorized",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": "Authentication required",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
    return user


def require_scope(required_scope: str):
    """
    Dependency factory para verificar scopes específicos.
    
    Uso:
        @router.post("/admin/...")
        async def admin_endpoint(
            _: None = Depends(require_scope("fm:admin")),
            current_user = Depends(get_current_user),
        ):
    """
    async def _check_scope(current_user: CurrentUser = Depends(get_current_user)):
        if required_scope not in current_user.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "type": "https://api.fmplatform.io/errors/forbidden",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": f"Scope '{required_scope}' required",
                }
            )
        return current_user
    
    return _check_scope


def require_role(*allowed_roles: str):
    """
    Dependency factory para verificar roles específicos.
    
    Uso:
        @router.delete("/assets/{id}")
        async def delete_asset(
            _: None = Depends(require_role("staff", "admin")),
            ...
        ):
    """
    async def _check_role(current_user: CurrentUser = Depends(get_current_user)):
        if current_user.user_type not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "type": "https://api.fmplatform.io/errors/forbidden",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": f"Role '{current_user.user_type}' not authorized. Required: {list(allowed_roles)}",
                }
            )
        return current_user
    
    return _check_role
