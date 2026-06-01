# Exploración de datos SESCO

Etapa exploratoria del proyecto **Monitor de Producción Hidrocarburífera Argentina**. El objetivo es entender la estructura real de los datos publicados en [datos.gob.ar](https://datos.gob.ar/dataset/energia-produccion-petroleo-gas-sesco) antes de avanzar con API, base de datos o dashboard final.

> **Nota:** `backend/` y `frontend/` quedan **pausados** hasta completar este análisis. No se modifican en esta etapa.

## Objetivo

- Inventariar recursos CKAN del dataset SESCO.
- Descargar CSVs candidatos para el MVP.
- Inspeccionar columnas, tipos, nulos y rangos temporales.
- Proponer un modelo analítico tentativo.
- Generar gráficos exploratorios iniciales.
- Documentar conclusiones para el primer dashboard (Streamlit).

## Fuente oficial

- API CKAN: `https://datos.gob.ar/api/3/action/package_show?id=energia-produccion-petroleo-gas-sesco`
- Dataset: [Energía — Producción de petróleo y gas — SESCO](https://datos.gob.ar/dataset/energia-produccion-petroleo-gas-sesco)

## Estructura

```
exploration/
├── notebooks/
│   └── 01_exploracion_sesco.ipynb   # Análisis exploratorio principal
├── scripts/
│   ├── inspect_ckan_resources.py    # Lista recursos y guarda metadata
│   └── download_sesco_resource.py   # Descarga un recurso por URL
├── data/
│   ├── raw/                         # CSVs descargados (gitignored)
│   └── processed/                   # Metadata y derivados (gitignored)
├── requirements.txt
└── README.md
```

## Instalación de dependencias

Desde la raíz del repositorio:

```bash
cd exploration
python -m venv .venv

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

Dependencias principales: `pandas`, `requests`, `matplotlib`, `jupyter`.

## Ejecutar scripts

### 1. Inspeccionar recursos CKAN

Lista todos los recursos del dataset en consola y guarda metadata en `data/processed/ckan_resources.csv`:

```bash
python scripts/inspect_ckan_resources.py
```

### 2. Descargar un recurso

Descarga un archivo por URL en `data/raw/`:

```bash
python scripts/download_sesco_resource.py "http://datos.energia.gob.ar/dataset/.../download/archivo.csv"
```

Con nombre de archivo personalizado:

```bash
python scripts/download_sesco_resource.py "http://..." produccion_petroleo_provincia.csv
```

## Abrir la notebook

Con el entorno virtual activado:

```bash
jupyter notebook notebooks/01_exploracion_sesco.ipynb
```

Alternativa con VS Code / Cursor: abrir el archivo `.ipynb` y seleccionar el kernel de `exploration/.venv`.

## Primer dashboard MVP (Streamlit)

Instalar dependencias de visualización:

```bash
pip install streamlit plotly
```

Ejecutar el dashboard:

```bash
streamlit run exploration/streamlit_app/app.py
```

## Flujo recomendado

1. Ejecutar `inspect_ckan_resources.py` para ver el catálogo completo.
2. Abrir `01_exploracion_sesco.ipynb` y ejecutar celdas en orden.
3. Revisar la sección final de conclusiones antes de retomar `backend/` o `frontend/`.

## Datos descargados

Los archivos en `data/raw/` y `data/processed/` no se versionan (ver `.gitignore`). Cada desarrollador los genera localmente con los scripts o la notebook.
