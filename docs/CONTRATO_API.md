# Contrato de la API de FOCU

Es el acuerdo entre el backend y cualquier pantalla que se conecte (la página incluida en `web/`, o una versión futura hecha con React u otra herramienta). Mientras no cambie, cada parte puede trabajar sin esperar a la otra.

- **Base:** todas las rutas empiezan con `/api` (por ejemplo `https://tu-sitio/api/login`).
- **Formato:** JSON, salvo la descarga de la plantilla y la carga del Excel.
- **Prueba interactiva:** en `/docs` se puede probar cada ruta con un botón.
- **Errores:** llegan como `{"detail": "mensaje"}` con su código: 400 datos inválidos, 401 sin sesión o código incorrecto, 403 sin permiso, 404 no existe, 409 repetido, 413 archivo muy grande, 429 demasiados intentos, 503 servicio de IA no disponible.

## Cómo funciona el acceso

1. La VRI registra a cada integrante (a mano o con el Excel) con nombre, correo, semillero y grupo. Solo quien esté registrado en un grupo puede ingresar.
   Para entrar, la persona pide un **código temporal de 6 dígitos** a su correo (`POST /api/login/solicitar`); vale 15 minutos, sirve una vez y permite 5 intentos.
2. El estudiante lo escribe en el ingreso → `POST /api/login` → recibe un **token**.
3. Cada petición posterior lleva la cabecera `Authorization: Bearer <token>`. El token dura 8 horas.
4. El rol (`estudiante` o `admin`) y si la cuenta está activa se consultan en la base en cada petición: si la VRI desactiva a alguien, pierde el acceso de inmediato.

## Públicas

| Ruta | Qué hace |
|---|---|
| `GET /api/salud` | Responde `{"ok": true}`. Sirve para comprobar que el servidor está encendido. |
| `GET /api/meta` | Opciones válidas y sus etiquetas (tipos de beneficiario, áreas, fases, alcances). |
| `GET /api/aviso` | Texto del aviso de datos personales. |
| `POST /api/login/solicitar` | Body `{"correo"}`. Envía el código por correo. Responde siempre lo mismo, esté o no registrado el correo. Máx. 3 por correo cada 15 min. |
| `POST /api/login` | Body `{"correo", "codigo"}`. Devuelve `{token, rol, nombre, consentimiento}`. |

## Cualquier usuario con sesión

| Ruta | Qué hace |
|---|---|
| `GET /api/yo` | Datos del usuario: nombre, rol, semillero, grupo, consentimiento y perfil. |
| `GET /api/convocatorias` | Catálogo de convocatorias activas. Los estudiantes solo ven las que tienen visto bueno de la VRI (`verificada`). |

## Estudiante

| Ruta | Qué hace |
|---|---|
| `POST /api/consentimiento` | Acepta el tratamiento de datos. Sin esto no puede postularse. |
| `PUT /api/perfil` | Guarda el perfil completo (reemplaza el anterior). Todos los campos son opcionales. |
| `POST /api/convocatorias/buscar` | Ranking con % de compatibilidad según el perfil guardado (sin body). La primera vez puede tardar unos segundos. |
| `POST /api/postulaciones` | Body `{"convocatoria_id": "...", "nota": "..."}`. Queda `solicitada`. 409 si ya la pidió. |
| `GET /api/postulaciones` | Sus postulaciones y la respuesta de la VRI. |
| `POST /api/chat` | Body `{"mensaje": "..."}`. Devuelve `{respuesta, perfil, resultados}`. |
| `DELETE /api/chat` | Reinicia la conversación con el asistente. |

Valores válidos del perfil (los mismos de `schema.py`):

```json
{
  "tipo_beneficiario": "grupo_semillero | grupo_investigacion | estudiante_pregrado | estudiante_posgrado | docente | investigador | emprendedor",
  "area_conocimiento": "ciencias_naturales | ingenieria_tecnologia | salud | agro | ciencias_sociales | humanidades_artes | multidisciplinar",
  "alcance": "regional | nacional | internacional",
  "fase_proyecto": "idea | prototipo | ejecucion | consolidacion",
  "monto_requerido_cop": 20000000,
  "descripcion_proyecto": "texto libre, máx. 2000 caracteres"
}
```

Cada elemento del ranking trae: `id, nombre, entidad, fecha_limite, objetivo, requisitos, beneficios, url, fuente, verificada, elegible, porcentaje, detalle, motivos_exclusion`.

## Administrador (VRI)

| Ruta | Qué hace |
|---|---|
| `GET /api/admin/plantilla` | Descarga `plantilla_semilleros.xlsx`. |
| `POST /api/admin/cargar-excel` | `multipart/form-data`, campo `archivo` (.xlsx, máx. 5 MB). Columnas obligatorias: `nombre` y `grupo`; opcionales: `correo`, `semillero`, `facultad`, `descripcion`. Quien no trae correo queda registrada pero no puede ingresar; al volver a subir el Excel con el correo se le completa sin duplicarla. Devuelve `{creados: [...], actualizados: [...], omitidos: [{fila, motivo}]}`. |
| `GET /api/admin/semilleros` | Grupos con sus semilleros y cuántos estudiantes tiene cada uno. |
| `GET /api/admin/estudiantes` | Lista de estudiantes con semillero, grupo, estado de acceso y postulaciones. |
| `POST /api/admin/estudiantes` | Agrega a UNA persona: `{"nombre", "grupo"}` y, opcionales, `correo`, `semillero`, `facultad`, `descripcion`. Sin correo no puede ingresar hasta que se edite. 409 si ya existe. |
| `PATCH /api/admin/estudiantes/{id}` | Cambia solo lo que se envíe: `activo`, `nombre`, `correo`, o `semillero` y `grupo` juntos. |
| `POST /api/admin/administradores` | Agrega a otra persona de la VRI: `{"nombre", "correo"}` (ambos obligatorios). |
| `GET /api/admin/postulaciones?estado=solicitada` | Solicitudes con estudiante, semillero, grupo y convocatoria. El filtro `estado` es opcional. |
| `PATCH /api/admin/postulaciones/{id}` | `{"estado": "aprobada" \| "rechazada", "comentario": "..."}`. |
| `GET /api/admin/convocatorias` | Todas las convocatorias, también las ocultas. |
| `POST /api/admin/convocatorias` | Agrega una convocatoria a mano (queda verificada). |
| `PATCH /api/admin/convocatorias/{id}` | `{"activa": bool}` y/o `{"verificada": bool}`. |
| `POST /api/admin/scrapers/ejecutar` | Lanza la recolección desde las fuentes oficiales (responde 202; corre en segundo plano). |
| `GET /api/admin/scrapers/estado` | Si está corriendo, desde cuándo, el informe de la última recolección y las fuentes configuradas. |

## Ejemplo (JavaScript)

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
