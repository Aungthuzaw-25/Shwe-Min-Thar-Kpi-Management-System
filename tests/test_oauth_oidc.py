from app.modules.auth.oidc import create_nonce, create_pkce_challenge, create_pkce_verifier


def test_pkce_verifier_and_challenge_are_generated():
    verifier = create_pkce_verifier()
    assert len(verifier) >= 43
    assert create_pkce_challenge(verifier)
    assert create_nonce() != create_nonce()
