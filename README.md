# alvina_culqi_payment

Integración CulqiLink de Grupo Alviña para Odoo 19.

**Estado: implementación parcial de desarrollo. No habilitar para cobros reales.**
Rama de trabajo: `development`. No se ha desplegado ni instalado en producción.

El addon está en `alvina_culqi_payment/`; agregar **la raíz de este repositorio** al `addons_path`.
Extiende modelos nativos y no requiere módulos de terceros ni licencias adicionales al Odoo existente.
La compatibilidad de instalación con Enterprise aún requiere validación en una copia de pruebas del build de Alviña.

## Implementado

- Addon instalado y actualizado en Odoo 19.0 con PostgreSQL 17.11; proveedor y cron deshabilitados.
- Hooks nativos de proveedor, método de tarjeta y configuración contable estándar.
- Cliente REST basado en la especificación oficial: creación de enlaces fijos de un uso,
  tarjeta, PEN/USD; consulta de conexión de solo lectura.
- Validación de respuesta, importe, moneda, concepto y URL HTTPS de `express.culqi.com`.
- Timeouts, TLS verificado, rechazo de redirecciones y errores sin cuerpos ni credenciales.
- Cola persistente de generación: registra el intento antes del POST, conserva resultados
  inciertos y evita reintentar o crear otra solicitud para el mismo pedido.
- Copiar/abrir el enlace y preparar un correo mediante el compositor estándar, con control de acceso.
- Confirmación verificada mediante adaptador simulado y bloqueo de fila también en el
  postprocesado nativo: contabilización y conciliación delegadas a `account_payment`.
- Prueba real de concurrencia con dos conexiones PostgreSQL: un único pago contabilizado.
- Bloqueo de activación/publicación y webhook que responde 503 sin leer ni persistir el cuerpo.
- Credenciales: se almacena únicamente el **nombre** de un binding de entorno por compañía,
  restringido a administradores. No se almacena una clave en Odoo ni en este repositorio.

El botón de generación permanece bloqueado por el proveedor deshabilitado. Es intencional:
no se presenta un enlace creado como pago confirmado. El cliente REST se prueba con mocks.
Para pedidos facturados se rechaza la generación hasta implementar saldo residual y asociación
segura entre factura/pedido/pagos ya contabilizados. No usar `amount_total - amount_paid`
como sustituto general del saldo contable.

## Dependencias y permisos

Dependencias directas: `sale_management`, `account_payment`; incluyen `sale`, `account`, `payment`,
`mail` y sus dependencias estándar. Python `requests` ya forma parte de Odoo.
Odoo 19 oficial declara Python >=3.10 y PostgreSQL >=13; seguir las dependencias del build real.
Se necesita Odoo.sh o una instalación que permita addons Python personalizados; confirmar
la modalidad de alojamiento antes de una instalación. No se ha verificado el alojamiento de Alviña.

Se añade `alvina.culqi.link.request`, con ACL y reglas por compañía y vendedor:
Ventas lee solicitudes de sus pedidos; responsables de Ventas leen todas dentro de sus
compañías permitidas; administración gestiona el modelo. La acción eleva privilegios
únicamente después de validar el pedido y deriva compañía/importe desde Odoo.
Las solicitudes intentadas no pueden eliminarse, vaciar su marca de intento ni volver a la cola.
Ventas requiere `sales_team.group_sale_salesman`; configuración del binding y consulta
administrativa requieren `base.group_system`, además de los permisos estándar de proveedor.
La configuración de proveedor/diario está separada por `company_id`; no compartir bindings
entre compañías. Un índice único impide reutilizar un binding entre proveedores. Las pruebas
ORM verifican permisos de vendedor, rechazo de pedidos de otra compañía y separación de bindings.

## Pruebas locales reproducibles

En esta máquina Debian 13 amd64, desde la raíz del repositorio:

```sh
# Suite aislada del contrato y cliente HTTP (22 pruebas):
bash scripts/setup-tests.sh
# Preparar/refrescar Odoo y PostgreSQL locales; instalar/actualizar el addon y probar:
bash scripts/prepare-environment.sh
# Repetir solo las pruebas ORM con actualización de esquema:
bash scripts/run-odoo-tests.sh -u
```

