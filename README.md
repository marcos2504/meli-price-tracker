# MeLi Price Tracker

Pipeline de datos que sigue a diario el precio de productos de MercadoLibre, guarda el **historial completo de precios (SCD tipo 2)** y muestra qué productos bajaron o subieron, cuántos vendedores compiten por cada uno y cuándo un precio está por debajo de lo habitual.

Cubre el caso más común en data engineering: **ingesta robusta de una API externa**, carga incremental, historización de cambios y automatización sin servidor propio.

> **Estado:** el pipeline corre solo todos los días en GitHub Actions · **[Ver el dashboard](URL_DEL_DASHBOARD)**

![Dashboard: movimientos de la semana](docs/img/dashboard-resumen.png)

---

## Resultado

- **Historial diario de precios** de cada vendedor para los productos de una watchlist configurable
- **Historial de cambios (SCD2)**: cada cambio de precio, descuento o condición de envío queda registrado con su vigencia
- **Competencia por producto**: precio mínimo, mediana y cantidad de vendedores por día
- **Mayores bajas y subas de la semana**
- **Dashboard público** con ranking de movimientos, evolución de precio por producto y salud del pipeline
- **Ejecución diaria automática** con GitHub Actions, idempotente y con costo $0

---

## Arquitectura

![Arquitectura del pipeline](docs/architecture.svg)

1. **GitHub Actions** dispara el pipeline una vez por día.
2. El **extractor Python** se autentica con OAuth, consulta la API con reintentos y guarda las respuestas sin modificar.
3. **dbt** transforma los datos en tres capas dentro de PostgreSQL.
4. Un **dashboard en Streamlit** (`dashboard/`) lee el star schema de gold con un usuario de solo lectura.

### Cómo se obtienen los datos

Desde 2025, MercadoLibre restringe varios endpoints a aplicaciones certificadas: la búsqueda general de publicaciones, los más vendidos y el detalle de publicaciones de terceros devuelven 403. La Fase 0 validó un camino que sí está disponible, basado en el **catálogo de productos**:

| Paso | Endpoint | Frecuencia |
|---|---|---|
| Descubrimiento | `/products/search` filtrado por búsqueda y dominio | Semanal |
| Precios | `/products/{id}/items`: todos los vendedores de un producto (hasta 100 por llamada) | Diaria |
| Atributos | `/products/{id}`: nombre y características del producto | Al descubrirlo |

El producto de catálogo es una entidad estable (por ejemplo, "iPhone 15 128 GB Negro"), y sus publicaciones son los vendedores que compiten por él. Eso permite analizar precios por producto y no por publicación individual.

### Capas (arquitectura medallion)

| Capa | Contenido | Tablas principales |
|---|---|---|
| **Bronze** | Respuestas de la API tal cual llegan, en `jsonb` | `api_responses` |
| **Silver** | Datos tipados y deduplicados, con historial SCD2 | `stg_products`, `stg_listing_prices`, `scd_listing_prices` |
| **Gold** | Modelo estrella listo para consumir | `dim_product`, `dim_seller`, `fct_listing_daily`, `fct_product_daily`, `mart_weekly_movers` |

El schema `ops` queda fuera de las capas: guarda el estado operativo del pipeline (tokens y registro de corridas), no datos de negocio.

---

## Patrones de diseño

### 1. Medallion (arquitectura de datos)
Separar crudo, limpio y consumible permite reprocesar sin volver a consultar la API y deja claro qué capa usa cada consumidor.

### 2. Sinks intercambiables (código)
El extractor no sabe dónde se guardan los datos: escribe a través de una interfaz `Sink` con un único método, `write_batch(records, run_id)`.

```python
class Sink(Protocol):
    def write_batch(self, records: list[dict], run_id: str) -> None: ...

class PostgresSink:   # escribe en bronze.api_responses
    ...
```

Cambiar el destino (por ejemplo, a un data lake) es agregar un sink nuevo y cambiar una línea de configuración, sin tocar el cliente de la API ni la lógica de extracción.

### 3. Modelo estrella en gold (modelado)
Gold sigue un modelo dimensional estilo Kimball, con dos tablas de hechos de distinto grano:

