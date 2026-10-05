# MeLi Price Tracker

Pipeline de datos que consulta diariamente la API de MercadoLibre para varias categorías, guarda el **historial completo de precios (SCD tipo 2)** y muestra qué productos bajaron o subieron de precio.

Cubre el caso más común en data engineering: **ingesta robusta de una API externa**, carga incremental, historización de cambios y automatización sin servidor propio.

> **Estado:** en construcción · Fase 0 (validación de la API)

---

## Resultado

- **Historial diario de precios** de los productos más relevantes de N categorías configurables
- **Historial de cambios (SCD2)**: cada cambio de precio o estado queda registrado con su vigencia
- **Mayores bajas y subas de la semana** por categoría
- **Dashboard público** con ranking de movimientos, evolución de precio por producto y salud del pipeline
- **Ejecución diaria automática** con GitHub Actions, idempotente y con costo $0

---

## Arquitectura

![Arquitectura del pipeline](docs/architecture.svg)

1. **GitHub Actions** dispara el pipeline una vez por día.
2. El **extractor Python** se autentica con OAuth, consulta la API con reintentos y guarda las respuestas sin modificar.
3. **dbt** transforma los datos en tres capas dentro de PostgreSQL.
4. El **dashboard** lee la capa gold.

### Capas (arquitectura medallion)

| Capa | Contenido | Tablas principales |
|---|---|---|
| **Bronze** | Respuestas de la API tal cual llegan, en `jsonb` | `api_responses` |
| **Silver** | Datos tipados y deduplicados, con historial SCD2 | `stg_item_prices`, `snap_item_prices` |
| **Gold** | Modelo estrella listo para consumir | `dim_item`, `dim_category`, `fct_daily_price`, `mart_weekly_movers` |

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
Gold sigue un modelo dimensional estilo Kimball:

- **`dim_item`**: publicación, título, vendedor, condición (se construye desde el SCD2 de silver)
- **`dim_category`**: categoría y su jerarquía
- **`fct_daily_price`**: un registro por ítem y día, con precio y variación respecto del día anterior

`mart_weekly_movers` se arma encima del modelo estrella con una consulta simple.

---

## Decisiones de diseño

| Decisión | Por qué |
|---|---|
| **ELT con capa bronze** | La respuesta JSON se guarda sin tocar. Si cambia la lógica, se reprocesa sin volver a consultar la API. |
| **Cargas idempotentes** | Correr dos veces el mismo día no duplica datos (delete + insert por `snapshot_date`). |
| **Refresh token persistido en la base** | MercadoLibre rota el refresh token en cada uso; un secret estático de GitHub no alcanza. |
| **Categorías en `categories.yml`** | Agregar una categoría es una línea de configuración, no un cambio de código. |
| **Multiget `/items?ids=`** | Hasta 20 ítems por request: menos llamadas y menos riesgo de superar los límites de consultas. |
| **dbt snapshot para SCD2** | Estrategia `check` sobre precio y estado; estándar de industria y testeable. |
| **Registro de corridas en `ops.pipeline_runs`** | Cada ejecución guarda duración, ítems, errores y respuestas 429. Observabilidad desde el día uno. |

> **Sobre la escala:** el volumen es de miles de filas por día, así que PostgreSQL alcanza de sobra. El diseño por capas y los sinks intercambiables permiten migrar a un lakehouse si el volumen creciera (ver [Roadmap](#roadmap)).

---

## Modelo de datos

| Schema | Tabla | Grano | Descripción |
|---|---|---|---|
| `bronze` | `api_responses` | request | JSON crudo + `run_id`, `category_id`, `fetched_at` |
| `silver` | `stg_item_prices` | ítem × día | Precio, precio original, moneda, estado, vendedor |
| `silver` | `snap_item_prices` | ítem × versión | SCD2 con `dbt_valid_from` / `dbt_valid_to` |
| `gold` | `dim_item` | ítem | Atributos descriptivos de la publicación |
| `gold` | `dim_category` | categoría | Nombre y jerarquía |
| `gold` | `fct_daily_price` | ítem × día | Precio, precio anterior, variación % |
| `gold` | `mart_weekly_movers` | ítem × semana | Mayores bajas y subas por categoría |
| `ops` | `auth_tokens` | 1 fila | Access y refresh token vigentes |
| `ops` | `pipeline_runs` | corrida | Métricas y estado de cada ejecución |

En dbt se mantiene la convención de carpetas (`models/staging`, `snapshots`, `models/marts`) y cada carpeta escribe en su schema desde `dbt_project.yml`.

---

## Plan de acción

### Fase 0: Validar la API (½ día)
- [ ] Crear la app en el DevCenter de MercadoLibre y completar el flujo OAuth
- [ ] Smoke test local de los endpoints (`scripts/smoke_test.py`)
- [ ] Mismo smoke test desde GitHub Actions para confirmar que la IP del runner no está bloqueada
- [ ] Elegir el endpoint para descubrir ítems por categoría (búsqueda o más vendidos)

### Fase 1: Extractor (2–3 días)
- [ ] `auth.py`: refresh y persistencia del token rotativo
- [ ] `client.py`: reintentos con backoff exponencial (429 y 5xx), timeouts, paginación
- [ ] `discovery.py`: ítems a seguir por categoría, leídos de `categories.yml`
- [ ] Multiget en lotes de 20 ítems
- [ ] Tests unitarios con respuestas simuladas

### Fase 2: Carga a bronze (1 día)
- [ ] Neon PostgreSQL con schemas `bronze`, `silver`, `gold` y `ops`
- [ ] `PostgresSink`: insert de respuestas JSON con `run_id`
- [ ] Registro de cada corrida en `ops.pipeline_runs`

### Fase 3: Transformación con dbt (2–3 días)
- [ ] Silver: `stg_item_prices` (idempotente) y `snap_item_prices` (SCD2)
- [ ] Gold: `dim_item`, `dim_category`, `fct_daily_price`, `mart_weekly_movers`
- [ ] Tests dbt: `unique`, `not_null`, relaciones, rangos de precio, frescura

### Fase 4: Orquestación (1 día)
- [ ] `daily.yml`: extracción + `dbt build` con cron diario
- [ ] `ci.yml`: lint (ruff), tests de Python y `dbt build` contra una base de prueba en cada PR
- [ ] Alerta si la corrida diaria falla

### Fase 5: Dashboard (1–2 días)
- [ ] Ranking de bajas y subas con filtro por categoría
- [ ] Evolución de precio por ítem
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
│   ├── discovery.py     # qué ítems seguir por categoría
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
├── categories.yml
└── .github/workflows/
    ├── daily.yml
    └── ci.yml
```

## Stack

Python · requests · PostgreSQL (Neon) · dbt-core · Streamlit · GitHub Actions

---

## Roadmap

**Migración a lakehouse (Databricks).** Una vez terminada la versión actual, el plan es migrarla a Databricks Free Edition para comparar ambos enfoques:

- Un nuevo sink escribe el JSON en un Volume de Unity Catalog; el extractor no cambia
- Las mismas tres capas pasan a tablas Delta: bronze con Auto Loader, silver con AUTO CDC (SCD2), gold con el mismo modelo estrella
- Comparativa final: complejidad, costo y cuándo conviene cada versión

## Nota sobre la API

Desde 2025 los endpoints de MercadoLibre requieren un access token OAuth asociado a un usuario. Este proyecto usa una aplicación registrada en el DevCenter y respeta los límites de consultas de la API.
