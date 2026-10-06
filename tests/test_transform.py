import unittest

from transform.__main__ import pg_env_from_url


class PgEnvFromUrlTests(unittest.TestCase):
    def test_descompone_la_url_de_neon(self):
        env = pg_env_from_url(
            "postgresql://neondb_owner:p%40ss@ep-cool-123.sa-east-1.aws.neon.tech/neondb?sslmode=require"
        )
        self.assertEqual(
            env,
            {
                "PGHOST": "ep-cool-123.sa-east-1.aws.neon.tech",
                "PGPORT": "5432",
                "PGUSER": "neondb_owner",
                "DBT_ENV_SECRET_PGPASSWORD": "p@ss",  # caracteres especiales decodificados
                "PGDATABASE": "neondb",
                "PGSSLMODE": "require",
            },
        )

    def test_puerto_y_sslmode_por_defecto(self):
        env = pg_env_from_url("postgres://u:p@localhost:5433/test")
        self.assertEqual((env["PGPORT"], env["PGSSLMODE"]), ("5433", "require"))

    def test_rechaza_otro_esquema(self):
        with self.assertRaises(ValueError):
            pg_env_from_url("mysql://u:p@h/db")


if __name__ == "__main__":
    unittest.main()
