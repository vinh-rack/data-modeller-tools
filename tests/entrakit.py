"""Synthetic Entra ID for tests: a locally generated RSA key, its JWKS, and signed v2-shaped access tokens.

Nothing here talks to Entra. LocalJWKS is a real PyJWKClient with only the HTTP fetch replaced, so key selection by
kid runs exactly as in production. All ids are made up.
"""

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

TENANT = "00000000-1111-4222-8333-444444444444"
CLIENT_ID = "55555555-6666-4777-8888-999999999999"
AUDIENCE = f"api://{CLIENT_ID}"
SCOPE = "dma.access"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
USER_A = "aaaaaaaa-0000-4000-8000-000000000001"
USER_B = "bbbbbbbb-0000-4000-8000-000000000002"
GROUP = "cccccccc-0000-4000-8000-000000000003"
KID = "test-key-1"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks(*pairs):
    keys = []
    for kid, private in pairs or [(KID, KEY)]:
        jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
        keys.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": keys}


class LocalJWKS(jwt.PyJWKClient):
    def __init__(self, data=None):
        super().__init__(f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys", cache_keys=True)
        self.data = data or jwks()
        self.fetches = 0

    def fetch_data(self):
        self.fetches += 1
        return self.data


def claims(oid=USER_A, name="User A", **over):
    now = int(time.time())
    base = {
        "aud": CLIENT_ID, "iss": ISSUER, "iat": now, "nbf": now, "exp": now + 3600, "tid": TENANT, "oid": oid,
        "sub": f"sub-{oid}", "name": name, "preferred_username": f"{name.lower().replace(' ', '.')}@example.test",
        "scp": SCOPE, "azp": "client-app-id", "ver": "2.0",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def token(key=KEY, kid=KID, alg="RS256", **over):
    return jwt.encode(claims(**over), key, algorithm=alg, headers={"kid": kid})


def entra_env(tmp_path, smx_dir, config_dir, **extra):
    return {
        "DMA_SMX_DIR": str(smx_dir), "DMA_OUTPUT_DIR": str(tmp_path / "output"), "DMA_CONFIG_DIR": str(config_dir),
        "DMA_TRANSPORT": "http", "DMA_AUTH": "entra", "DMA_ENTRA_TENANT_ID": TENANT, "DMA_ENTRA_CLIENT_ID": CLIENT_ID,
        "DMA_ENTRA_AUDIENCE": AUDIENCE, "DMA_REQUIRED_SCOPES": SCOPE, "DMA_RESOURCE_SERVER_URL": "http://127.0.0.1:8000/mcp", **extra,
    }
