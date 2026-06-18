"""
CKAN Explorer — herramienta interna para explorar la Action API de datos.gob.ar.
"""

import json
import time
from typing import Any
from urllib.parse import urlencode, urljoin

import pandas as pd
import requests
import streamlit as st

DEFAULT_BASE_URL = "https://datos.gob.ar/api/3/action"
DEFAULT_PACKAGE_ID = "energia-produccion-petroleo-gas-sesco"
TIMEOUT = 20

ENDPOINT_OPTIONS = [
    "status_show",
    "package_search",
    "package_show",
    "package_list",
    "resource_show",
    "resource_search",
    "organization_list",
    "organization_show",
    "group_list",
    "group_show",
    "tag_list",
    "package_autocomplete",
    "organization_autocomplete",
    "group_autocomplete",
    "tag_autocomplete",
]


def build_url(base_url: str, action: str, params: dict[str, Any] | None = None) -> str:
    """Construye la URL final para una acción CKAN con parámetros opcionales."""
    base = base_url.rstrip("/") + "/"
    url = urljoin(base, action)
    clean_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    if clean_params:
        url = f"{url}?{urlencode(clean_params)}"
    return url


def call_ckan_action(
    base_url: str,
    action: str,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, int | None, float, str | None, str]:
    """
    Ejecuta una acción CKAN vía GET.

    Retorna: (payload_json, status_code, elapsed_s, error_msg, final_url)
    """
    final_url = build_url(base_url, action, params)
    try:
        start = time.perf_counter()
        response = requests.get(final_url, timeout=TIMEOUT)
        elapsed = time.perf_counter() - start
        status_code = response.status_code
        try:
            payload = response.json()
        except ValueError:
            return None, status_code, elapsed, "La respuesta no es JSON válido.", final_url
        return payload, status_code, elapsed, None, final_url
    except requests.RequestException as exc:
        return None, None, 0.0, str(exc), final_url


def response_to_download(payload: dict[str, Any], filename: str = "ckan_response.json") -> bytes:
    """Serializa el payload para descarga."""
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def build_python_snippet(url: str) -> str:
    """Genera un snippet Python equivalente con requests."""
    return (
        "import requests\n\n"
        f'url = "{url}"\n'
        f"response = requests.get(url, timeout={TIMEOUT})\n"
        "response.raise_for_status()\n"
        "data = response.json()\n"
        "print(data.get('success'))\n"
    )


def _safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Accede a claves anidadas sin romper si falta algún nivel."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is None:
            return default
    return current


def render_response_meta(
    final_url: str,
    status_code: int | None,
    elapsed: float,
    payload: dict[str, Any] | None,
    error_msg: str | None,
) -> None:
    """Muestra URL, tiempo, status HTTP y errores comunes."""
    st.subheader("Consulta")
    st.code(final_url, language=None)
    cols = st.columns(3)
    cols[0].metric("HTTP status", status_code if status_code is not None else "—")
    cols[1].metric("Tiempo (s)", f"{elapsed:.2f}")
    if payload is not None:
        cols[2].metric("success", str(payload.get("success", "—")))

    if error_msg:
        st.error(f"Error de red o parseo: {error_msg}")
        return

    if payload is not None and payload.get("success") is False:
        st.warning(f"CKAN devolvió success=false: {payload.get('error', payload)}")


def render_utilities(final_url: str, payload: dict[str, Any] | None) -> None:
    """Snippet Python y descarga del JSON."""
    st.subheader("Utilidades")
    with st.expander("Snippet Python (requests)"):
        st.code(build_python_snippet(final_url), language="python")
    if payload is not None:
        st.download_button(
            "Descargar JSON de respuesta",
            data=response_to_download(payload),
            file_name="ckan_response.json",
            mime="application/json",
        )


def render_generic_json(payload: dict[str, Any] | None, result_key: str = "result") -> None:
    """Renderiza resultados genéricos: listas, autocomplete y metadata simple."""
    if payload is None:
        return

    result = payload.get(result_key)
    if result is None:
        st.info("Sin campo 'result' en la respuesta.")
    elif isinstance(result, list):
        st.metric("Cantidad de elementos", len(result))
        if result and isinstance(result[0], dict):
            st.dataframe(pd.DataFrame(result), use_container_width=True, hide_index=True)
        else:
            df = pd.DataFrame({"valor": result})
            st.dataframe(df, use_container_width=True, hide_index=True)
    elif isinstance(result, dict):
        st.json(result)
        packages = result.get("packages") or result.get("datasets")
        if packages:
            st.markdown("**Datasets asociados**")
            st.dataframe(pd.DataFrame(packages), use_container_width=True, hide_index=True)
    else:
        st.write(result)

    with st.expander("JSON completo"):
        st.json(payload)


