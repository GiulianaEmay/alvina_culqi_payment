# Evidencia de validación · 2026-10-16

## Entorno real utilizado

- Odoo 19.0 público oficial, commit `5997f8d35b91c5a49048af7785511b8aa388b502`.
- Python 3.12.14; PostgreSQL 17.11; Debian 13 amd64.
- Runtime y cluster locales, aislados de producción; sin claves Culqi.
- Módulos: `sale_management` 19.0.1.0, `payment` 19.0.2.0,
  `account_payment` 19.0.2.0 y `alvina_culqi_payment` 19.0.0.1.0.

## Resultados completados

| Validación | Resultado |
| --- | --- |
| Contrato/cliente REST con transporte simulado | 22 pruebas, salida 0 |
| Actualización del addon y suite ORM | 21 pruebas, 0 fallos, 0 errores, salida 0 |
| Instalación limpia desde base vacía | 57 módulos instalados y 21 pruebas, salida 0 |
| Script completo `scripts/prepare-environment.sh` | Ejecutado de nuevo; instalación de dependencias y 21 pruebas ORM aprobadas |
| Python, XML, manifiesto y ACL | Cargados por el registro real de Odoo |

Las 21 pruebas ORM son distintas de las 22 unitarias. Repetir instalación/actualización no
incrementa el número de casos distintos. Odoo muestra además «29 tests» en estadísticas de
setup; su resultado final documentado es **21 pruebas de test**, que es el conteo informado aquí.

La suite ORM cubre generación en cola por un vendedor sin acceso de escritura a pagos,
validación de importe, enlace devuelto, borrador de correo, solicitud duplicada, timeout incierto
sin segundo POST, interrupción y cambio de saldo antes del envío, restricción del cron,
confirmación/rechazo con adaptador, discrepancias, errores de red y credenciales,
recuperación error→done, binding multiempresa y permisos.

La contabilización y conciliación se ejecutan mediante Odoo real, sin sustituir esos métodos:
se verifica asiento posted, importe residual cero y un único `account.payment`.
Dos conexiones PostgreSQL independientes cargan la misma transacción antes de competir por
el postprocesado; la conexión perdedora reintenta una `SerializationFailure` y ambas observan
el mismo pago. El ERROR SQL esperado de esa colisión aparece en el log y no es un fallo del test.
La limpieza de esa fixture en la instalación limpia dejó cero pagos o transacciones concurrentes.

Evidencia de ejecución (logs copiados con espacios finales de línea eliminados):

- [Unitarias](evidence/unit-tests.txt)
- [Actualización](evidence/odoo-upgrade.log)
- [Instalación limpia](evidence/odoo-fresh-install.log)

Comandos utilizados (los scripts del repositorio conservan el contenido de los helpers probados):

```sh
bash scripts/setup-tests.sh
bash /workspace/culqi-runtime/run-addon-tests.sh -u
CULQI_TEST_DATABASE=culqi19_fresh bash /workspace/culqi-runtime/run-addon-tests.sh -i
bash scripts/prepare-environment.sh
```

## Límites pendientes

La generación y confirmación usan Culqi simulado. No se ha hecho un cobro real ni sandbox.
Las pruebas de callback interno repetido no equivalen a recibir webhooks autenticados: el endpoint
público sigue devolviendo 503 y su implementación final depende del contrato oficial faltante.
Las guías públicas ya son accesibles, pero no documentan autenticidad ni asociación enlace/cargo.

No se han probado addons privados Enterprise, alojamiento o build de Alviña, SMTP real,
comisiones/reconciliación de abonos Culqi ni saldo de pedidos ya facturados. Estos últimos siguen
rechazados por diseño. Reemisión y cancelación remota requieren implementación posterior.
El proveedor y su cron permanecen deshabilitados. La validación se ejecutó localmente, sin despliegue ni reinicio productivo.