- **`dim_product`**: producto de catálogo, nombre, dominio y atributos
- **`dim_seller`**: vendedor, provincia, si es tienda oficial
- **`fct_listing_daily`**: publicación × día, con precio, precio original, envío y condición
- **`fct_product_daily`**: producto × día, con precio mínimo, mediana, cantidad de vendedores y brecha entre el más barato y el segundo

`mart_weekly_movers` se arma encima de `fct_product_daily`.

---

## Decisiones de diseño

| Decisión | Por qué |
|---|---|
| **Descubrimiento por catálogo** | Es el camino disponible para apps no certificadas, y el producto de catálogo es mejor unidad de análisis que la publicación suelta. |
| **Watchlist en `watchlist.toml`** | Cada entrada es una búsqueda con su dominio y un máximo de productos. TOML se lee con la biblioteca estándar (`tomllib`), sin dependencias extra. |
| **Filtro por regex sobre modelo y nombre** | La búsqueda del catálogo es aproximada y el atributo `MODEL` lo cargan los vendedores sin formato fijo (el mismo Galaxy S24 aparece como "S24", "Galaxy S24" o "S24 (eSIM)"). Un valor exacto pierde productos; las regex `include`/`exclude` de la watchlist los capturan y descartan variantes (FE, Ultra, Plus) antes de gastar llamadas en ellas. |
| **Descubrimiento semanal** | La lista de productos se guarda y se refresca cada 7 días o cuando cambia la watchlist. Los precios ya consultados al validar productos se reusan, sin repetir llamadas. |
| **ELT con capa bronze** | La respuesta JSON se guarda sin tocar. Si cambia la lógica, se reprocesa sin volver a consultar la API. |
| **Silver incremental e idempotente** | `stg_listing_prices` reprocesa los últimos 3 días y reemplaza cada producto-día completo (`delete+insert`). Correr dbt dos veces da el mismo resultado. |
| **Refresh token persistido en la base** | MercadoLibre rota el refresh token en cada uso; un secret estático de GitHub no alcanza. |
| **Permisos mínimos** | La app solo tiene acceso de lectura: un token filtrado no permite modificar nada en la cuenta. |
| **dbt sobre Postgres, no Spark ni pandas** | Los datos ya están en la base: dbt transforma ahí mismo, sin moverlos, y suma orden automático entre modelos, tests de datos y documentación. Con miles de filas por día, Spark agregaría complejidad sin beneficio; queda para la migración a Databricks del roadmap. |
| **SCD2 como modelo, no como `dbt snapshot`** | Bronze guarda la historia completa, así que el historial se reconstruye entero desde ahí con un *gaps and islands*: una versión nueva cuando cambia el precio, el precio original, el envío o el tipo de publicación, o cuando la publicación reaparece después de faltar. Un snapshot es estado acumulado que no se puede regenerar; este modelo es determinista y se testea con tests singulares (sin superposiciones, una versión vigente, cobertura de cada precio diario). |
| **Registro de corridas en `ops.pipeline_runs`** | La fila se inserta al empezar (`running`) y se actualiza al terminar: una corrida que se cae a la mitad también queda visible. Guarda duración, productos, errores y respuestas 429. |
| **Renovación del token bajo lock** | El refresh se hace con un advisory lock de Postgres. Si dos corridas renuevan a la vez, la segunda espera y reusa el token nuevo en lugar de invalidarlo. |
| **Bronze solo agrega filas** | Nunca se modifica ni se borra: si el pipeline corre dos veces el mismo día, silver se queda con la última corrida de cada día. |
| **Fecha de snapshot en hora de Argentina** | Una corrida a las 22 h cuenta para ese día, aunque en UTC ya sea el siguiente. |
| **CI con resultados esperados, no solo tests de reglas** | Los tests de dbt validan reglas generales (unicidad, nulos, relaciones). El CI además carga un escenario conocido y verifica números concretos del SCD2 y las métricas: si un cambio en el SQL altera los resultados, el PR falla. |
| **El dashboard lee el star schema, sin tablas extra** | Gold tiene solo dimensiones, hechos y un mart. Los joins de presentación (nombre del producto, datos del vendedor) los hace el dashboard en sus consultas; la lógica que necesita SQL de verdad, como comparar cada versión del SCD2 con la anterior, va en dbt como hecho propio (`fct_price_changes`, un evento por cambio de precio). |
| **Dashboard como código, con usuario de solo lectura** | Streamlit en el mismo repo: las páginas pasan por PR y el CI las ejecuta contra el escenario de prueba. La app es pública, así que usa un rol que solo lee gold y algunas columnas de `ops.pipeline_runs` (sin errores internos), con transacciones de solo lectura y timeout. Los *default privileges* mantienen el acceso aunque dbt recree las tablas, y el CI lo verifica creando el rol antes de `dbt build`. Las consultas se cachean una hora: los datos cambian una vez por día. |
| **Migraciones versionadas** | Archivos SQL numerados en `db/migrations/`, aplicados una sola vez y registrados en `ops.schema_migrations`. |

