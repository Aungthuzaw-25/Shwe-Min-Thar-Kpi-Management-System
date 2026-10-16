import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWTError as JWTError

SUPPORTED_ID_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


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

    async def authorization_url(
        self,
        state: str,
        nonce: str,
        verifier: str,
        *,
        force_reauthentication: bool = False,
    ) -> str:
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
            "prompt": self._authorization_prompt(force_reauthentication),
            "max_age": "0" if force_reauthentication else None,
        }
        return f"{metadata['authorization_endpoint']}?{urlencode({k: v for k, v in params.items() if v is not None})}"

    def _authorization_prompt(self, force_reauthentication: bool) -> str | None:
        if force_reauthentication:
            return "login"
        if self.name == "google":
            return "select_account"
        return None

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

    async def _decode_identity_claims(self, id_token: str, metadata: dict) -> dict:
        try:
            issuer = metadata["issuer"]
            if not isinstance(issuer, str) or not issuer:
                raise OIDCValidationError("Provider identity validation failed.")
            supported_algorithms = metadata.get("id_token_signing_alg_values_supported")
            if not isinstance(supported_algorithms, list):
                raise OIDCValidationError("Unsupported ID token algorithm.")
            algorithms = [
                algorithm
                for algorithm in SUPPORTED_ID_TOKEN_ALGORITHMS
                if algorithm in supported_algorithms
            ]
            if not algorithms:
                raise OIDCValidationError("Unsupported ID token algorithm.")

            jwks = await self._jwks(metadata["jwks_uri"])
            keys = jwks.get("keys") if isinstance(jwks, dict) else None
            if not isinstance(keys, list):
                raise OIDCValidationError("Signing key not found.")

            for algorithm in algorithms:
                expected_key_type = "RSA" if algorithm.startswith("RS") else "EC"
                for key in keys:
                    if (
                        not isinstance(key, dict)
                        or key.get("kty") != expected_key_type
                        or key.get("use", "sig") != "sig"
                        or ("key_ops" in key and "verify" not in key["key_ops"])
                        or ("alg" in key and key["alg"] != algorithm)
                    ):
                        continue
                    try:
                        return jwt.decode(
                            id_token,
                            jwt.PyJWK.from_dict(key, algorithm=algorithm),
                            algorithms=[algorithm],
                            audience=self.client_id,
                            issuer=issuer,
                            options={
                                "verify_signature": True,
                                "verify_at_hash": False,
                                "require": ["iss", "sub", "aud", "exp", "iat", "nonce"],
                            },
                        )
                    except (JWTError, KeyError, TypeError):
                        continue
            raise OIDCValidationError("Provider identity validation failed.")
        except (JWTError, KeyError, TypeError) as exc:
            raise OIDCValidationError("Provider identity validation failed.") from exc

    def _identity_from_claims(
        self, claims: dict, nonce: str, issuer: str | None = None
    ) -> OIDCIdentity:
        if claims.get("nonce") != nonce:
            raise OIDCValidationError("Provider nonce validation failed.")
        self._validate_authorized_party(claims)
        subject = claims.get("sub")
        email = claims.get("email") or claims.get("preferred_username")
        if not subject or not email:
            raise OIDCValidationError("Provider identity is incomplete.")
        email_verified = claims.get("email_verified")
        self._validate_provider_email(email_verified)
        tenant_id = self._microsoft_tenant(claims)
        first_name, last_name = self._identity_name(claims, email)
        return OIDCIdentity(
            provider=self.name,
            issuer=claims.get("iss", issuer),
            subject=subject,
            email=email,
            email_verified=email_verified is True,
            first_name=first_name,
            last_name=last_name,
            tenant_id=tenant_id,
        )

    def _validate_authorized_party(self, claims: dict) -> None:
        audiences = claims.get("aud")
        if isinstance(audiences, list) and len(audiences) > 1:
            if claims.get("azp") != self.client_id:
                raise OIDCValidationError("Provider authorized party validation failed.")
            return
        if claims.get("azp") not in (None, self.client_id):
            raise OIDCValidationError("Provider authorized party validation failed.")

    def _validate_provider_email(self, email_verified) -> None:
        if self.name == "google" and email_verified is not True:
            raise OIDCValidationError("A verified provider email is required.")

    def _microsoft_tenant(self, claims: dict) -> str | None:
        tenant_id = claims.get("tid") if self.microsoft else None
        if self.microsoft and (
            not tenant_id
            or self.name != "microsoft"
            or tenant_id in {"common", "consumers"}
        ):
            raise OIDCValidationError("Only organizational Microsoft accounts are supported.")
        return tenant_id

    @staticmethod
    def _identity_name(claims: dict, email: str) -> tuple[str, str]:
        full_name = (claims.get("name") or "").strip()
        parts = full_name.split(maxsplit=1)
        first_name = parts[0] if parts else email.split("@", 1)[0]
        last_name = parts[1] if len(parts) > 1 else ""
        return first_name, last_name

    async def validate_identity(
        self,
        token_response: dict[str, Any],
        nonce: str,
        *,
        require_recent_authentication: bool = False,
    ) -> OIDCIdentity:
        id_token = token_response.get("id_token")
        if not id_token:
            raise OIDCValidationError("Provider did not return an ID token.")
        metadata = await self.metadata()
        claims = await self._decode_identity_claims(id_token, metadata)
        if require_recent_authentication:
            auth_time = claims.get("auth_time")
            now = datetime.now(UTC).timestamp()
            if (
                isinstance(auth_time, bool)
                or not isinstance(auth_time, (int, float))
                or auth_time > now + 60
                or auth_time < now - timedelta(minutes=10).total_seconds()
            ):
                raise OIDCValidationError("Provider authentication is not recent.")
        return self._identity_from_claims(claims, nonce, metadata.get("issuer"))

    async def _jwks(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
