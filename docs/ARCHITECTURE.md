# Diagnóstico y arquitectura · 2026-10-16

## Inspección

El repositorio remoto y local no tenían commits, archivos ni rama `main`; el acceso Git de lectura
funciona. Se creó la rama local `development` sin tocar producción. No existían addons previos,
configuración Odoo, base de datos ni indicios de una instancia Enterprise en esta máquina.
Posteriormente se preparó un runtime local de pruebas con Odoo Community 19.0 y PostgreSQL 17.11,
sin instalación del sistema, y se instalaron Ventas/Pagos y el addon. Este runtime no representa
la instancia productiva ni los addons Enterprise de Alviña.
En la inspección inicial no había variables Culqi/Odoo/PostgreSQL identificadas por nombre en el entorno. No se leyeron valores.
No se puede deducir qué módulos están instalados en Alviña a partir de un repositorio vacío.

Se inspeccionó código **oficial de Odoo 19.0**, commit
`5997f8d35b91c5a49048af7785511b8aa388b502` de https://github.com/odoo/odoo/tree/19.0.
No se afirma que ese commit sea el build Enterprise del cliente.

Comprobaciones de API:

- `payment.provider`: `code`, `state` (`disabled`, `test`, `enabled`), `company_id` y métodos.
- `payment.transaction`: `_process`, `_extract_amount_data`, `_apply_updates`, `_set_done`,
  `_set_pending`, `_set_error`, `_set_canceled`, `_post_process`.
- `account_payment` amplía proveedor con `journal_id`, y transacción con `payment_id` e `invoice_ids`.
  Su `_post_process` crea el pago faltante y `_create_payment` contabiliza y concilia.
- `sale` añade `sale_order_ids` y `transaction_ids`; `amount_paid` suma transacciones,
  no necesariamente todos los pagos de facturas ya existentes.
- Odoo 19 dispone de `check_access(operation)` y vistas con expresiones `invisible`.

## Propuesta por fases

1. Contrato CulqiLink y adaptador aislado; pruebas HTTP con respuestas mock.
2. Creación persistente antes de llamadas remotas, reserva de saldo bajo bloqueo, estados
   ambiguos persistentes y recuperación. No reintentar POST tras timeout sin reconciliación.
3. Endpoint con autenticidad documentada; usar evento como aviso y consultar API autenticada.
   Derivar credencial desde una transacción interna, nunca desde compañía/importe del visitante.
4. Comparar cargo final, importe, moneda, referencia, comercio/compañía y enlace.
   Registrar solo metadatos mínimos de evento, con índices únicos y acceso restringido.
5. Bajo bloqueo, aplicar una transición válida y ejecutar el postprocesado estándar.
   Probar la interacción con cron y callbacks concurrentes; no crear un segundo `account.payment`.
6. Reemisión segura, correo, recuperación de eventos, pruebas Enterprise y aprobación separada.

## Incompatibilidades y límites conocidos

No reutilizar los antiguos hooks `_handle_notification_data` de otras versiones como si se
hubieran validado en 19. No equiparar Orders/Checkout con Links. No inferir pago a partir
únicamente del enlace ni de `limit_uses`. La especificación de links no incluye evidencia de
liquidación o relación suficiente con un cargo para completar el flujo contable con seguridad.
No hay sandbox real validado para este producto. Un módulo Python requiere alojamiento compatible.
Las restricciones de edición del onboarding no impiden este trabajo de código: el usuario lo
solicitó explícitamente después del diagnóstico inicial del entorno.

## Verificación adicional en la instancia local

La instalación real resolvió `sale_management` 19.0.1.0, `payment` 19.0.2.0 y
`account_payment` 19.0.2.0. Se verificaron las firmas y el esquema mediante instalación,
no únicamente lectura de código. Se añadieron los hooks nativos `setup_provider` y
`reset_payment_provider`, el método `card` y la inicialización contable estándar.
El índice único de bindings impide compartir un nombre entre proveedores, incluso en carrera.
El postprocesado Culqi adquiere bloqueo de la transacción y refresca el estado; los demás
proveedores conservan su comportamiento nativo. El estado `error` puede recuperarse a `done`
tras una consulta verificada, como en Odoo 19.

Los bloqueos que requieren información externa son ahora la autenticidad y asociación Culqi,
y la verificación final sobre el build Enterprise y alojamiento de Alviña. La documentación
web ya está accesible. La falta inicial de PostgreSQL fue resuelta en esta máquina.
