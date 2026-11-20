# Contrato de la API de FOCU

Describe las rutas del servidor, los permisos de cada una y el formato de los datos. Sirve para que la interfaz incluida en `web/`, o cualquier otro cliente, se conecte sin depender del código del servidor.

## Convenciones

- **Base:** todas las rutas empiezan con `/api` (por ejemplo, `https://servidor/api/login`).
- **Formato:** JSON, salvo la descarga de plantillas y la carga de archivos (`multipart/form-data`).
- **Documentación interactiva:** en `/docs`.
- **Errores:** `{"detail": "mensaje"}` con el código HTTP correspondiente.

| Código | Significado |
|---|---|
| 400 | Datos inválidos |
| 401 | Sin sesión o código incorrecto |
| 403 | Sin permiso |
| 404 | No existe |
| 409 | Conflicto (repetido o estado que no lo permite) |
| 413 | Archivo demasiado grande (máximo 5 MB) |
| 429 | Demasiados intentos |
| 503 | Servicio de IA no disponible |

## Acceso y sesión

1. La persona debe estar registrada en un grupo de investigación (o ser administradora de la VRI).
2. `POST /api/login/solicitar` envía un código de 6 dígitos al correo. El código vale 15 minutos, se usa una sola vez y admite 5 intentos.
3. `POST /api/login` con el código devuelve un token. Cada petición posterior envía `Authorization: Bearer <token>`. El token dura 8 horas.
4. El rol y el estado de la cuenta se consultan en la base en cada petición: desactivar a una persona le quita el acceso de inmediato.
5. Un mismo correo puede tener varios perfiles (por ejemplo, administración de la VRI y dirección de un grupo). `POST /api/yo/perfil` cambia de perfil y devuelve un token nuevo.

## Rutas públicas

| Ruta | Descripción |
|---|---|
| `GET /api/salud` | Devuelve `{"ok": true}`. Sirve para comprobar que el servidor está activo. |
| `GET /api/meta` | Opciones válidas y etiquetas (tipos de beneficiario, áreas, fases, alcances). |
| `GET /api/aviso` | Texto del aviso de tratamiento de datos personales. |
| `GET /api/contacto` | Datos de contacto del pie de página. |
| `POST /api/login/solicitar` | `{correo}`. Envía el código. La respuesta es la misma exista o no el correo. Máximo 3 por correo cada 15 minutos. |
| `POST /api/login` | `{correo, codigo}`. Devuelve `{token, rol, nombre, consentimiento}`. |
| `GET /api/grupos` | Grupos (id, nombre, descripción, área, facultad, director, categoría, línea), sin correos. |
| `POST /api/registro/solicitar` | `{nombre, correo, facultad, grupo_id, semillero?}`. La facultad debe ser la del grupo. Envía un código al correo. |
| `POST /api/registro/confirmar` | `{correo, codigo}`. La solicitud llega al director del grupo, o a la VRI si el grupo no tiene director con correo. |

## Cualquier usuario con sesión

| Ruta | Descripción |
|---|---|
| `GET /api/yo` | Datos de la persona: nombre, correo, rol, grupo, semillero, facultad, consentimiento, perfil y perfiles disponibles. |
| `PATCH /api/yo` | Actualiza nombre, facultad o semillero. |
| `POST /api/yo/perfil` | `{usuario_id}`. Cambia a otro perfil del mismo correo. |
| `POST /api/yo/correo` | `{correo}`. Solo administradores y directores de grupo: envía un código al correo nuevo. |
| `POST /api/yo/correo/confirmar` | `{codigo}`. Confirma el cambio de correo. |
| `GET /api/convocatorias` | Convocatorias activas. Los integrantes solo ven las que tienen visto bueno de la VRI. |

## Integrante de un grupo

