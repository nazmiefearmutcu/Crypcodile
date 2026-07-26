import importlib

import pytest


def test_the_token_bucket_is_in_core():
    mod = importlib.import_module("crocodile.core.ratelimit")
    assert hasattr(mod, "TokenBucket")
    assert hasattr(mod, "TokenBucketLimiter")


@pytest.mark.parametrize("name", ["ProxyRotator", "ApiKeyPool"])
def test_evasion_machinery_is_not_in_core(name: str) -> None:
    mod = importlib.import_module("crocodile.core.ratelimit")
    assert not hasattr(mod, name), (
        f"{name} is evasion machinery and belongs in crocodile.contrib.evasion, "
        "so that 'legal by construction' is the product's default posture"
    )


def test_the_limiter_does_not_know_about_proxies_or_keys():
    """The coupling that put evasion in the core is the one to break here."""
    import inspect

    from crocodile.core.ratelimit import TokenBucket

    params = set(inspect.signature(TokenBucket.__init__).parameters)
    assert "proxy_rotator" not in params
    assert "api_key_pool" not in params

    leaked = [
        name
        for name in dir(TokenBucket)
        if any(word in name for word in ("proxy", "api_key", "key_"))
    ]
    assert not leaked, f"proxy/key delegation still on TokenBucket: {leaked}"
