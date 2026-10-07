import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWTError as JWTError


class OIDCValidationError(ValueError):
    pass


def create_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def create_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def create_nonce() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class OIDCIdentity:
    provider: str
    issuer: str
    subject: str
    email: str
    email_verified: bool
    first_name: str
    last_name: str
    tenant_id: str | None = None


class OIDCProvider:
    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        discovery_url: str,
        scopes: tuple[str, ...],
        microsoft: bool = False,
    ):
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.discovery_url = discovery_url
        self.scopes = scopes
        self.microsoft = microsoft

    async def metadata(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self.discovery_url)
            response.raise_for_status()
            return response.json()

    async def validate_callback_issuer(self, callback_issuer: str | None) -> None:
        if callback_issuer is None:
            return
        metadata = await self.metadata()
        expected_issuer = metadata.get("issuer")
        if not expected_issuer or callback_issuer != expected_issuer:
            raise OIDCValidationError("Authorization response issuer validation failed.")

    async def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        metadata = await self.metadata()
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": create_pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": "offline" if self.name == "google" else None,
            "prompt": "select_account" if self.name == "google" else None,
        }
        return f"{metadata['authorization_endpoint']}?{urlencode({k: v for k, v in params.items() if v is not None})}"

    async def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        if not code or len(code) > 4096:
            raise OIDCValidationError("Invalid authorization code.")
        metadata = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": verifier,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(metadata["token_endpoint"], data=data)
            if response.status_code >= 400:
                raise OIDCValidationError("Authorization code exchange failed.")
            return response.json()

    async def validate_identity(self, token_response: dict[str, Any], nonce: str) -> OIDCIdentity:
        id_token = token_response.get("id_token")
        if not id_token:
            raise OIDCValidationError("Provider did not return an ID token.")
        metadata = await self.metadata()
        try:
            header = jwt.get_unverified_header(id_token)
            allowed_algorithms = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
            if header.get("alg") not in allowed_algorithms:
                raise OIDCValidationError("Unsupported ID token algorithm.")
            keys = await self._jwks(metadata["jwks_uri"])
            key = next((item for item in keys.get("keys", []) if item.get("kid") == header.get("kid")), None)
            if not key:
                raise OIDCValidationError("Signing key not found.")
            issuer = metadata["issuer"]
            claims = jwt.decode(
                id_token,
                jwt.PyJWK.from_dict(key),
                algorithms=allowed_algorithms,
                audience=self.client_id,
                issuer=issuer,
                options={"verify_at_hash": False},
            )
        except (JWTError, KeyError, TypeError) as exc:
            raise OIDCValidationError("Provider identity validation failed.") from exc
        if claims.get("nonce") != nonce:
            raise OIDCValidationError("Provider nonce validation failed.")
        subject = claims.get("sub")
        email = claims.get("email") or claims.get("preferred_username")
        if not subject or not email:
            raise OIDCValidationError("Provider identity is incomplete.")
        email_verified = claims.get("email_verified")
        if self.name == "google" and email_verified is not True:
            raise OIDCValidationError("A verified provider email is required.")
        tenant_id = claims.get("tid") if self.microsoft else None
        if self.microsoft and (
            not tenant_id
            or self.name != "microsoft"
            or tenant_id in {"common", "consumers"}
        ):
            raise OIDCValidationError("Only organizational Microsoft accounts are supported.")
        full_name = (claims.get("name") or "").strip()
        parts = full_name.split(maxsplit=1)
        return OIDCIdentity(
            provider=self.name,
            issuer=claims.get("iss", metadata["issuer"]),
            subject=subject,
            email=email,
            email_verified=email_verified is True,
            first_name=parts[0] if parts else email.split("@", 1)[0],
            last_name=parts[1] if len(parts) > 1 else "",
            tenant_id=tenant_id,
        )

    async def _jwks(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