def render_package_search(payload: dict[str, Any] | None) -> None:
    """Renderiza resultados de package_search."""
    if payload is None:
        return

    result = payload.get("result") or {}
    count = result.get("count")
    if count is not None:
        st.metric("count", count)

    packages = result.get("results") or []
    if packages:
        rows = []
        for pkg in packages:
            rows.append(
                {
                    "id": pkg.get("id"),
                    "name": pkg.get("name"),
                    "title": pkg.get("title"),
                    "organization": _safe_get(pkg, "organization", "title"),
                    "metadata_modified": pkg.get("metadata_modified"),
                    "resources": len(pkg.get("resources") or []),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No se encontraron datasets.")

    with st.expander("JSON completo"):
        st.json(payload)


def render_package_show(payload: dict[str, Any] | None) -> None:
    """Renderiza resultados de package_show."""
    if payload is None:
        return

    pkg = payload.get("result") or {}
    cols = st.columns(2)
    cols[0].markdown(f"**title:** {pkg.get('title', '—')}")
    cols[0].markdown(f"**name:** {pkg.get('name', '—')}")
    cols[0].markdown(f"**id:** {pkg.get('id', '—')}")
    cols[1].markdown(f"**organization:** {_safe_get(pkg, 'organization', 'title', default='—')}")
    cols[1].markdown(f"**metadata_created:** {pkg.get('metadata_created', '—')}")
    cols[1].markdown(f"**metadata_modified:** {pkg.get('metadata_modified', '—')}")

    tags = [t.get("name") for t in (pkg.get("tags") or []) if isinstance(t, dict)]
    st.markdown(f"**tags:** {', '.join(tags) if tags else '—'}")

    resources = pkg.get("resources") or []
    st.metric("Cantidad de resources", len(resources))

    if resources:
        res_rows = []
        for res in resources:
            res_rows.append(
                {
                    "id": res.get("id"),
                    "name": res.get("name"),
                    "format": res.get("format"),
                    "url": res.get("url"),
                    "created": res.get("created"),
                    "last_modified": res.get("last_modified"),
                    "size": res.get("size"),
                    "datastore_active": res.get("datastore_active"),
                }
            )
        df_resources = pd.DataFrame(res_rows)
        st.dataframe(df_resources, use_container_width=True, hide_index=True)

        st.download_button(
            "Descargar tabla de resources (CSV)",
            data=df_resources.to_csv(index=False).encode("utf-8"),
            file_name="package_resources.csv",
            mime="text/csv",
        )

        with st.expander("URLs de descarga por resource"):
            for res in resources:
                url = res.get("url") or ""
                label = res.get("name") or res.get("id") or "resource"
                st.text_input(f"{label} ({res.get('format', '')})", value=url, key=f"url_{res.get('id')}")

    with st.expander("JSON completo"):
        st.json(payload)


def render_resource_show(payload: dict[str, Any] | None) -> None:
    """Renderiza resultados de resource_show."""
    if payload is None:
        return

    res = payload.get("result") or {}
    fields = [
        ("id", res.get("id")),
        ("name", res.get("name")),
        ("format", res.get("format")),
        ("url", res.get("url")),
        ("package_id", res.get("package_id")),
        ("created", res.get("created")),
        ("last_modified", res.get("last_modified")),
        ("size", res.get("size")),
        ("datastore_active", res.get("datastore_active")),
    ]
    for label, value in fields:
        st.markdown(f"**{label}:** {value if value is not None else '—'}")

    if res.get("url"):
        st.text_input("URL de descarga", value=res["url"], key="resource_show_url")

    with st.expander("JSON completo"):
        st.json(payload)


def render_resource_search(payload: dict[str, Any] | None) -> None:
    """Renderiza resultados de resource_search."""
    if payload is None:
        return

    result = payload.get("result") or {}
    count = result.get("count")
    if count is not None:
        st.metric("count", count)

    resources = result.get("results") or []
    if resources:
        st.dataframe(pd.DataFrame(resources), use_container_width=True, hide_index=True)
    else:
        st.info("No se encontraron resources.")

    with st.expander("JSON completo"):
        st.json(payload)


def collect_params(action: str) -> dict[str, Any]:
    """Recolecta parámetros del sidebar según la acción seleccionada."""
    params: dict[str, Any] = {}

    if action == "package_search":
        params["q"] = st.session_state.get("param_q", "")
        params["rows"] = st.session_state.get("param_rows", 10)
        params["start"] = st.session_state.get("param_start", 0)
        sort = st.session_state.get("param_sort", "")
        fq = st.session_state.get("param_fq", "")
        if sort:
            params["sort"] = sort
        if fq:
            params["fq"] = fq

    elif action == "package_show":
        params["id"] = st.session_state.get("param_dataset_id", DEFAULT_PACKAGE_ID)

    elif action in ("package_list", "organization_list", "group_list", "tag_list"):
        limit = st.session_state.get("param_limit", 0)
        offset = st.session_state.get("param_offset", 0)
        if limit and int(limit) > 0:
            params["limit"] = int(limit)
        if offset and int(offset) > 0:
            params["offset"] = int(offset)

    elif action == "resource_show":
        resource_id = st.session_state.get("param_resource_id", "")
        if resource_id:
            params["id"] = resource_id

    elif action == "resource_search":
        params["query"] = st.session_state.get("param_query", "")

    elif action in ("organization_show", "group_show"):
        entity_id = st.session_state.get("param_entity_id", "")
        if entity_id:
            params["id"] = entity_id

    elif action in ("package_autocomplete", "organization_autocomplete", "group_autocomplete"):
        params["q"] = st.session_state.get("param_autocomplete_q", "")

    elif action == "tag_autocomplete":
        params["query"] = st.session_state.get("param_tag_query", "")

    return params


def render_sidebar_inputs(action: str) -> None:
    """Renderiza inputs dinámicos en el sidebar."""
    if action == "package_search":
        st.text_input("q (búsqueda)", value="sesco", key="param_q")
        st.number_input("rows", min_value=1, max_value=1000, value=10, key="param_rows")
        st.number_input("start (offset)", min_value=0, value=0, key="param_start")
        st.text_input("sort (opcional)", value="", key="param_sort")
        st.text_input("fq (filtro opcional)", value="", key="param_fq")

    elif action == "package_show":
        st.text_input(
            "dataset_id",
            value=DEFAULT_PACKAGE_ID,
            key="param_dataset_id",
        )

    elif action in ("package_list", "organization_list", "group_list", "tag_list"):
        st.number_input("limit (opcional)", min_value=0, value=0, key="param_limit")
        st.number_input("offset (opcional)", min_value=0, value=0, key="param_offset")
        st.caption("Dejar en 0 para no enviar el parámetro.")

    elif action == "resource_show":
        st.text_input("resource_id", value="", key="param_resource_id")

    elif action == "resource_search":
        st.text_input("query", value="name:sesco", key="param_query")

    elif action in ("organization_show", "group_show"):
        st.text_input("id", value="", key="param_entity_id")

    elif action in ("package_autocomplete", "organization_autocomplete", "group_autocomplete"):
        st.text_input("q", value="sesco", key="param_autocomplete_q")

    elif action == "tag_autocomplete":
        st.text_input("query", value="energia", key="param_tag_query")

    elif action == "status_show":
        st.caption("Sin parámetros.")


def main() -> None:
    st.set_page_config(
        page_title="CKAN Explorer — datos.gob.ar",
        page_icon="🔍",
        layout="wide",
    )

    st.title("CKAN Explorer — datos.gob.ar")
    st.markdown(
        "Herramienta interna para explorar endpoints CKAN usados por el proyecto SESCO."
    )

    with st.expander("¿Qué es cada endpoint?"):
        st.markdown(
            """
            - **package_search** busca datasets.
            - **package_show** muestra el detalle completo de un dataset.
            - **resource_show** muestra un recurso puntual.
            - **resource_search** busca recursos.
            - **organization_*** trabaja con organismos publicadores.
            - **group_*** trabaja con grupos/categorías.
            - **tag_*** trabaja con etiquetas.

            CKAN no debe confundirse con la fuente final del CSV: funciona como **catálogo**
            para descubrir recursos y URLs de descarga.
            """
        )

    with st.expander("Flujo recomendado para SESCO"):
        st.markdown(
            """
            ```
            package_search
               ↓
            package_show
               ↓
            resources[]
               ↓
            seleccionar recurso CSV
               ↓
            descargar URL
               ↓
            procesar con pandas
            ```
            """
        )

    st.sidebar.header("Configuración")
    base_url = st.sidebar.text_input(
        "Base URL",
        value=DEFAULT_BASE_URL,
        key="base_url",
    )
    action = st.sidebar.selectbox("Endpoint / acción CKAN", ENDPOINT_OPTIONS, key="action")

    st.sidebar.subheader("Parámetros")
    render_sidebar_inputs(action)

    consultar = st.sidebar.button("Consultar", type="primary", use_container_width=True)

    if consultar:
        params = collect_params(action)
        payload, status_code, elapsed, error_msg, final_url = call_ckan_action(
            base_url, action, params
        )
        st.session_state["last_payload"] = payload
        st.session_state["last_status"] = status_code
        st.session_state["last_elapsed"] = elapsed
        st.session_state["last_error"] = error_msg
        st.session_state["last_url"] = final_url
        st.session_state["last_action"] = action

    if "last_url" in st.session_state:
        render_response_meta(
            st.session_state["last_url"],
            st.session_state.get("last_status"),
            st.session_state.get("last_elapsed", 0.0),
            st.session_state.get("last_payload"),
            st.session_state.get("last_error"),
        )
        render_utilities(
            st.session_state["last_url"],
            st.session_state.get("last_payload"),
        )

        last_action = st.session_state.get("last_action", action)
        last_payload = st.session_state.get("last_payload")

        st.subheader("Resultado")
        if last_action == "package_search":
            render_package_search(last_payload)
        elif last_action == "package_show":
            render_package_show(last_payload)
        elif last_action == "resource_show":
            render_resource_show(last_payload)
        elif last_action == "resource_search":
            render_resource_search(last_payload)
        else:
            render_generic_json(last_payload)


if __name__ == "__main__":
    main()
