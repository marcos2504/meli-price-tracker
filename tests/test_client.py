import tempfile
import unittest
from pathlib import Path

from extract.auth import FileTokenStore, TokenManager
from extract.client import ApiError, MeliClient
from tests.fakes import FakeResponse, FakeSession, connection_error, oauth_body, settings, valid_tokens

is_api = lambda url: "/oauth/token" not in url  # noqa: E731
is_token_url = lambda url: url.endswith("/oauth/token")  # noqa: E731


class MeliClientTests(unittest.TestCase):
    def make_client(self, session: FakeSession, max_retries: int = 3) -> tuple[MeliClient, list[float]]:
        tmp = Path(tempfile.mkdtemp())
        s = settings(tmp)
        store = FileTokenStore(s.tokens_path)
        store.save(valid_tokens())
        sleeps: list[float] = []
        client = MeliClient(
            TokenManager(s, store, session),
            session=session,
            max_retries=max_retries,
            backoff_base=1.0,
            sleep=sleeps.append,
        )
        return client, sleeps

    def test_200_devuelve_los_datos_y_manda_el_token(self):
        session = FakeSession().add("GET", is_api, FakeResponse(200, {"ok": True}))
        client, sleeps = self.make_client(session)

        resp = client.get("/products/MLA1/items", {"x": 1})

        self.assertTrue(resp.ok)
        self.assertEqual(resp.data, {"ok": True})
        self.assertEqual(resp.params, {"x": 1})
        self.assertEqual(session.calls[0][2]["headers"]["Authorization"], "Bearer ACCESS-OLD")
        self.assertEqual(sleeps, [])

    def test_429_respeta_retry_after(self):
        session = FakeSession().add(
            "GET", is_api, [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, {})]
        )
        client, sleeps = self.make_client(session)

        self.assertTrue(client.get("/x").ok)
        self.assertEqual(sleeps, [7.0])
        self.assertEqual(client.stats.rate_limited, 1)
        self.assertEqual(client.stats.retries, 1)

    def test_5xx_reintenta_con_backoff_creciente(self):
        session = FakeSession().add(
            "GET", is_api, [FakeResponse(503), FakeResponse(502), FakeResponse(200, {})]
        )
        client, sleeps = self.make_client(session)

        self.assertTrue(client.get("/x").ok)
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(1.0 <= sleeps[0] < 2.0)  # 1s + jitter
        self.assertTrue(2.0 <= sleeps[1] < 3.0)  # 2s + jitter

    def test_agotar_reintentos_lanza_api_error(self):
        session = FakeSession().add("GET", is_api, FakeResponse(500))
        client, sleeps = self.make_client(session, max_retries=2)

        with self.assertRaises(ApiError) as ctx:
            client.get("/x")
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(client.stats.requests, 3)

    def test_401_renueva_el_token_una_vez(self):
        session = (
            FakeSession()
            .add("POST", is_token_url, FakeResponse(200, oauth_body()))
            .add("GET", is_api, [FakeResponse(401), FakeResponse(200, {})])
        )
        client, _ = self.make_client(session)

        self.assertTrue(client.get("/x").ok)
        get_calls = [c for c in session.calls if c[0] == "GET"]
        self.assertEqual(get_calls[-1][2]["headers"]["Authorization"], "Bearer ACCESS-NEW")

    def test_401_repetido_no_entra_en_bucle(self):
        session = (
            FakeSession()
            .add("POST", is_token_url, FakeResponse(200, oauth_body()))
            .add("GET", is_api, FakeResponse(401))
        )
        client, _ = self.make_client(session)

        with self.assertRaises(ApiError) as ctx:
            client.get("/x")
        self.assertEqual(ctx.exception.status, 401)

    def test_404_no_es_error(self):
        session = FakeSession().add("GET", is_api, FakeResponse(404, {"message": "No winners found"}))
        client, _ = self.make_client(session)

        resp = client.get("/products/MLA1/items")
        self.assertFalse(resp.ok)
        self.assertEqual(resp.status, 404)

    def test_403_falla_sin_reintentar(self):
        session = FakeSession().add("GET", is_api, FakeResponse(403, {"error": "forbidden"}))
        client, sleeps = self.make_client(session)

        with self.assertRaises(ApiError):
            client.get("/items/MLA1")
        self.assertEqual(sleeps, [])

    def test_error_de_red_se_reintenta(self):
        session = FakeSession().add("GET", is_api, [connection_error, FakeResponse(200, {})])
        client, sleeps = self.make_client(session)

        self.assertTrue(client.get("/x").ok)
        self.assertEqual(len(sleeps), 1)


if __name__ == "__main__":
    unittest.main()
