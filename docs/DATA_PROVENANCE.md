# Procedencia de datos

## Origen de las fuentes (`sources.provenance`)

- `provider_annotation`: URL de una anotación `url_citation` del proveedor.
- `provider_source_list`: URL de la lista de fuentes de la llamada de
  búsqueda hospedada.
- `synthetic`: solo en ejecuciones `is_synthetic` (fakes). En ejecuciones
  sintéticas cuenta como «fuente del proveedor» en la tabla 4.4 (D6); en
  reales nunca aparece.

**Ninguna URL procede del texto libre del modelo**: solo de anotaciones o
listas de fuentes del proveedor. Las fuentes se deduplican por
`canonical_url` (esquema https preferido, sin fragmento/utm) y se les asigna
`registrable_domain` (tldextract con lista empaquetada, sin descarga).

## Estado de acceso (`access_status`)

`ok`, `redirected`, `blocked` (401/403/paywall), `blocked_by_policy` (esquema,
IP prohibida, robots), `not_found`, `timeout`, `not_checked`. El verificador
HTTP (una GET por URL citada) aplica anti-SSRF, ≤5 redirecciones validadas,
robots con caché, ≤1 req/s/host, tope de tamaño y solo extrae texto
html/plain (sin ejecutar nada). El contenido de la página **no se almacena**:
solo `content_hash` + `excerpt` ≤500; el texto completo vive en memoria.

## Estado de las afirmaciones (4.4)

`verified`, `partially_supported` (respaldo sin confirmar, o respaldo +
contradicción → `disputed`), `contradicted`, `unverified`. Etiquetas de tipo
(`kind`): `fact` requiere refs del proveedor o degrada a `hypothesis`;
`estimate`/`hypothesis` nunca cuentan como evidencia verificada. Cada vínculo
afirmación-fuente guarda `support_type` (supports/contradicts/mentions),
`quote_match` y `judge_verdict`.

## Minimización y datos no confiables

- El contenido web es dato no confiable: se incrusta en prompts acotado y nunca
  altera instrucciones, herramientas ni scoring (test de prompt injection).
- Los prompts nunca incluyen umbrales, pesos ni `requested_ideas` (test).
- Logs JSON sin secretos; `message_safe` en eventos. Trazas SDK off por
  defecto.
- A proveedores externos solo se envía: sector/país/idioma, resúmenes de
  candidatas y texto de páginas citadas (juez de evidencia).