| Ruta | Descripción |
|---|---|
| `POST /api/consentimiento` | Acepta el tratamiento de datos. Sin esto no puede postular. |
| `PUT /api/perfil` | Guarda el perfil completo (reemplaza el anterior). Todos los campos son opcionales. |
| `POST /api/convocatorias/buscar` | Ranking con porcentaje de compatibilidad según el perfil guardado. |
| `POST /api/postulaciones` | `{convocatoria_id, titulo?, nota?}`. Crea la postulación del grupo. |
| `GET /api/postulaciones` | Postulaciones del grupo, con su fase y los comentarios. |
| `POST /api/postulaciones/{id}/reenviar` | `{nota?}`. Corrige y reenvía una postulación devuelta por el director o rechazada por la VRI. Vuelve a pasar primero por el director. |
| `DELETE /api/postulaciones/{id}` | Cancela la postulación. Solo quien la inició o el director del grupo, y mientras la VRI no la haya aprobado. |
| `GET /api/postulaciones/{id}/requisitos` | Lista de requisitos de la postulación, con su estado. La ven los integrantes del grupo y la VRI. |
| `PUT /api/postulaciones/{id}/requisitos/{clave}` | `{estado, observacion?}`. Lo marcan quien inició la postulación y el director del grupo, mientras la VRI no la haya aprobado. |
| `POST /api/chat` | `{mensaje}`. Devuelve `{respuesta, perfil, resultados}`. |
| `GET /api/chat/conversaciones` | Historial de conversaciones con el asistente. Se eliminan a los 4 días. |
| `GET /api/chat/conversaciones/{id}` | Mensajes de una conversación. |
| `DELETE /api/chat/conversaciones/{id}` | Elimina una conversación. |
| `DELETE /api/chat` | Inicia una conversación nueva. |

### Postulaciones

- Hay una postulación por grupo y convocatoria. Cualquier integrante puede iniciarla y todo el grupo la ve.
- La postulación recorre estas fases: `en_director`, `devuelta`, `en_vri`, `aprobada` y `rechazada`.
- Si quien postula es el director, o el grupo no tiene un director activo, la postulación pasa directo a la VRI.
- Exige haber aceptado el tratamiento de datos y que la convocatoria esté vigente y con visto bueno.

### Lista de requisitos

- Si la convocatoria es una convocatoria interna de investigación de la VRI, la lista corresponde al formato FOR-40. En las demás, se arma con los requisitos que trae la propia convocatoria.
- Estados posibles de cada requisito: `pendiente`, `si`, `no` y `no_aplica`.
- Los avales de Departamento y de Facultad de la lista interna solo los marca el director. El aval de Facultad no puede ir en «Sí» si el de Departamento no lo está.
- La VRI no aprueba una postulación mientras falten avales en «Sí».

### Perfil

Valores válidos (los mismos de `schema.py`):

```json
{
  "tipo_beneficiario": "grupo_semillero | grupo_investigacion | estudiante_pregrado | estudiante_posgrado | docente | investigador | emprendedor",
  "area_conocimiento": "ciencias_naturales | ingenieria_tecnologia | salud | agro | ciencias_sociales | humanidades_artes | multidisciplinar",
  "alcance": "regional | nacional | internacional",
  "fase_proyecto": "idea | prototipo | ejecucion | consolidacion",
  "monto_requerido_cop": 20000000,
  "descripcion_proyecto": "texto libre, máximo 2000 caracteres"
}
```

Cada elemento del ranking trae: `id, nombre, entidad, fecha_limite, objetivo, requisitos, beneficios, url, fuente, verificada, elegible, porcentaje, detalle, motivos_exclusion`.

## Director de grupo

