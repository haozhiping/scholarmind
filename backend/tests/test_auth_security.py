"""Unit tests for common.auth.security — pure functions, no DB/infra."""
from jose import JWTError

from common.auth.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"  # never store plaintext
    assert verify_password("s3cret-pw", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("correct-horse")
    assert verify_password("battery-staple", h) is False


def test_verify_handles_garbage_hash():
    # malformed stored hash must not raise, just fail
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_long_password_does_not_raise():
    # >72 bytes must be truncated, not crash (bcrypt 4.x limit)
    long_pw = "a" * 200
    h = hash_password(long_pw)
    assert verify_password(long_pw, h) is True


def test_jwt_roundtrip_carries_claims():
    token = create_access_token(42, {"username": "alice", "role": "admin"})
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["username"] == "alice"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_decode_rejects_tampered_token():
    token = create_access_token(1, {"username": "bob"})
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    try:
        decode_token(tampered)
        raised = False
    except JWTError:
        raised = True
    assert raised is True
