# MeLi Price Tracker

Pipeline de datos que sigue a diario el precio de productos de MercadoLibre, guarda el **historial completo de precios (SCD tipo 2)** y muestra qué productos bajaron o subieron, cuántos vendedores compiten por cada uno y cuándo un precio está por debajo de lo habitual.

Cubre el caso más común en data engineering: **ingesta robusta de una API externa**, carga incremental, historización de cambios y automatización sin servidor propio.

> **Estado:** Fase 0 completada (API validada, local y desde GitHub Actions) · Fase 1 en curso

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
4. El **dashboard** lee la capa gold.

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
| **Silver** | Datos tipados y deduplicados, con historial SCD2 | `stg_products`, `stg_listing_prices`, `snap_listing_prices` |
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
| **Watchlist en `watchlist.yml`** | Cada entrada es una búsqueda con su dominio y un máximo de productos. Agregar productos a seguir es una línea de configuración. |
| **ELT con capa bronze** | La respuesta JSON se guarda sin tocar. Si cambia la lógica, se reprocesa sin volver a consultar la API. |
| **Cargas idempotentes** | Correr dos veces el mismo día no duplica datos (delete + insert por `snapshot_date`). |
| **Refresh token persistido en la base** | MercadoLibre rota el refresh token en cada uso; un secret estático de GitHub no alcanza. |
| **Permisos mínimos** | La app solo tiene acceso de lectura: un token filtrado no permite modificar nada en la cuenta. |
| **dbt snapshot para SCD2** | Estrategia `check` sobre precio, precio original, envío y tipo de publicación. |
| **Registro de corridas en `ops.pipeline_runs`** | Cada ejecución guarda duración, productos procesados, errores y respuestas 429. |

> **Sobre la escala:** el volumen es de miles de filas por día, así que PostgreSQL alcanza de sobra. El diseño por capas y los sinks intercambiables permiten migrar a un lakehouse si el volumen creciera (ver [Roadmap](#roadmap)).

> **Sobre la inflación:** los precios están en pesos argentinos, donde una suba puede reflejar solo inflación. El roadmap incluye una dimensión de cotización del dólar para mostrar también variaciones en USD.

---

## Modelo de datos

| Schema | Tabla | Grano | Descripción |
|---|---|---|---|
| `bronze` | `api_responses` | request | JSON crudo + `run_id`, `endpoint`, `product_id`, `fetched_at` |
| `silver` | `stg_products` | producto | Nombre, dominio y atributos del catálogo |
| `silver` | `stg_listing_prices` | publicación × día | Precio, precio original, condición, tipo de publicación, envío, vendedor |
| `silver` | `snap_listing_prices` | publicación × versión | SCD2 con `dbt_valid_from` / `dbt_valid_to` |
| `gold` | `dim_product` | producto | Atributos descriptivos |
| `gold` | `dim_seller` | vendedor | Provincia, tienda oficial |
| `gold` | `fct_listing_daily` | publicación × día | Precio y condiciones de venta |
| `gold` | `fct_product_daily` | producto × día | Precio mínimo, mediana, vendedores, brecha de precios |
| `gold` | `mart_weekly_movers` | producto × semana | Mayores bajas y subas |
| `ops` | `auth_tokens` | 1 fila | Access y refresh token vigentes |
| `ops` | `pipeline_runs` | corrida | Métricas y estado de cada ejecución |

En dbt se mantiene la convención de carpetas (`models/staging`, `snapshots`, `models/marts`) y cada carpeta escribe en su schema desde `dbt_project.yml`.

---

## Plan de acción

### Fase 0: Validar la API ✅
- [x] App registrada en el DevCenter de MercadoLibre, con permisos de solo lectura
- [x] Flujo OAuth completo con refresh token
- [x] Endpoints validados: búsqueda general, más vendidos y detalle de publicaciones bloqueados; catálogo de productos disponible
- [x] Smoke test exitoso localmente y desde GitHub Actions

### Fase 1: Extractor (2–3 días)
- [ ] `auth.py`: refresh y persistencia del token rotativo, con PKCE
- [ ] `client.py`: reintentos con backoff exponencial (429 y 5xx), timeouts, paginación
- [ ] `discovery.py`: productos a seguir a partir de `watchlist.yml`
- [ ] `prices.py`: publicaciones y precios de cada producto
- [ ] Tests unitarios con respuestas simuladas

### Fase 2: Carga a bronze (1 día)
- [ ] Neon PostgreSQL con schemas `bronze`, `silver`, `gold` y `ops`
- [ ] `PostgresSink`: insert de respuestas JSON con `run_id`
- [ ] Registro de cada corrida en `ops.pipeline_runs`

### Fase 3: Transformación con dbt (2–3 días)
- [ ] Silver: `stg_products`, `stg_listing_prices` (idempotente) y `snap_listing_prices` (SCD2)
- [ ] Gold: dimensiones, `fct_listing_daily`, `fct_product_daily`, `mart_weekly_movers`
- [ ] Tests dbt: `unique`, `not_null`, relaciones, rangos de precio, frescura

### Fase 4: Orquestación (1 día)
- [ ] `daily.yml`: extracción + `dbt build` con cron diario
- [ ] `ci.yml`: lint (ruff), tests de Python y `dbt build` contra una base de prueba en cada PR
- [ ] Alerta si la corrida diaria falla

### Fase 5: Dashboard (1–2 días)
- [ ] Ranking de bajas y subas
- [ ] Evolución de precio y competencia por producto
- [ ] Panel de salud del pipeline (desde `ops.pipeline_runs`)
- [ ] Deploy en Streamlit Community Cloud

### Fase 6: Documentación (½ día)
- [ ] Captura del dashboard y link público
- [ ] Ejemplo de filas SCD2 explicadas
- [ ] Sección "qué aprendí / qué haría distinto"

---

## Estructura del repo

```
├── extract/
│   ├── auth.py          # obtención y rotación de tokens
│   ├── client.py        # cliente HTTP de la API
│   ├── discovery.py     # productos a seguir según la watchlist
│   ├── prices.py        # publicaciones y precios por producto
│   ├── sinks/
│   │   ├── base.py      # interfaz Sink
│   │   └── postgres.py  # escribe en bronze
│   └── main.py          # punto de entrada del pipeline
├── dbt/
│   ├── models/staging/  # → silver
│   ├── snapshots/       # → silver (SCD2)
│   └── models/marts/    # → gold
├── app/                 # dashboard Streamlit
├── tests/
├── scripts/             # utilidades de la Fase 0
├── docs/                # diagramas
├── watchlist.yml
└── .github/workflows/
    ├── daily.yml
    └── ci.yml
```

## Stack

Python · requests · PostgreSQL (Neon) · dbt-core · Streamlit · GitHub Actions

---

## Roadmap

- **Variaciones en USD:** dimensión diaria de cotización del dólar para separar cambios reales de precio de la inflación.
- **Migración a lakehouse (Databricks):** un nuevo sink escribe el JSON en un Volume de Unity Catalog sin cambiar el extractor; las mismas capas pasan a tablas Delta (Auto Loader en bronze, AUTO CDC en silver). Cierra con una comparativa de complejidad, costo y cuándo conviene cada versión.

## Nota sobre la API

Este proyecto usa una aplicación registrada en el DevCenter de MercadoLibre, con permisos de solo lectura, y respeta los límites de consultas de la API.