Usuarios con `es_director_grupo` (lo indica `GET /api/yo`). Operan solo sobre su grupo.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/grupo/solicitudes?estado=pendiente\|aprobada\|rechazada` | Solicitudes de registro del grupo. |
| POST | `/api/grupo/solicitudes/{id}/decidir` | `{aprobar, comentario?}`. Al aprobar, la persona queda en el grupo y puede ingresar. |
| GET | `/api/grupo/postulaciones?etapa=pendiente\|devuelta\|aprobada` | Postulaciones de los integrantes del grupo. |
| POST | `/api/grupo/postulaciones/{id}/decidir` | `{aprobar, comentario?}`. Al aprobar pasa a la VRI; al devolver, el comentario es obligatorio. |
| GET | `/api/grupo/miembros` | Integrantes del grupo. |
| POST | `/api/grupo/miembros` | `{nombre, correo?, semillero?}`. Agrega a una persona. |
| PATCH | `/api/grupo/miembros/{id}` | `{activo}`. Activa o desactiva el acceso. |
| DELETE | `/api/grupo/miembros/{id}` | Elimina a una persona. Si ya inició postulaciones, se conserva y se debe desactivar. |

## Administración (VRI)

### Grupos y solicitudes de registro

| Método | Ruta | Descripción |
|---|---|---|
| GET, POST | `/api/admin/grupos` | Lista los grupos / crea o completa uno. |
| PATCH | `/api/admin/grupos/{id}` | Edita descripción, área, facultad o director. |
| DELETE | `/api/admin/grupos/{id}` | Elimina un grupo. |
| GET | `/api/admin/solicitudes` | Todas las solicitudes de registro. |
| POST | `/api/admin/solicitudes/{id}/decidir` | Igual que la del director (útil para grupos sin director). |

### Personas

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/admin/estudiantes` | Personas registradas, con su grupo, cargo, acceso y postulaciones. |
| POST | `/api/admin/estudiantes` | `{nombre, grupo, correo?, semillero?, facultad?, descripcion?}`. El grupo debe existir. |
| POST | `/api/admin/directores` | `{nombre, correo, grupo_id, facultad, semillero?}`. Agrega un director sin reemplazar al que el grupo ya tenga. |
| PATCH | `/api/admin/estudiantes/{id}` | Cambia solo lo que se envíe: `activo`, `nombre`, `correo`, `facultad`, cargo, o `semillero` y `grupo` juntos. El grupo debe existir. |
| DELETE | `/api/admin/estudiantes/{id}` | Elimina a una persona. Si ya inició postulaciones, se conserva y se debe desactivar. |
| POST | `/api/admin/administradores` | `{nombre, correo}`. Agrega a otra persona de la VRI. |
| GET | `/api/admin/semilleros` | Grupos con sus semilleros y su número de integrantes. |
| GET | `/api/admin/plantilla` | Descarga `plantilla_personas.xlsx`, generada en el momento con listas desplegables de grupos, facultades y cargo. |
| POST | `/api/admin/cargar-excel` | Carga personas desde un archivo `.xlsx` (campo `archivo`, máximo 5 MB). |

Carga de personas:

- Columnas obligatorias: `nombre`, `correo` y `grupo`. Opcionales: `semillero`, `facultad`, `descripcion` y `cargo` (`Estudiante` o `Director/a`; vacío equivale a estudiante).
- El grupo debe existir. Se compara sin distinguir mayúsculas ni tildes. Una fila con un grupo que no existe se omite y no se crea el grupo.
- Un director se agrega como adicional: no reemplaza al que el grupo ya tenga.
- Respuesta: `{creados, actualizados, omitidos: [{fila, motivo}]}`.

### Postulaciones

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/admin/postulaciones?estado=` | Postulaciones que el director ya aprobó. El filtro es opcional. |
| PATCH | `/api/admin/postulaciones/{id}` | `{estado: "aprobada" \| "rechazada", comentario?}`. Responde 409 si el director aún no la aprobó o si faltan avales. |
| DELETE | `/api/admin/postulaciones/{id}` | Elimina cualquier postulación, también las aprobadas. |

### Convocatorias, contacto y recolección

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/admin/convocatorias` | Todas las convocatorias, también las ocultas. |
| POST | `/api/admin/convocatorias` | Agrega una convocatoria a mano (queda con visto bueno). |
| POST | `/api/admin/convocatorias/desde-url` | `{url}`. La IA lee la página y crea la convocatoria sin visto bueno. |
| PATCH | `/api/admin/convocatorias/{id}` | `{activa?, verificada?}`. |
| DELETE | `/api/admin/convocatorias/{id}` | Elimina una convocatoria. |
| PUT | `/api/admin/contacto` | Actualiza los datos de contacto del pie de página. |
| POST | `/api/admin/scrapers/ejecutar` | Lanza la recolección desde las fuentes oficiales (responde 202; corre en segundo plano). |
| GET | `/api/admin/scrapers/estado` | Si está en ejecución, desde cuándo y el informe de la última recolección. |

## Ejemplo

```js
const r = await fetch("/api/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ correo: "ana@unicauca.edu.co", codigo: "482915" }),
});
const { token } = await r.json();

const res = await fetch("/api/convocatorias/buscar", {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` },
});
const ranking = await res.json();
```