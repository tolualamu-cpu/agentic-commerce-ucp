"""RFC 9421 sign + verify roundtrip."""

from __future__ import annotations

from ucp.signing import RequestSigner, generate_keypair


def test_sign_and_verify_roundtrip():
    private_pem, _, jwk = generate_keypair("agent-key-1")
    signer = RequestSigner(private_pem, key_id="agent-key-1")
    signed = signer.sign(
        "POST",
        "https://merchant.com/checkout-sessions",
        headers={"content-type": "application/json"},
        body=b'{"foo":"bar"}',
    )
    assert "Signature" in signed.headers
    assert "Signature-Input" in signed.headers
    assert "content-digest" in signed.headers
    assert signer.verify(signed, jwk) is True


def test_tampered_body_fails_verify():
    private_pem, _, jwk = generate_keypair("agent-key-1")
    signer = RequestSigner(private_pem, key_id="agent-key-1")
    signed = signer.sign(
        "POST",
        "https://merchant.com/checkout-sessions",
        headers={"content-type": "application/json"},
        body=b'{"foo":"bar"}',
    )
    signed.body = b'{"foo":"evil"}'
    # content-digest header still matches old body; rewrite it to simulate tamper
    from ucp.signing import _content_digest

    signed.headers["content-digest"] = _content_digest(signed.body)
    assert signer.verify(signed, jwk) is False


def test_signature_input_includes_components():
    private_pem, _, jwk = generate_keypair("k1")
    signer = RequestSigner(private_pem, key_id="k1")
    signed = signer.sign("GET", "https://x.com/orders/1", body=b"")
    sig_input = signed.headers["Signature-Input"]
    assert sig_input.startswith("sig1=")
    for comp in ('"@method"', '"@target-uri"', '"host"'):
        assert comp in sig_input
    assert 'keyid="k1"' in sig_input
    assert 'alg="ed25519"' in sig_input


def test_wrong_key_fails_verify():
    private_pem_a, _, _ = generate_keypair("a")
    _, _, jwk_b = generate_keypair("b")
    signer = RequestSigner(private_pem_a, key_id="a")
    signed = signer.sign("GET", "https://x.com/", body=b"")
    assert signer.verify(signed, jwk_b) is False
