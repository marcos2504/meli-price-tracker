import json
import re
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from extract.auth import (
    AuthError,
    FileTokenStore,
    TokenManager,
    Tokens,
    authorization_url,
    bootstrap,
    code_challenge_s256,
    generate_code_verifier,
)
from tests.fakes import FakeResponse, FakeSession, oauth_body, settings, valid_tokens

is_token_url = lambda url: url.endswith("/oauth/token")  # noqa: E731


class TokenManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = settings(self.tmp)
        self.store = FileTokenStore(self.settings.tokens_path)

    def test_token_vigente_no_se_renueva(self):
        self.store.save(valid_tokens(minutes=60))
        session = FakeSession()
        manager = TokenManager(self.settings, self.store, session)

        self.assertEqual(manager.get_access_token(), "ACCESS-OLD")
        self.assertEqual(session.calls, [])

    def test_token_por_vencer_se_renueva_y_persiste_el_refresh_rotado(self):
        self.store.save(valid_tokens(minutes=2))  # dentro del margen de 5 minutos
        session = FakeSession().add("POST", is_token_url, FakeResponse(200, oauth_body()))
        manager = TokenManager(self.settings, self.store, session)

        self.assertEqual(manager.get_access_token(), "ACCESS-NEW")
        sent = session.calls[0][2]["data"]
        self.assertEqual(sent["grant_type"], "refresh_token")
        self.assertEqual(sent["refresh_token"], "REFRESH-OLD")
        # El refresh token nuevo quedó guardado: el anterior ya no sirve
        self.assertEqual(self.store.load().refresh_token, "REFRESH-NEW")

    def test_refresh_rechazado_lanza_auth_error(self):
        self.store.save(valid_tokens(minutes=1))
        session = FakeSession().add("POST", is_token_url, FakeResponse(400, {"error": "invalid_grant"}))
        manager = TokenManager(self.settings, self.store, session)

        with self.assertRaises(AuthError) as ctx:
            manager.get_access_token()
        self.assertIn("bootstrap", str(ctx.exception))

    def test_sin_tokens_guardados_lanza_auth_error(self):
        manager = TokenManager(self.settings, self.store, FakeSession())
        with self.assertRaises(AuthError):
            manager.get_access_token()


class FileTokenStoreTests(unittest.TestCase):
    def test_lee_el_archivo_generado_en_la_fase_0(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / ".tokens.json"
        # Formato que guardaba scripts/oauth_bootstrap.py: respuesta completa de OAuth + expires_at
        path.write_text(
            json.dumps(
                {
                    "access_token": "A",
                    "token_type": "Bearer",
                    "expires_in": 21600,
                    "scope": "offline_access read",
                    "user_id": 533182444,
                    "refresh_token": "R",
                    "expires_at": "2026-10-05T10:01:16.546381+00:00",
                }
            )
        )
        tokens = FileTokenStore(path).load()
        self.assertEqual(tokens.refresh_token, "R")
        self.assertEqual(tokens.user_id, 533182444)
        self.assertEqual(tokens.expires_at, datetime(2026, 10, 5, 10, 1, 16, 546381, tzinfo=UTC))

    def test_ida_y_vuelta(self):
        path = Path(tempfile.mkdtemp()) / ".tokens.json"
        store = FileTokenStore(path)
        original = valid_tokens()
        store.save(original)
        self.assertEqual(store.load(), original)


class PkceTests(unittest.TestCase):
    def test_verifier_cumple_el_rfc(self):
        verifier = generate_code_verifier()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertRegex(verifier, r"^[A-Za-z0-9\-._~]+$")

    def test_challenge_es_sha256_base64url_sin_padding(self):
        challenge = code_challenge_s256("un-verifier-de-prueba-con-largo-suficiente-para-el-rfc-7636")
        self.assertEqual(len(challenge), 43)
        self.assertNotIn("=", challenge)
        self.assertIsNone(re.search(r"[+/]", challenge))

    def test_bootstrap_con_pkce_envia_challenge_y_verifier(self):
        tmp = Path(tempfile.mkdtemp())
        s = settings(tmp)
        store = FileTokenStore(s.tokens_path)
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs["data"])
            return FakeResponse(200, oauth_body())

        with (
            mock.patch("extract.auth.requests.post", side_effect=fake_post),
            mock.patch("builtins.print") as printed,
        ):
            bootstrap(s, store, use_pkce=True, ask=lambda _: "TG-abc")

        url = next(c.args[0] for c in printed.call_args_list if c.args and "authorization?" in str(c.args[0]))
        self.assertIn("code_challenge_method=S256", url)
        challenge = re.search(r"code_challenge=([^&\s]+)", url).group(1)
        self.assertEqual(code_challenge_s256(captured["code_verifier"]), challenge)
        self.assertEqual(captured["code"], "TG-abc")
        self.assertEqual(store.load().access_token, "ACCESS-NEW")

    def test_url_sin_pkce_no_lleva_challenge(self):
        url = authorization_url(settings(Path(tempfile.mkdtemp())))
        self.assertNotIn("code_challenge", url)


class TokensTests(unittest.TestCase):
    def test_from_oauth_response_calcula_vencimiento(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        tokens = Tokens.from_oauth_response(oauth_body(), now=now)
        self.assertEqual((tokens.expires_at - now).total_seconds(), 21600)


if __name__ == "__main__":
    unittest.main()