> **Sobre la escala:** el volumen es de miles de filas por día, así que PostgreSQL alcanza de sobra. El diseño por capas y los sinks intercambiables permiten migrar a un lakehouse si el volumen creciera (ver [Roadmap](#roadmap)).

> **Sobre la inflación:** los precios están en pesos argentinos, donde una suba puede reflejar solo inflación. El roadmap incluye una dimensión de cotización del dólar para mostrar también variaciones en USD.

---

## Modelo de datos

| Schema | Tabla | Grano | Descripción |
|---|---|---|---|
| `bronze` | `api_responses` | request | JSON crudo + `run_id`, `endpoint`, `product_id`, `fetched_at` |
| `silver` | `stg_products` | producto | Nombre, dominio y atributos del catálogo |
| `silver` | `stg_listing_prices` | publicación × día | Precio, precio original, condición, tipo de publicación, envío, vendedor |
| `silver` | `scd_listing_prices` | publicación × versión | SCD2 con `valid_from` / `valid_to` (exclusivo) e `is_current` |
| `gold` | `dim_product` | producto | Atributos descriptivos y `display_name` (nombre con formato corregido) |
| `gold` | `dim_seller` | vendedor | Provincia, tienda oficial |
| `gold` | `fct_listing_daily` | publicación × día | Precio y condiciones de venta |
| `gold` | `fct_product_daily` | producto × día | Precio mínimo, mediana, vendedores, brecha de precios |
| `gold` | `fct_price_changes` | cambio de precio | Cada vez que una publicación cambió de precio: anterior, nuevo y variación |
| `gold` | `mart_weekly_movers` | producto × semana | Mayores bajas y subas |
| `ops` | `auth_tokens` | 1 fila | Access y refresh token vigentes |
| `ops` | `tracked_products` | producto | Productos seguidos, resultado del último descubrimiento |
| `ops` | `pipeline_runs` | corrida | Métricas y estado de cada ejecución |

En dbt se mantiene la convención de carpetas (`models/staging` → `silver`, `models/marts` → `gold`); una macro hace que cada carpeta escriba exactamente en su schema.

### El historial de precios (SCD2), con un ejemplo

Este es el escenario que carga el CI en cada PR (`scripts/seed_ci.py`); los resultados de abajo los verifica `scripts/check_ci.py`. Un producto con tres publicaciones durante tres días:

| Publicación | Día 1 (05/01) | Día 2 (06/01) | Día 3 (07/01) |
|---|---|---|---|
| I1 | $ 1.000 | $ 950 | $ 950 (*) |
| I2 | $ 1.100 | $ 1.100 | — (ya no está) |
| I3 | — | $ 1.050 | $ 1.050 |

(*) El día 3 hubo dos corridas: la primera vio $ 900 y la segunda $ 950. Bronze guarda las dos; silver se queda con la última del día.

`silver.scd_listing_prices` lo convierte en versiones, una por cada tramo en que la publicación no cambió:

| item_id | version_n | price | valid_from | valid_to | is_current |
|---|---|---|---|---|---|
| I1 | 1 | 1.000 | 05/01 | 06/01 | false |
| I1 | 2 | 950 | 06/01 | — | true |
| I2 | 1 | 1.100 | 05/01 | 07/01 | false |
| I3 | 1 | 1.050 | 06/01 | — | true |

- `valid_to` es **exclusivo**: la versión 1 de I1 vale el 05/01 y deja de valer el 06/01. Así no hay huecos ni días contados dos veces.
- Cuando I2 desaparece, su versión se **cierra** el 07/01 en lugar de quedar vigente para siempre.
- La aparición de I3 y la desaparición de I2 no son cambios de precio. Por eso `gold.fct_price_changes` tiene **una sola fila**: I1, de $ 1.000 a $ 950 (−5 %), el 06/01.
- El SCD2 no es un `dbt snapshot`: se reconstruye entero desde bronze cada vez, así que si cambia la lógica, la historia se recalcula sola.

---

## Plan de acción

### Fase 0: Validar la API ✅
- [x] App registrada en el DevCenter de MercadoLibre, con permisos de solo lectura
- [x] Flujo OAuth completo con refresh token
- [x] Endpoints validados: búsqueda general, más vendidos y detalle de publicaciones bloqueados; catálogo de productos disponible
- [x] Smoke test exitoso localmente y desde GitHub Actions

### Fase 1: Extractor ✅
- [x] `auth.py`: renovación automática y persistencia atómica del token rotativo, bootstrap con PKCE opcional
- [x] `client.py`: reintentos con backoff exponencial y jitter (429, 5xx, errores de red), `Retry-After`, renovación ante 401
- [x] `discovery.py`: productos con vendedores activos a partir de `watchlist.toml`, con búsqueda paginada
- [x] `prices.py`: publicaciones y precios de cada producto
- [x] `LocalJsonlSink`: bronze en archivos JSON Lines particionados por fecha, mientras no hay base
- [x] Filtro de productos por dominio y regex sobre modelo y nombre
- [x] 34 tests unitarios con la API simulada, sin acceso a red

### Fase 2: Carga a bronze ✅
- [x] Neon PostgreSQL (São Paulo) con schemas `bronze`, `silver`, `gold` y `ops`, mediante migraciones versionadas
- [x] `PostgresSink`: cada lote se inserta en una sola transacción
- [x] Tokens, productos seguidos y registro de corridas en `ops`, para que el pipeline no dependa de archivos locales
- [x] Renovación del token protegida contra corridas simultáneas
- [x] Tests unitarios de los stores y tests de integración contra Postgres (opcionales, con `TEST_DATABASE_URL`)

### Fase 3: Transformación con dbt ✅
- [x] Silver: `stg_products` (atributos del catálogo), `stg_listing_prices` (incremental e idempotente) y `scd_listing_prices` (SCD2 reconstruible desde bronze)
- [x] Gold: `dim_product`, `dim_seller`, `fct_listing_daily`, `fct_product_daily`, `mart_weekly_movers`
- [x] Tests de datos: `unique`, `not_null`, relaciones, valores aceptados, precios positivos y frescura de bronze
- [x] Tests singulares del SCD2: sin versiones superpuestas, una sola versión vigente, cada precio diario cubierto por una versión
- [x] `python -m transform`: corre dbt con la misma `DATABASE_URL` que el extractor

### Fase 4: Orquestación ✅
- [x] `daily.yml`: extracción + `dbt build` todos los días a las 10:17 (hora de Argentina), sin corridas superpuestas
- [x] `ci.yml`: en cada PR, lint (ruff), tests de Python, tests de integración y `dbt build` contra un Postgres descartable
- [x] Escenario de prueba en el CI con resultados esperados: cambio de precio, publicación que desaparece, producto sin vendedores y dos corridas el mismo día
- [x] Si la corrida diaria falla, se abre (o se actualiza) un issue en el repo con el link a la ejecución

### Fase 5: Dashboard en Streamlit
- [x] `fct_price_changes` en gold: un evento por cada cambio de precio, a partir del SCD2
- [x] Usuario de solo lectura `dashboard_reader` (`db/manual/dashboard_reader.sql`)
- [x] Cuatro páginas: bajas y subas de la semana, evolución por producto, cambios de precio y salud del pipeline
- [x] `display_name` en `dim_product`: limpia los nombres del catálogo ("S24 , Negro, 8gb_256gb" → "S24, Negro, 8 GB 256 GB")
- [x] Origen de cada corrida (`triggered_by`: programada, manual o local) para distinguir la diaria de las pruebas
- [x] Tema propio y formato argentino en KPIs, tablas y ejes
- [x] CI: las consultas corren con el usuario de solo lectura, se verifica que no pueda ver ni escribir lo que no debe y cada página se ejecuta con `AppTest`
- [ ] Publicado en Streamlit Community Cloud, con el link en este README

### Fase 6: Documentación y uso
- [ ] Captura del dashboard y link público
- [x] Ejemplo del SCD2 explicado con el escenario del CI
- [x] Productos por link (`[[product]]` en la watchlist) y `python -m extract preview` para probar búsquedas
- [x] [Lecciones aprendidas](#qué-aprendí-y-qué-haría-distinto)

---

## Estructura del repo

```
├── extract/
│   ├── config.py        # configuración desde el entorno
│   ├── auth.py          # obtención y rotación de tokens
│   ├── client.py        # cliente HTTP de la API
│   ├── discovery.py     # productos a seguir según la watchlist
│   ├── prices.py        # publicaciones y precios por producto
│   ├── db.py            # conexión y migraciones
│   ├── runlog.py        # registro de corridas
│   ├── sinks/
│   │   ├── base.py      # interfaz Sink
│   │   ├── local.py     # bronze en JSON Lines (desarrollo)
│   │   └── postgres.py  # bronze en Postgres
│   └── main.py          # punto de entrada: python -m extract
├── db/migrations/       # SQL versionado
├── db/manual/           # SQL que se corre a mano una vez (usuario del dashboard)
├── transform/           # python -m transform: corre dbt con DATABASE_URL
├── dbt/
│   ├── models/staging/  # → silver (incluye el SCD2)
│   ├── models/marts/    # → gold
│   ├── macros/          # schemas, atributos del catálogo, tests genéricos
│   └── tests/           # tests singulares del SCD2
├── dashboard/           # Streamlit: streamlit run dashboard/app.py
│   ├── app.py           # navegación entre páginas
│   ├── queries.py       # SQL contra el star schema de gold
│   ├── data.py          # conexión de solo lectura y caché
│   ├── fmt.py           # formato de precios, porcentajes, fechas y ejes
│   └── views/           # una página por archivo
├── .streamlit/          # tema del dashboard
├── tests/
├── scripts/             # utilidades de la Fase 0 y verificaciones del CI
├── docs/                # diagrama de arquitectura y capturas (img/)
├── watchlist.toml
└── .github/workflows/
    ├── daily.yml        # pipeline diario
    └── ci.yml           # lint, tests y dbt en cada PR
```

## Cómo correrlo

Requiere Python 3.11 o superior.

```bash
python -m venv .venv && source .venv/bin/activate   # en Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                 # completar con la app del DevCenter y DATABASE_URL

python -m extract db-init            # crea las tablas en Postgres (si hay un token local, lo importa)
python -m extract bootstrap          # autorización inicial, una sola vez
python -m extract run                # descubrimiento (si toca) y precios del día
python -m transform build            # modelos silver y gold de dbt, con sus tests
python -m unittest discover -s tests -t .   # tests
```

Sin `DATABASE_URL`, el pipeline guarda todo en archivos dentro de `data/`: sirve para probar sin base.

### Elegir qué productos seguir

Todo se define en `watchlist.toml`, de dos formas:

**Un producto puntual:** se pega el link de MercadoLibre, el que tiene `/p/MLA…`. Se sigue siempre, aunque algún día no tenga vendedores.

```toml
[[product]]
url = "https://www.mercadolibre.com.ar/apple-iphone-15-128-gb-negro/p/MLA1027172677"
group = "iphone-15"     # nombre con el que aparece en el dashboard (si no se pone: "favoritos")
```

**Una búsqueda:** trae los productos del catálogo que coinciden y hoy tienen vendedores, hasta `max_products`. Sirve para seguir una familia entera (todos los colores y capacidades de un modelo). Antes de agregarla, se prueba sin guardar nada:

```bash
python -m extract preview --query "iphone 16" --domain MLA-CELLPHONES --exclude "\b(pro|plus)\b"
python -m extract preview            # qué encontraría la watchlist actual y qué descarta el filtro
```

Al cambiar la watchlist, la próxima corrida vuelve a descubrir los productos; para hacerlo en el momento: `python -m extract run --force-discovery`.

### Dashboard

```bash
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py      # lee DASHBOARD_DATABASE_URL del .env (usuario dashboard_reader)
```

Para publicarlo en [Streamlit Community Cloud](https://share.streamlit.io): *Create app* → este repo, rama `main`, archivo `dashboard/app.py`, y en *Advanced settings → Secrets*:

```toml
DASHBOARD_DATABASE_URL = "postgresql://dashboard_reader:...@...-pooler....neon.tech/neondb?sslmode=require"
```

### Automatización en GitHub Actions

El workflow `daily.yml` necesita estos secrets en **Settings → Secrets and variables → Actions**:

| Secret | Valor |
|---|---|
| `ML_CLIENT_ID` | App ID de la aplicación del DevCenter |
| `ML_CLIENT_SECRET` | Clave secreta de la aplicación |
| `DATABASE_URL` | Connection string de Neon |

Los tokens de MercadoLibre no van como secret: viven en `ops.auth_tokens` y el pipeline los renueva solo. Para correrlo a mano: pestaña **Actions → Pipeline diario → Run workflow**.

> GitHub desactiva los workflows programados de un repo sin actividad durante 60 días. Si eso pasa, se reactivan desde la pestaña Actions.

## Stack

Python · requests · PostgreSQL (Neon) · dbt-core · GitHub Actions · Streamlit

---

## Qué aprendí y qué haría distinto

- **Validar la API antes de diseñar.** La Fase 0 encontró que la búsqueda general y el detalle de publicaciones estaban bloqueados para apps no certificadas. Todo el diseño (catálogo de productos → publicaciones de cada producto) salió de lo que sí estaba disponible. Si hubiera empezado por el modelo de datos, lo habría tenido que tirar.
- **El estado que cambia va en la base, no en secrets.** MercadoLibre rota el refresh token en cada uso: guardarlo como secret de GitHub funciona una sola vez. Vive en `ops.auth_tokens` y se renueva bajo un lock, porque dos corridas a la vez se invalidaban el token entre sí.
- **Bronze crudo y solo de escritura hace todo lo demás reversible.** Con las respuestas originales guardadas, corregir un modelo de silver o del SCD2 es volver a correr dbt: nunca hubo que volver a pedirle datos a la API.
- **Tests con resultados esperados, no solo reglas.** `unique` y `not_null` no detectan un SCD2 que calcula mal las fechas. El escenario fijo del CI (dos corridas el mismo día, una publicación que desaparece, un producto sin vendedores) sí.
- **Verificar el estado actual de las herramientas.** Para el dashboard evalué Looker Studio y Evidence. Evidence había cambiado de modelo (deprecó el build estático y la versión hosteada pasó a ser paga), y eso descartaba justo lo que lo hacía atractivo. Terminó siendo Streamlit, con el dashboard como código y probado en el CI.
- **Qué haría distinto: un entorno de desarrollo separado.** Mientras desarrollaba el dashboard en una rama, el pipeline diario (que corre el código de `main`) reconstruyó gold con los modelos viejos y el dashboard de la rama se rompió. Con una branch de Neon para desarrollo, cada rama tendría su propia base y `main` no pisaría nada.
- **Qué haría distinto: productos por link desde el principio.** Las búsquedas con regex son potentes para seguir una familia entera, pero para "quiero este producto" alcanza con pegar el link. Lo agregué al final; hubiera ahorrado varias iteraciones de regex.

## Roadmap

- **Nombre del vendedor:** hoy el dashboard identifica a cada vendedor por ID y ubicación. Falta validar si `/users/{id}` responde sin certificar la app para sumar el nickname a `dim_seller`.
- **Variaciones en USD:** dimensión diaria de cotización del dólar para separar cambios reales de precio de la inflación.
- **Migración a lakehouse (Databricks):** un nuevo sink escribe el JSON en un Volume de Unity Catalog sin cambiar el extractor; las mismas capas pasan a tablas Delta (Auto Loader en bronze, AUTO CDC en silver). Cierra con una comparativa de complejidad, costo y cuándo conviene cada versión.

## Nota sobre la API

Este proyecto usa una aplicación registrada en el DevCenter de MercadoLibre, con permisos de solo lectura, y respeta los límites de consultas de la API.
