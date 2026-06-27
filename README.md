# Monitor de Producción Hidrocarburífera Argentina — SESCO

También referido como **Oil & Gas Dashboard MVP**.

## 1. Descripción general

Este proyecto busca construir un **dashboard** y, en etapas posteriores, una **plataforma de análisis** sobre la producción hidrocarburífera argentina, apoyándose en datos públicos oficiales del dataset **SESCO** (Secretaría de Energía / [Datos Argentina](https://datos.gob.ar)).

El trabajo avanza por etapas:

1. **Exploración de datos** con notebooks Jupyter.
2. **Dashboard rápido** con Streamlit (prototipo funcional).
3. **PostgreSQL / PostGIS** como almacenamiento espacial y analítico.
4. **FastAPI** como capa de API.
5. **React + MapLibre** como frontend definitivo.

**Estado actual:** la etapa implementada y validada es [`exploration/`](exploration/). Las carpetas [`backend/`](backend/) y [`frontend/`](frontend/) existen como base para fases futuras, pero **no** constituyen el núcleo operativo del MVP en este momento.

## 2. Fuente de datos

Se utiliza el dataset oficial **Producción de Petróleo y Gas — SESCO**.

La metadata y los recursos descargables se consultan vía la API CKAN de Datos Argentina:

https://datos.gob.ar/api/3/action/package_show?id=energia-produccion-petroleo-gas-sesco

**CKAN** se usa para descubrir recursos del paquete, leer metadatos (nombre, formato, fechas) y obtener las **URLs actuales** de descarga de cada CSV, sin depender de enlaces fijos que puedan cambiar.

## 3. Recursos usados en el MVP

En la etapa de exploración se trabajaron estos **seis recursos**:

| Recurso |
|--------|
| Producción de petróleo promedio diaria por provincia |
| Producción de gas promedio diaria por provincia |
| Producción de petróleo promedio diaria por cuenca |
| Producción de gas promedio diaria por cuenca |
| Producción de petróleo promedio diaria por empresa |
| Producción de gas promedio diaria por empresa |

Permiten analizar la producción por **provincia**, **cuenca** y **empresa**, siempre como **vistas alternativas** del mismo fenómeno (no acumulables entre sí).

## 4. Estado actual del proyecto

| Etapa | Estado |
|-------|--------|
| Exploración individual (6 recursos) | Completada |
| Modelo unificado | Completado |
| Validación final | Completada |
| Dashboard Streamlit | Primer MVP funcional |
| PostgreSQL / PostGIS | Pendiente |
| FastAPI | Pendiente |
| React + MapLibre | Pendiente |

## 5. Estructura del repositorio

```
.
├── backend/                 # Base FastAPI + ETL (futuro, no validado como MVP)
├── frontend/                # Base React (futuro, no validado como MVP)
├── exploration/             # Etapa principal actual
│   ├── notebooks/
│   ├── scripts/
│   ├── data/
│   │   ├── raw/
│   │   └── processed/
│   └── streamlit_app/
├── docs/
└── README.md
```

`backend/` y `frontend/` están presentes en el repositorio, pero **todavía no** son el núcleo validado del MVP. El flujo de datos y visualización que hoy funciona de punta a punta vive en `exploration/`.

## 6. Sección `exploration/`

[`exploration/`](exploration/) concentra notebooks, scripts, datos procesados y el dashboard Streamlit.

### Notebooks

| Notebook | Rol |
|----------|-----|
| [`exploration/notebooks/01_exploracion_sesco.ipynb`](exploration/notebooks/01_exploracion_sesco.ipynb) | Exploración inicial: CKAN, primer recurso (petróleo por provincia), normalización y gráficos. |
| [`exploration/notebooks/02_modelo_unificado_sesco.ipynb`](exploration/notebooks/02_modelo_unificado_sesco.ipynb) | Procesamiento de los seis recursos MVP y generación del dataset unificado. |
| [`exploration/notebooks/03_validacion_final_sesco.ipynb`](exploration/notebooks/03_validacion_final_sesco.ipynb) | Validaciones finales, últimos períodos válidos y archivos auxiliares para el dashboard. |

### Scripts

| Script | Rol |
|--------|-----|
| [`exploration/scripts/inspect_ckan_resources.py`](exploration/scripts/inspect_ckan_resources.py) | Inspecciona recursos disponibles en CKAN. |
| [`exploration/scripts/download_sesco_resource.py`](exploration/scripts/download_sesco_resource.py) | Descarga recursos CSV desde las URLs del paquete. |
| [`exploration/scripts/sesco_processing.py`](exploration/scripts/sesco_processing.py) | Funciones reutilizables de procesamiento, normalización, validación y exportación. |
| [`exploration/scripts/run_mvp_processing.py`](exploration/scripts/run_mvp_processing.py) | Ejecuta el pipeline MVP de procesamiento desde consola. |

La documentación detallada de notebooks y decisiones de exploración se mantiene **fuera** de este README (ver [§13](#13-documentación-adicional)).

## 7. Dataset procesado principal

### Archivo principal

[`exploration/data/processed/sesco_produccion_model_clean.csv`](exploration/data/processed/sesco_produccion_model_clean.csv)

Columnas principales del modelo unificado:

| Columna | Descripción |
|---------|-------------|
| `periodo_str` | Período en formato `YYYY-MM` |
| `periodo_dt` | Fecha del período |
| `anio`, `mes` | Componentes temporales |
| `producto` | `petroleo` o `gas` |
| `agrupador_tipo` | `provincia`, `cuenca` o `empresa` |
| `agrupador_nombre` | Nombre del agrupador |
| `tipo_recurso` | Tipo de medición (p. ej. promedio diario) |
| `produccion` | Valor de producción |
| `source_resource` | Recurso CKAN de origen (trazabilidad) |

Este archivo es la **base del dashboard Streamlit**.

### Archivos auxiliares

| Archivo | Uso |
|---------|-----|
| [`sesco_validaciones_resumen.csv`](exploration/data/processed/sesco_validaciones_resumen.csv) | Resumen de validaciones por recurso. |
| [`sesco_latest_periods_by_view.csv`](exploration/data/processed/sesco_latest_periods_by_view.csv) | Último período válido por producto y tipo de agrupador. |
| [`sesco_totales_por_vista_resumen.csv`](exploration/data/processed/sesco_totales_por_vista_resumen.csv) | Comparación de totales entre provincia, cuenca y empresa para **control de consistencia** (no para sumarlos). |
| [`sesco_dashboard_config.csv`](exploration/data/processed/sesco_dashboard_config.csv) | Configuración y resumen para el dashboard. |

## 8. Reglas metodológicas importantes

- **No sumar** provincia + cuenca + empresa: son vistas distintas del mismo fenómeno.
- Para **totales nacionales**, usar **una sola** vista; se recomienda **provincia**.
- El **último período válido** se calcula por `producto` + `agrupador_tipo`.
- Los **períodos incompletos** se excluyen de KPIs y gráficos principales.
- **Petróleo y gas** pueden tener unidades distintas.
- **No comparar** petróleo y gas en el mismo eje salvo con base 100 o gráficos separados.
- Se conserva **trazabilidad** con la columna `source_resource`.
- **No convertir** valores nulos de producción a cero sin justificación explícita.

## 9. Dashboard Streamlit

Primer MVP visual en [`exploration/streamlit_app/app.py`](exploration/streamlit_app/app.py).

Permite:

- filtrar por **producto** (petróleo / gas);
- elegir **agrupador** (provincia, cuenca o empresa);
- seleccionar **agrupadores** concretos;
- definir **rango temporal**;
- ver **KPIs** (último período válido, totales, variación);
- ver **evolución mensual**;
- ver **rankings** (Top N);
- ver **evolución Top 5** (últimos períodos);
- **descargar** datos filtrados en CSV.

Ejecución:

```bash
streamlit run exploration/streamlit_app/app.py
```

Es un **prototipo funcional** para validar el modelo de datos y las visualizaciones antes de migrar a PostgreSQL, FastAPI y React.

## 10. Instalación y ejecución

Desde la raíz del repositorio:

**Crear entorno virtual:**

```bash
python -m venv .venv
```

**Activar (Windows — Git Bash):**

```bash
source .venv/Scripts/activate
```

**Activar (Windows — PowerShell):**

```powershell
.\.venv\Scripts\Activate.ps1
```

**Instalar dependencias de exploración:**

```bash
pip install -r exploration/requirements.txt
```

Incluye `pandas`, `requests`, `matplotlib`, `jupyter`, `geopandas`, `shapely`, `plotly`, `folium` y `streamlit`.

**Levantar el dashboard:**

```bash
streamlit run exploration/streamlit_app/app.py
```

Los CSV procesados deben existir en `exploration/data/processed/` (versionados en el repo o regenerados con el pipeline; ver [§10.1](#101-actualización-de-datos)).

## 10.1 Actualización de datos

El dashboard **no descarga desde CKAN** en tiempo de ejecución: lee archivos en `exploration/data/processed/`.

**Actualización manual** (desde la raíz del repositorio):

```bash
python exploration/scripts/run_mvp_processing.py --update-raw
```

Consulta CKAN, crea snapshot raw solo si hay cambios, procesa los 6 recursos MVP y regenera `processed/` (incluye auxiliares del dashboard).

**Actualización automática diaria:** GitHub Actions (workflow `.github/workflows/update_sesco_data.yml`) ejecuta el mismo comando y hace commit/push solo si detecta cambios en `exploration/data/processed/` o `exploration/data/raw/latest/`. Horario programado: **06:00** hora de Argentina (`America/Argentina/Buenos_Aires`, UTC-3). También disponible manualmente desde la pestaña **Actions** → **Update SESCO data** → **Run workflow**.

**Limitación:** los schedules de GitHub Actions usan UTC y pueden retrasarse varios minutos (o más en repos inactivos); no garantizan ejecución exacta al minuto.

### Política de versionado de datos

| Ruta | ¿Versionado? | Motivo |
|------|--------------|--------|
| `exploration/data/processed/` | Sí | Necesario para Streamlit Community Cloud sin CKAN en runtime |
| `exploration/data/raw/latest/` | Sí | Manifest + 6 CSV MVP (~3 MB); trazabilidad del último snapshot |
| `exploration/data/raw/cuencas_sedimentarias_*.csv` | Sí | Fuentes geo estáticas (pequeñas) |
| `exploration/data/raw/snapshots/` | No | Histórico duplicado; crece con el tiempo |
| `exploration/data/raw/*.csv` (nombres obsoletos) | No | Descargas antiguas con nombres no canónicos |

## 10.2 Despliegue en Streamlit Community Cloud

1. Subir el repositorio a GitHub (rama principal con `exploration/data/processed/` presente).
2. En [share.streamlit.io](https://share.streamlit.io), conectar el repositorio.
3. **Main file path:** `exploration/streamlit_app/app.py`
4. **Requirements file:** `exploration/requirements.txt`
5. Desplegar y verificar que el dashboard carga KPIs y gráficos.
6. Opcional: habilitar el workflow **Update SESCO data** en GitHub Actions para mantener los datos al día (requiere permisos de escritura en el repo para el bot de Actions).

Tras cada actualización automática de datos, Streamlit Community Cloud puede requerir unos minutos o un redeploy manual para reflejar CSV nuevos (la caché de `@st.cache_data` usa TTL de 3600 s).

## 11. Flujo recomendado de trabajo

1. Revisar o ejecutar los notebooks en `exploration/notebooks/` (opcional si se usa el CLI).
2. Regenerar datasets con `python exploration/scripts/run_mvp_processing.py --update-raw` si los datos fuente cambiaron.
3. Ejecutar el dashboard Streamlit.
4. Validar visualmente KPIs, rankings y series.
5. Publicar en GitHub y conectar Streamlit Community Cloud (ver [§10.2](#102-despliegue-en-streamlit-community-cloud)).
6. Solo entonces avanzar con base de datos, API y frontend definitivo.

## 12. Próximos pasos

- Mejorar el dashboard Streamlit (filtros, UX, notas de unidades).
- Documentar **unidades de medida** por producto y recurso.
- Diseñar modelo **PostgreSQL / PostGIS**.
- Implementar **FastAPI** sobre el modelo validado.
- Desarrollar **frontend React + MapLibre** e incorporar **mapas**.

## 13. Documentación adicional

Documentación existente en el repositorio:

| Documento | Contenido |
|-----------|-----------|
| [`docs/mvp-plan.md`](docs/mvp-plan.md) | Alcance del MVP original, stack objetivo y roadmap. |
| [`docs/revision_documentacion_exploration.md`](docs/revision_documentacion_exploration.md) | Revisión y consolidación de la documentación de la etapa `exploration/`. |

No existe actualmente `docs/exploration-sesco.md` en el repositorio; puede crearse más adelante como guía dedicada a la etapa de exploración.

---

**Licencia / datos:** los datos provienen de la Secretaría de Energía — Argentina. Consultar términos de uso en [datos.gob.ar](https://datos.gob.ar).