La preparación instala las dependencias en `/workspace`, verifica metadatos firmados de Debian,
extrae PostgreSQL sin instalación del sistema y usa las dependencias oficiales del commit
Odoo fijado. Los servicios solo escuchan en loopback. El cluster es desechable y usa confianza
local, sin credenciales reales; no usarlo como plantilla de producción. `culqi19_*` son bases
de pruebas; no proporcionar el nombre de una base productiva al runner.
Los procesos no se conservan entre tareas: la preparación comprueba y arranca PostgreSQL.
No se necesitan claves Culqi. Ver [evidencia](docs/VALIDATION.md).

## Instalación únicamente en una base desechable

1. Confirmar Odoo 19 exacto, addons Enterprise disponibles, módulos instalados y permiso para
   addons personalizados. Revisar [diagnóstico y arquitectura](docs/ARCHITECTURE.md).
2. Respaldar base y filestore de pruebas. Incorporar el repositorio al `addons_path` sin
   sustituir las rutas de addons existentes. No instalar Odoo Enterprise desde fuentes ajenas.
3. En una máquina de pruebas ya preparada con PostgreSQL y dependencias oficiales, ejecutar:

```sh
/path/to/odoo-venv/bin/python /path/to/odoo/odoo-bin \
  --addons-path=/path/to/odoo/addons,/path/to/enterprise,/path/to/this-repository \
  -d alvina_culqi_test -i alvina_culqi_payment --without-demo=True \
  --test-enable --test-tags /alvina_culqi_payment --stop-after-init
```

Las rutas son ejemplos para adaptar a la instalación de pruebas, no comandos ya verificados.
No pasar claves por CLI ni guardarlas en `.env` versionados. El proveedor debe quedar deshabilitado.
No abrir un endpoint público ni configurar eventos hasta resolver [webhooks](docs/WEBHOOKS.md).

## Pendiente para completar la entrega

- Autenticidad documentada del webhook y vínculo inequívoco enlace/cargo/evento.
- Adaptador que consulte el cargo/evento con credencial de la compañía y pruebe pago final.
- Bandeja de eventos autenticados y límites/reintentos de recepción. La cola de creación de
  enlaces ya está implementada; las solicitudes inciertas requieren reconciliación manual
  hasta contar con el contrato remoto que permita recuperarlas con seguridad.
- Validación de estado sandbox/live y disponibilidad real de sandbox para CulqiLink.
- Flujo de saldo de facturas y reemisión/cancelación remota. Abrir/copiar y preparar correo
  están implementados; el envío SMTP depende de la infraestructura existente y no se probó.
- Pruebas completas de instalación/actualización, permisos y contabilidad en Odoo 19 Enterprise.

No retirar la restricción de activación hasta completar esos puntos.
Las comisiones se conciliarán contra el abono bancario y gasto con las herramientas estándar;
no reducir el importe pagado por el cliente ni marcar manualmente una factura como pagada.

## Actualización y rollback

Con autorización y primero en pruebas: respaldar base/filestore y versión del addon; ejecutar
`-u alvina_culqi_payment --stop-after-init`, repetir las pruebas y revisar vistas/reglas/datos.
En esta versión no hay cobros activos. Se crea una tabla de solicitudes y campos/índices propios;
no se migran ni alteran transacciones de otros proveedores. Para volver atrás, restaurar
juntos código y respaldo de base/filestore de pruebas. No borrar transacciones ni pagos para
simular una reversión. Una versión futura con cobros reales requerirá conciliación de eventos
pendientes y desactivación remota de enlaces antes de cualquier rollback; restaurar solo la
base puede perder pagos recibidos externamente. Ningún paso autoriza despliegue o reinicio productivo.

## Cola y operación prevista

Tras verificar los contratos pendientes, una versión posterior podrá habilitar el proveedor y
su cron. El vendedor genera una solicitud local; el cron valida nuevamente usuario, compañía,
importe y pedido y deja `requesting` confirmado antes de enviar. Los estados `blocked` indican
rechazo local antes de llamar; `uncertain` indica que no se puede asegurar el resultado remoto.
Ninguno debe reencolarse mediante SQL o borrado de registros. No se soporta una segunda solicitud
para ese pedido hasta implementar reconciliación/cancelación segura en una siguiente versión.
El estado `created` acredita únicamente el enlace, no un pago ni una factura pagada.

El botón de correo prepara un borrador para revisión en Odoo. No envía mensajes automáticamente.
El webhook permanece en 503 y no almacena cuerpos públicos hasta contar con autenticidad verificable.
