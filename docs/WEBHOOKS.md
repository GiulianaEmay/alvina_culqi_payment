# Evidencia oficial y webhook pendiente

Consultado el 2026-10-16:

- https://apidocs.culqi.com/
- https://apidocs.culqi.com/apiculqi.yaml
- Guía enlazada por la especificación: https://docs.culqi.com/es/documentacion/pagos-online/webhooks/
- Producto enlazado: https://docs.culqi.com/es/documentacion/pagos-links

SHA256 de la especificación consultada: `5c218a25485777ed8085cd67a4d4b4c4f7ac38b7892b475474e734ddf38d9424`.
No se copia la documentación completa ni sus ejemplos de claves al repositorio.

Verificado en la especificación:

| Operación | Contrato documentado |
| --- | --- |
| Crear link | `POST https://api.culqi.com/v2/links`, respuesta 201 |
| Consultar link | `GET /v2/links/{id}` |
| Listar links | `GET /v2/links`, admite `limit` |
| Desactivar link | `DELETE /v2/links/{id}`; aún no implementado |
| Autenticación | `Authorization: Bearer …`, `x-culqi-product: link` |
| Importe | Entero en céntimos, mínimo 600 y máximo 999900 en creación |
| Solicitud | `amount`, `currency_code`, `concept`, `limit_uses`, `payment_methods`; `is_open_amount=false` |
| Respuesta | `object=link`, `id`, `url`, `amount`, `currency_code`, `concept`, etc. |
| Evento | `GET /v2/events/{id}` con llave privada; no prueba por sí solo la relación con un enlace |

La especificación enumera eventos como `charge.creation.succeeded`, `charge.creation.failed`,
`charge.capture.succeeded` y `order.status.changed`, pero no
se ha confirmado cuál acredita un pago de este enlace ni cómo relacionar el cargo con seguridad.
La guía de webhooks ya es accesible (HTTP 200). Se revisó su contenido: explica
configurar URL y eventos en CulqiPanel, pero no especifica un mecanismo de autenticidad.
**No se ha verificado un algoritmo de firma, cabecera, secreto, ventana de replay ni protocolo de challenge.**
No inventar HMAC ni usar un token en la URL como sustituto del mecanismo oficial.

Ruta reservada: `POST /payment/culqilink/webhook`, responde **503** deliberadamente.
No configurarla aún en Culqi Panel: no acepta ni registra eventos. El HTTPS debe proporcionarlo
el alojamiento/proxy de la instancia, que no se ha inspeccionado.

Antes de activación:

1. Obtener de Culqi documentación oficial adicional que precise autenticidad, payload y política de reintentos. La guía pública ya revisada no los precisa.
2. Obtener documentación o aclaración oficial de asociación enlace/evento/cargo, pago final y sandbox.
3. Implementar validación de autenticidad y consulta API por compañía; nunca confiar en el cuerpo.
4. Implementar bandeja con unicidad y ACL/reglas; resolver duplicados, replay, orden y concurrencia.
5. Registrar en Culqi Panel únicamente la URL HTTPS de **pruebas**, con los eventos confirmados.
6. Probar autenticidad inválida, duplicados y pagos correctos/rechazados/monto diferente mediante mocks;
   después, pruebas sandbox si Culqi confirma disponibilidad. No realizar pruebas live.

El borrador de red añade `apidocs.culqi.com` y `docs.culqi.com` conservando los presets existentes.
Para llamadas reales futuras también será necesario autorizar `api.culqi.com`; no se llamó a la API
con credenciales ni se solicitó una clave. Los secretos se ingresan solo en la configuración segura.

## Resultado de la revisión adicional

Las guías oficiales de [generación](https://docs.culqi.com/es/documentacion/pagos-links/generar-un-link-de-pago),
[links creados](https://docs.culqi.com/es/documentacion/pagos-links/revisar-mis-links-creados) e
[historial](https://docs.culqi.com/es/documentacion/pagos-links/revisar-historial-de-ventas)
explican el uso y los movimientos en el panel. No describen un campo que una un cargo con
el link mediante API. No se asociará un pago por coincidencia de importe, email o concepto.

La consulta autenticada `GET /v2/events/{id}` está documentada para el identificador recibido
por webhook. Permite comprobar que el evento existe bajo esa credencial; no autentica por sí
misma todos los campos del cuerpo recibido ni demuestra su relación con el enlace local.
`status_code` es código HTTP, no evidencia del estado final del pago.

La [documentación general de integración](https://docs.culqi.com/es/documentacion/pagos-online/integracion-vs-produccion)
distingue llaves test/live usando la misma API. La disponibilidad específica de CulqiLink
para pruebas sigue sin confirmarse: sus ejemplos muestran IDs/llaves live.
No se asumirá que una llave test permite usar Links sin comprobarlo en un entorno autorizado.

Culqi no documenta `Idempotency-Key` en el contrato consultado. La aplicación debe persistir
la intención antes de crear un link y conservar cualquier respuesta incierta para revisión;
no puede convertir un timeout en un segundo POST automático.
