"use strict";
/* FOCU: página web. Habla con la API del servidor (/api).
   Todo el texto que viene del servidor se inserta como texto (nunca como HTML),
   para que nadie pueda inyectar código a través de un nombre o una convocatoria. */

const API = "/api";
const $ = (s) => document.querySelector(s);

const estado = {
  token: null, yo: null, meta: null, pestana: null,
  ranking: null, chat: null, scr: null, temporizador: null, filtroPost: "solicitada",
};
try { estado.token = localStorage.getItem("focu_token"); } catch (e) { /* modo privado */ }

const PESTANAS_ESTUDIANTE = [["perfil", "Mi perfil"], ["convocatorias", "Convocatorias"],
  ["postulaciones", "Mis postulaciones"], ["asistente", "Asistente IA"]];
const PESTANAS_ADMIN = [["a_post", "Postulaciones"], ["a_est", "Estudiantes"], ["a_excel", "Cargar Excel"],
  ["a_conv", "Convocatorias"], ["a_admin", "Directores"]];

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------
/** Crea un elemento HTML. Los hijos de tipo texto se insertan como texto, nunca como HTML. */
function h(tag, attrs, ...hijos) {
  const el = document.createElement(tag);
  let valor;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "value") valor = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const hijo of hijos.flat(Infinity)) {
    if (hijo === null || hijo === undefined || hijo === false) continue;
    el.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  if (valor !== undefined) el.value = valor;   // después de crear las opciones de un select
  return el;
}

function aviso(texto, tipo = "ok") {
  const t = h("div", { class: "toast" + (tipo === "error" ? " error" : "") }, texto);
  $("#avisos").append(t);
  setTimeout(() => t.remove(), tipo === "error" ? 8000 : 4500);
}

function mensajeError(datos, status) {
  if (datos && typeof datos.detail === "string") return datos.detail;
  if (datos && Array.isArray(datos.detail)) {
    return datos.detail.map((d) => (d.loc ? d.loc.slice(1).join(".") + ": " : "") + d.msg).join(" · ");
  }
  return "Error " + status;
}

async function api(ruta, { metodo = "GET", cuerpo, formData } = {}) {
  const cab = {};
  if (estado.token) cab["Authorization"] = "Bearer " + estado.token;
  let body;
  if (formData) body = formData;
  else if (cuerpo !== undefined) { cab["Content-Type"] = "application/json"; body = JSON.stringify(cuerpo); }
  let r;
  try { r = await fetch(API + ruta, { method: metodo, headers: cab, body }); }
  catch (e) { throw new Error("No hay conexión con el servidor."); }
  if (r.status === 401 && estado.token && ruta !== "/login") {
    cerrarSesion(false);
    throw new Error("Tu sesión terminó. Ingresa de nuevo.");
  }
  const tipo = r.headers.get("content-type") || "";
  const datos = tipo.includes("application/json") ? await r.json() : null;
  if (!r.ok) throw new Error(mensajeError(datos, r.status));
  return datos;
}

async function conBoton(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { aviso(e.message, "error"); } finally { btn.disabled = false; }
}

const fechaTxt = (f) => (f ? String(f).slice(0, 10) : "sin fecha");
const fechaHora = (f) => (f ? String(f).replace("T", " ").slice(0, 16) : "—");
const COP = (n) => (n || n === 0 ? "$" + new Intl.NumberFormat("es-CO").format(n) : "—");
const urlSegura = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

function insigniaEstado(e) {
  const mapa = { solicitada: ["alerta", "Solicitada"], aprobada: ["ok", "Aprobada"], rechazada: ["mal", "Rechazada"] };
  const [clase, texto] = mapa[e] || ["", e];
  return h("span", { class: "insignia " + clase }, texto);
}

function tabla(columnas, filas) {
  return h("div", { class: "tabla-scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, columnas.map((c) => h("th", {}, c)))),
    h("tbody", {}, filas.map((f) => h("tr", {},
      f.map((c) => (c instanceof HTMLTableCellElement ? c : h("td", {}, c))))))));
}
const tdAcciones = (...botones) => h("td", { class: "acciones" }, botones);

function modal(titulo, contenido, botones) {
  const fondo = h("div", { class: "modal-fondo" });
  const cerrar = () => fondo.remove();
  const barra = h("div", { class: "modal-botones" }, botones.map((b) => h("button", {
    class: "btn " + (b.clase || ""),
    onclick: async (ev) => {
      const boton = ev.currentTarget;
      boton.disabled = true;
      try {
        const r = b.accion ? await b.accion() : undefined;
        if (r !== false) cerrar();
      } catch (e) { aviso(e.message, "error"); } finally { boton.disabled = false; }
    },
  }, b.texto)));
  fondo.append(h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, h("h3", {}, titulo), contenido, barra));
  fondo.addEventListener("click", (ev) => { if (ev.target === fondo) cerrar(); });
  document.body.append(fondo);
  return { cerrar };
}

function cajaCodigo(titulo, codigo) {
  return h("div", { class: "codigo-caja" },
    h("div", {}, titulo),
    h("code", {}, codigo),
    h("p", { class: "sub" }, "Anótalo o cópialo ahora: por seguridad no se vuelve a mostrar."),
    h("button", { class: "btn chico", onclick: async () => {
      try { await navigator.clipboard.writeText(codigo); aviso("Código copiado."); }
      catch (e) { aviso("No se pudo copiar. Selecciónalo y cópialo a mano.", "error"); }
    } }, "Copiar código"));
}

function descargarBlob(blob, nombre) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: nombre });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function descargarCSV(nombre, columnas, filas) {
  const esc = (v) => {
    let t = String(v ?? "");
    if (/^[=+\-@]/.test(t)) t = "'" + t;   // evita que Excel interprete el texto como fórmula
    return '"' + t.replace(/"/g, '""') + '"';
  };
  const lineas = [columnas.map((c) => esc(c.titulo)).join(",")]
    .concat(filas.map((f) => columnas.map((c) => esc(f[c.campo])).join(",")));
  descargarBlob(new Blob(["\ufeff" + lineas.join("\r\n")], { type: "text/csv;charset=utf-8" }), nombre);
}

function mostrar(pestana, ...nodos) {
  if (estado.pestana !== pestana) return;   // el usuario ya cambió de pestaña
  // acepta listas y null (como h()): sin esto, una lista de tarjetas se imprimía como texto
  $("#app").replaceChildren(...nodos.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));
}

function detenerSondeo() {
  if (estado.temporizador) { clearTimeout(estado.temporizador); estado.temporizador = null; }
}

// ---------------------------------------------------------------------------
// Sesión y navegación
// ---------------------------------------------------------------------------
function guardarToken(t) {
  estado.token = t;
  try { localStorage.setItem("focu_token", t); } catch (e) { /* modo privado */ }
}

function cerrarSesion(avisar = true) {
  estado.token = null; estado.yo = null; estado.ranking = null; estado.chat = null; estado.pestana = null;
  try { localStorage.removeItem("focu_token"); } catch (e) { /* modo privado */ }
  detenerSondeo();
  render();
  if (avisar) aviso("Sesión cerrada.");
}

function pintarUsuario() {
  const cont = $("#usuario");
  if (!estado.yo) { cont.replaceChildren(); return; }
  const detalle = estado.yo.rol === "admin" ? "Administrador VRI"
    : [estado.yo.semillero, estado.yo.grupo].filter(Boolean).join(" · ");
  cont.replaceChildren(
    h("div", {}, h("b", {}, estado.yo.nombre)),
    detalle ? h("div", { class: "sub", style: "color:#E9EDFB" }, detalle) : null,
    h("button", { class: "btn-salir", onclick: () => cerrarSesion() }, "Cerrar sesión"));
}

function pintarMenu(pestanas) {
  const menu = $("#menu");
  menu.hidden = false;
  menu.replaceChildren(...pestanas.map(([id, texto]) => h("button", {
    class: id === estado.pestana ? "activa" : "",
    onclick: () => { estado.pestana = id; detenerSondeo(); render(); },
  }, texto)));
}

async function render() {
  const app = $("#app");
  if (!estado.token) {
    estado.yo = null;
    pintarUsuario();
    $("#menu").hidden = true;
    return vistaLogin();
  }
  try {
    if (!estado.meta) estado.meta = await api("/meta");
    if (!estado.yo) {
      app.replaceChildren(h("p", { class: "cargando" }, "Cargando…"));
      estado.yo = await api("/yo");
    }
  } catch (e) {
    if (!estado.token) return;   // la sesión ya se cerró y se mostró el ingreso
    app.replaceChildren(h("div", { class: "aviso-caja error" }, e.message + " ",
      h("button", { class: "btn chico", onclick: render }, "Reintentar")));
    return;
  }
  pintarUsuario();
  const esAdmin = estado.yo.rol === "admin";
  if (!esAdmin && !estado.yo.consentimiento) {
    $("#menu").hidden = true;
    return vistaConsentimiento();
  }
  const pestanas = esAdmin ? PESTANAS_ADMIN : PESTANAS_ESTUDIANTE;
  if (!pestanas.some(([id]) => id === estado.pestana)) estado.pestana = pestanas[0][0];
  pintarMenu(pestanas);
  const vistas = {
    perfil: vistaPerfil, convocatorias: vistaConvocatorias, postulaciones: vistaMisPostulaciones,
    asistente: vistaAsistente, a_post: vistaAdminPostulaciones, a_est: vistaAdminEstudiantes,
    a_excel: vistaAdminExcel, a_conv: vistaAdminConvocatorias, a_admin: vistaAdminAdmins,
  };
  try { await vistas[estado.pestana](); }
  catch (e) { if (estado.token) mostrar(estado.pestana, h("div", { class: "aviso-caja error" }, e.message)); }
}

// ---------------------------------------------------------------------------
// Ingreso y consentimiento
// ---------------------------------------------------------------------------
function vistaLogin() {
  const correo = h("input", { type: "email", placeholder: "tu.correo@unicauca.edu.co", autocomplete: "email",
    maxlength: "120", required: true, "aria-label": "Correo" });
  const codigo = h("input", { type: "text", placeholder: "000000", autocomplete: "one-time-code",
    inputmode: "numeric", maxlength: "10", "aria-label": "Código recibido por correo" });
  const bPedir = h("button", { class: "btn primario", type: "submit" }, "Enviarme un código");
  const bEntrar = h("button", { class: "btn primario", type: "submit" }, "Ingresar");
  const msg = h("p", { class: "sub" });
  const paso2 = h("div", { hidden: true },
    h("label", { class: "campo" }, h("span", {}, "Código de 6 dígitos"), codigo),
    h("div", { class: "fila" }, bEntrar,
      h("button", { class: "btn", type: "button", onclick: () => pedir() }, "Reenviar código")));
  async function pedir() {
    if (!correo.value.trim()) return;
    bPedir.disabled = true;
    msg.textContent = "Conectando con el servidor… la primera vez puede tardar hasta un minuto.";
    try {
      const r = await api("/login/solicitar", { metodo: "POST", cuerpo: { correo: correo.value.trim() } });
      msg.textContent = r.mensaje;
      paso2.hidden = false;
      codigo.focus();
    } catch (e) { msg.textContent = ""; aviso(e.message, "error"); }
    bPedir.disabled = false;
  }
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    if (paso2.hidden) { await pedir(); return; }
    if (!codigo.value.trim()) return;
    bEntrar.disabled = true;
    try {
      const r = await api("/login", { metodo: "POST",
        cuerpo: { correo: correo.value.trim(), codigo: codigo.value.trim() } });
      guardarToken(r.token);
      estado.yo = null; estado.pestana = null;
      await render();
    } catch (e) {
      aviso(e.message, "error");
      bEntrar.disabled = false;
      codigo.select();
    }
  } },
  h("label", { class: "campo" }, h("span", {}, "Correo registrado"), correo),
  h("div", { class: "fila" }, bPedir), paso2, msg);
  $("#app").replaceChildren(h("div", { class: "ingreso" }, h("div", { class: "tarjeta" },
    h("h2", {}, "Ingresar"),
    h("p", {}, "Escribe el correo con el que tu grupo de investigación te registró. Te enviaremos un código temporal."),
    form,
    h("p", { class: "sub" }, "¿Tu correo no funciona? Pide a tu grupo o a la Vicerrectoría de Investigaciones que te registre."))));
  correo.focus();
}

async function vistaConsentimiento() {
  const app = $("#app");
  app.replaceChildren(h("p", { class: "cargando" }, "Cargando…"));
  const { texto } = await api("/aviso");
  app.replaceChildren(h("div", { class: "tarjeta" },
    h("h2", {}, "Antes de empezar"),
    h("p", {}, "Para usar FOCU necesitamos tu autorización para tratar tus datos personales."),
    h("div", { class: "aviso-texto" }, texto),
    h("div", { class: "fila" },
      h("button", { class: "btn primario", onclick: (ev) => conBoton(ev.currentTarget, async () => {
        await api("/consentimiento", { metodo: "POST" });
        estado.yo.consentimiento = true;
        await render();
      }) }, "Acepto"),
      h("button", { class: "btn", onclick: () => cerrarSesion() }, "No acepto (salir)"))));
}

// ---------------------------------------------------------------------------
// Estudiante: perfil
// ---------------------------------------------------------------------------
async function vistaPerfil() {
  const meta = estado.meta;
  const p = estado.yo.perfil || {};
  const selector = (nombre, etiqueta, placeholder) => h("label", { class: "campo" },
    h("span", {}, etiqueta),
    h("select", { name: nombre, value: p[nombre] || "" },
      h("option", { value: "" }, placeholder),
      meta.categorias[nombre].map((o) => h("option", { value: o }, meta.etiquetas[nombre][o] || o))));

  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const perfil = {};
    for (const [k, v] of new FormData(ev.target).entries()) {
      const t = String(v).trim();
      if (!t) continue;
      perfil[k] = k === "monto_requerido_cop" ? parseInt(t, 10) : t;
    }
    const boton = ev.target.querySelector("button[type=submit]");
    await conBoton(boton, async () => {
      const r = await api("/perfil", { metodo: "PUT", cuerpo: perfil });
      estado.yo.perfil = r.perfil;
      estado.ranking = null;
      aviso("Perfil guardado.");
      estado.pestana = "convocatorias";
      await render();
    });
  } },
  h("div", { class: "rejilla" },
    selector("tipo_beneficiario", "Tipo de beneficiario", "TIPO DE BENEFICIARIO:"),
    selector("fase_proyecto", "Fase del proyecto", "FASE:"),
    selector("area_conocimiento", "Área de conocimiento", "ÁREA DE CONOCIMIENTO:"),
    h("label", { class: "campo" }, h("span", {}, "Monto requerido (COP)"),
      h("input", { type: "number", name: "monto_requerido_cop", min: "0", step: "1000000",
        placeholder: "Ej.: 20000000", value: p.monto_requerido_cop || "" })),
    selector("alcance", "Alcance del proyecto", "ALCANCE:"),
    h("label", { class: "campo" }, h("span", {}, "Describe brevemente tu proyecto o idea"),
      h("textarea", { name: "descripcion_proyecto", maxlength: "2000",
        placeholder: "Qué quieres hacer, para quién y con qué resultado.", value: p.descripcion_proyecto || "" }))),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Guardar y ver convocatorias")));

  mostrar("perfil", h("h2", {}, "Mi perfil"),
    h("p", { class: "sub" }, "Cuéntanos qué necesitas financiar. Mientras más completo, mejor el resultado. Puedes cambiarlo cuando quieras."),
    form);
}

// ---------------------------------------------------------------------------
// Estudiante: convocatorias
// ---------------------------------------------------------------------------
function tarjetaConvocatoria(r, yaPostulado) {
  const etiquetas = { area_conocimiento: "Área", fase_proyecto: "Fase", monto: "Monto",
    alcance: "Alcance", afinidad_tematica: "Afinidad temática" };
  const detalle = Object.entries(r.detalle || {}).map(([k, v]) =>
    h("li", {}, h("b", {}, (etiquetas[k] || k) + ": "), v));
  const enlace = urlSegura(r.url);
  const botonPostular = yaPostulado
    ? h("span", { class: "insignia ok" }, "Solicitud enviada a la VRI")
    : h("button", { class: "btn primario", onclick: () => modalPostular(r) }, "Quiero postularme");
  return h("div", { class: "tarjeta" },
    h("div", { class: "cabeza" },
      h("div", {},
        h("h3", {}, r.nombre),
        h("div", { class: "sub" }, `${r.entidad || "Entidad no indicada"} · cierra ${fechaTxt(r.fecha_limite)}`),
        r.verificada ? null : h("span", { class: "insignia alerta" }, "Datos extraídos automáticamente: verifícalos en la fuente")),
      h("div", { class: "porcentaje" }, h("b", {}, r.porcentaje + "%"), h("span", { class: "sub" }, "compatibilidad"))),
    h("div", { class: "barra" }, h("i", { style: `width:${Math.max(0, Math.min(100, r.porcentaje))}%` })),
    r.objetivo ? h("details", {}, h("summary", {}, "De qué trata"), h("p", {}, r.objetivo)) : null,
    h("details", {}, h("summary", {}, "Ventajas y beneficios"), h("p", {}, r.beneficios || "No especificadas.")),
    h("details", {}, h("summary", {}, "Requisitos"), h("p", {}, r.requisitos || "No especificados en la fuente.")),
    detalle.length ? h("details", {}, h("summary", {}, "Por qué este porcentaje"), h("ul", { class: "simple" }, detalle)) : null,
    h("div", { class: "fila" },
      botonPostular,
      enlace ? h("a", { href: enlace, target: "_blank", rel: "noopener noreferrer" }, "Ir a la convocatoria ↗") : null));
}

function modalPostular(r) {
  const nota = h("textarea", { maxlength: "1000", placeholder: "Opcional: cuéntale a la VRI por qué quieres postularte." });
  modal("Solicitar postulación", h("div", {},
    h("p", {}, "Vas a avisar a la VRI que quieres postularte a:"),
    h("p", {}, h("b", {}, r.nombre)),
    h("p", { class: "sub" }, "La VRI revisará tu solicitud y te responderá aquí mismo, en “Mis postulaciones”."),
    nota), [
    { texto: "Cancelar" },
    { texto: "Enviar solicitud", clase: "primario", accion: async () => {
      await api("/postulaciones", { metodo: "POST", cuerpo: { convocatoria_id: r.id, nota: nota.value.trim() || null } });
      aviso("Solicitud enviada a la VRI.");
      render();
    } },
  ]);
}

async function vistaConvocatorias() {
  mostrar("convocatorias", h("h2", {}, "Convocatorias"),
    h("p", { class: "cargando" }, "Calculando la compatibilidad… la primera vez puede tardar unos segundos."));
  if (!Object.keys(estado.yo.perfil || {}).length) {
    return mostrar("convocatorias", h("h2", {}, "Convocatorias"),
      h("div", { class: "aviso-caja" }, "Primero completa tu perfil para ver qué convocatorias te aplican. ",
        h("button", { class: "btn chico", onclick: () => { estado.pestana = "perfil"; render(); } }, "Ir a Mi perfil")));
  }
  if (!estado.ranking) estado.ranking = await api("/convocatorias/buscar", { metodo: "POST" });
  const posts = await api("/postulaciones");
  const yaPostulado = new Set(posts.map((p) => String(p.convocatoria_id)));
  const elegibles = estado.ranking.filter((r) => r.elegible);
  const no = estado.ranking.filter((r) => !r.elegible);
  mostrar("convocatorias",
    h("h2", {}, "Convocatorias"),
    h("p", { class: "sub" }, `${elegibles.length} de ${estado.ranking.length} convocatorias te aplican.`),
    elegibles.length ? elegibles.map((r) => tarjetaConvocatoria(r, yaPostulado.has(r.id)))
      : h("div", { class: "vacio" }, "Por ahora ninguna convocatoria encaja con tu perfil. Prueba ajustando tu perfil o vuelve más tarde."),
    no.length ? h("details", {}, h("summary", {}, `Convocatorias que no te aplican (${no.length})`),
      h("ul", { class: "simple" }, no.map((r) => h("li", {}, h("b", {}, r.nombre + ": "), r.motivos_exclusion.join(" "))))) : null,
    h("div", { class: "fila" }, h("button", { class: "btn", onclick: () => { estado.ranking = null; render(); } }, "Actualizar resultados")));
}

async function vistaMisPostulaciones() {
  const filas = await api("/postulaciones");
  mostrar("postulaciones", h("h2", {}, "Mis postulaciones"),
    filas.length
      ? tabla(["Convocatoria", "Cierra", "Solicitada", "Estado", "Respuesta de la VRI"],
          filas.map((f) => [f.convocatoria, fechaTxt(f.fecha_limite), fechaHora(f.creada),
            insigniaEstado(f.estado), f.comentario_vri || "—"]))
      : h("div", { class: "vacio" }, "Aún no has solicitado ninguna postulación. Hazlo desde la pestaña Convocatorias."));
}

// ---------------------------------------------------------------------------
// Estudiante: asistente
// ---------------------------------------------------------------------------
function vistaAsistente() {
  if (!estado.chat) {
    estado.chat = [{ rol: "asistente", texto: "¡Hola! Soy FOCU. Cuéntame de tu proyecto (a qué semillero perteneces, " +
      "qué quieres financiar, en qué fase está y cuánto necesitas) y te ayudo a encontrar convocatorias." }];
  }
  const lista = h("div", { class: "mensajes" });
  const pintar = () => {
    lista.replaceChildren(...estado.chat.map((m) => h("div",
      { class: "burbuja " + m.rol + (m.pensando ? " pensando" : "") }, m.texto)));
    lista.scrollTop = lista.scrollHeight;
  };
  const entrada = h("input", { type: "text", placeholder: "Escribe tu mensaje…", maxlength: "2000",
    autocomplete: "off", "aria-label": "Mensaje" });
  const enviar = h("button", { class: "btn primario", type: "submit" }, "Enviar");
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const texto = entrada.value.trim();
    if (!texto) return;
    entrada.value = "";
    estado.chat.push({ rol: "usuario", texto });
    const espera = { rol: "asistente", texto: "Pensando…", pensando: true };
    estado.chat.push(espera);
    pintar();
    enviar.disabled = true;
    try {
      const r = await api("/chat", { metodo: "POST", cuerpo: { mensaje: texto } });
      espera.texto = r.respuesta || "(sin respuesta)";
      estado.yo.perfil = r.perfil || estado.yo.perfil;
      estado.ranking = null;
    } catch (e) { espera.texto = "⚠️ " + e.message; }
    espera.pensando = false;
    enviar.disabled = false;
    pintar();
  } }, entrada, enviar);

  mostrar("asistente", h("h2", {}, "Asistente FOCU"),
    h("p", { class: "sub" }, "Pregúntame lo que quieras sobre tus opciones de financiación. No escribas datos sensibles."),
    h("div", { class: "chat" }, lista, form),
    h("div", { class: "fila" }, h("button", { class: "btn chico", onclick: (ev) => conBoton(ev.currentTarget, async () => {
      await api("/chat", { metodo: "DELETE" });
      estado.chat = null;
      render();
    }) }, "Nueva conversación")));
  pintar();
}

// ---------------------------------------------------------------------------
// Administrador: postulaciones
// ---------------------------------------------------------------------------
function modalDecision(f, decision) {
  const comentario = h("textarea", { maxlength: "1000", placeholder: "Opcional: comentario para el estudiante." });
  modal(decision === "aprobada" ? "Aprobar postulación" : "Rechazar postulación", h("div", {},
    h("p", {}, h("b", {}, f.estudiante), " · ", f.semillero || "sin semillero"),
    h("p", {}, f.convocatoria),
    comentario), [
    { texto: "Cancelar" },
    { texto: decision === "aprobada" ? "Aprobar" : "Rechazar", clase: decision === "aprobada" ? "primario" : "peligro",
      accion: async () => {
        await api(`/admin/postulaciones/${f.id}`, { metodo: "PATCH",
          cuerpo: { estado: decision, comentario: comentario.value.trim() || null } });
        aviso("Decisión registrada.");
        render();
      } },
  ]);
}

async function vistaAdminPostulaciones() {
  const q = estado.filtroPost ? `?estado=${estado.filtroPost}` : "";
  const filas = await api("/admin/postulaciones" + q);
  const filtro = h("select", { value: estado.filtroPost, onchange: (ev) => { estado.filtroPost = ev.target.value; render(); } },
    h("option", { value: "solicitada" }, "Solicitadas"), h("option", { value: "aprobada" }, "Aprobadas"),
    h("option", { value: "rechazada" }, "Rechazadas"), h("option", { value: "" }, "Todas"));
  mostrar("a_post", h("h2", {}, "Postulaciones"),
    h("p", { class: "sub" }, "Solicitudes de los estudiantes. Al aprobar o rechazar, el estudiante lo ve en su cuenta."),
    h("div", { class: "fila" }, h("label", { class: "campo" }, h("span", {}, "Mostrar"), filtro)),
    filas.length
      ? tabla(["Estudiante", "Semillero / grupo", "Convocatoria", "Cierra", "Nota", "Estado", "Acciones"],
          filas.map((f) => [f.estudiante, [f.semillero, f.grupo].filter(Boolean).join(" / ") || "—",
            f.convocatoria, fechaTxt(f.fecha_limite), f.nota || "—",
            h("td", {}, insigniaEstado(f.estado), f.comentario_vri ? h("div", { class: "sub" }, f.comentario_vri) : null),
            tdAcciones(
              h("button", { class: "btn chico primario", onclick: () => modalDecision(f, "aprobada") }, "Aprobar"),
              h("button", { class: "btn chico peligro", onclick: () => modalDecision(f, "rechazada") }, "Rechazar"))]))
      : h("div", { class: "vacio" }, "No hay postulaciones en esta lista."));
}

// ---------------------------------------------------------------------------
// Administrador: estudiantes
// ---------------------------------------------------------------------------
function modalEditarEstudiante(e) {
  const campos = {
    nombre: h("input", { type: "text", value: e.nombre, maxlength: "120" }),
    correo: h("input", { type: "text", value: e.correo || "", maxlength: "120" }),
    grupo: h("input", { type: "text", value: e.grupo || "", maxlength: "120" }),
    semillero: h("input", { type: "text", value: e.semillero || "", maxlength: "120" }),
  };
  const etiqueta = (t, c) => h("label", { class: "campo" }, h("span", {}, t), c);
  modal("Editar estudiante", h("div", { class: "rejilla" },
    etiqueta("Nombre", campos.nombre), etiqueta("Correo", campos.correo),
    etiqueta("Grupo de investigación", campos.grupo), etiqueta("Semillero", campos.semillero)), [
    { texto: "Cancelar" },
    { texto: "Guardar cambios", clase: "primario", accion: async () => {
      const cambios = {};
      for (const k of Object.keys(campos)) {
        const nuevo = campos[k].value.trim();
        if (nuevo !== (e[k] || "")) cambios[k] = nuevo;
      }
      if (cambios.grupo !== undefined || cambios.semillero !== undefined) {
        cambios.grupo = campos.grupo.value.trim();
        cambios.semillero = campos.semillero.value.trim();
      }
      if (!Object.keys(cambios).length) { aviso("No hiciste ningún cambio."); return false; }
      await api(`/admin/estudiantes/${e.id}`, { metodo: "PATCH", cuerpo: cambios });
      aviso("Cambios guardados.");
      render();
    } },
  ]);
}

async function vistaAdminEstudiantes() {
  const [estudiantes, grupos] = await Promise.all([api("/admin/estudiantes"), api("/admin/semilleros")]);
  const dlGrupos = h("datalist", { id: "dl-grupos" }, grupos.map((g) => h("option", { value: g.nombre })));
  const dlSem = h("datalist", { id: "dl-semilleros" },
    grupos.flatMap((g) => g.semilleros.map((s) => h("option", { value: s.nombre }))));
  const campo = (nombre, etiqueta, extra) => h("label", { class: "campo" }, h("span", {}, etiqueta),
    h("input", { type: "text", name: nombre, maxlength: "120", ...extra }));

  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const fd = Object.fromEntries(new FormData(ev.target).entries());
    const boton = ev.target.querySelector("button[type=submit]");
    await conBoton(boton, async () => {
      const r = await api("/admin/estudiantes", { metodo: "POST", cuerpo: {
        nombre: fd.nombre.trim(), correo: fd.correo.trim(),
        semillero: fd.semillero.trim(), grupo: fd.grupo.trim(),
        facultad: fd.facultad.trim() || null, descripcion: fd.descripcion.trim() || null } });
      aviso(`${r.nombre} quedó registrado.`);
      render();
    });
  } },
  h("div", { class: "rejilla" },
    campo("nombre", "Nombre completo", { required: true, minlength: "2" }),
    campo("correo", "Correo (opcional; sin él no podrá ingresar)", { type: "email" }),
    campo("grupo", "Grupo de investigación", { required: true, list: "dl-grupos", minlength: "2" }),
    campo("semillero", "Semillero (opcional)", { list: "dl-semilleros" }),
    campo("facultad", "Facultad (opcional)", {}),
    campo("descripcion", "Tema de trabajo, una frase (opcional)", { maxlength: "2000" })),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Agregar estudiante")),
  dlGrupos, dlSem);

  const filas = estudiantes.map((e) => [
    e.nombre, e.correo || "—", e.facultad || "—", e.semillero || "—", e.grupo || "—",
    h("td", {}, e.activo ? h("span", { class: "insignia ok" }, "Activo") : h("span", { class: "insignia mal" }, "Desactivado"),
      e.consentimiento ? null : h("div", { class: "sub" }, "Sin aceptar datos")),
    String(e.postulaciones),
    tdAcciones(
      h("button", { class: "btn chico", onclick: () => modalEditarEstudiante(e) }, "Editar"),
      h("button", { class: "btn chico", onclick: (ev) => conBoton(ev.currentTarget, async () => {
        await api(`/admin/estudiantes/${e.id}`, { metodo: "PATCH", cuerpo: { activo: !e.activo } });
        aviso(e.activo ? "Acceso desactivado." : "Acceso activado.");
        render();
      }) }, e.activo ? "Desactivar" : "Activar")),
  ]);

  mostrar("a_est", h("h2", {}, "Estudiantes"),
    h("div", { class: "tarjeta" }, h("h3", {}, "Agregar a una persona"), form),
    h("h3", {}, `Estudiantes registrados (${estudiantes.length})`),
    estudiantes.length
      ? tabla(["Nombre", "Correo", "Facultad", "Semillero", "Grupo", "Acceso", "Postulaciones", "Acciones"], filas)
      : h("div", { class: "vacio" }, "Aún no hay estudiantes. Agrégalos uno por uno o carga un Excel."));
}

// ---------------------------------------------------------------------------
// Administrador: carga desde Excel
// ---------------------------------------------------------------------------
async function descargarPlantilla() {
  const r = await fetch(API + "/admin/plantilla", { headers: { Authorization: "Bearer " + estado.token } });
  if (!r.ok) throw new Error("No se pudo descargar la plantilla.");
  descargarBlob(await r.blob(), "plantilla_semilleros.xlsx");
}

async function vistaAdminExcel() {
  const resultado = h("div", {});
  const archivo = h("input", { type: "file", accept: ".xlsx" });
  const botonSubir = h("button", { class: "btn primario" }, "Subir y registrar integrantes");
  botonSubir.addEventListener("click", () => conBoton(botonSubir, async () => {
    if (!archivo.files.length) { aviso("Elige primero un archivo .xlsx.", "error"); return; }
    const fd = new FormData();
    fd.append("archivo", archivo.files[0]);
    const r = await api("/admin/cargar-excel", { metodo: "POST", formData: fd });
    resultado.replaceChildren(
      h("div", { class: "aviso-caja" }, `Se registraron ${r.creados.length} integrantes, se completó el correo de ${(r.actualizados || []).length} y se omitieron ${r.omitidos.length} filas. Quien no tiene correo queda registrada, pero no puede ingresar hasta que se complete. Cada uno ingresa con su correo y recibe un código temporal.`),
      r.creados.length ? tabla(["Nombre", "Correo", "Semillero", "Grupo"],
        r.creados.map((c) => [c.nombre, c.correo, c.semillero, c.grupo])) : null,
      r.omitidos.length ? h("details", {}, h("summary", {}, `Filas omitidas (${r.omitidos.length})`),
        h("ul", { class: "simple" }, r.omitidos.map((o) => h("li", {}, `Fila ${o.fila}: ${o.motivo}`)))) : null);
    archivo.value = "";
  }));

  mostrar("a_excel", h("h2", {}, "Cargar Excel"),
    h("div", { class: "tarjeta" },
      h("p", {}, "Sube un Excel con las columnas ", h("b", {}, "nombre, correo, semillero y grupo"),
        ". El sistema crea los grupos y semilleros que no existan y genera un código de acceso por cada estudiante nuevo."),
      h("ul", { class: "simple" },
        h("li", {}, "Los estudiantes repetidos (mismo nombre en el semillero, o mismo correo) se omiten."),
        h("li", {}, "Puedes volver a subir el archivo cuando haya gente nueva.")),
      h("div", { class: "fila" },
        h("button", { class: "btn", onclick: (ev) => conBoton(ev.currentTarget, descargarPlantilla) }, "Descargar plantilla"),
        archivo, botonSubir)),
    resultado);
}

// ---------------------------------------------------------------------------
// Administrador: convocatorias y recolección automática
// ---------------------------------------------------------------------------
function modalNuevaConvocatoria() {
  const meta = estado.meta;
  const campo = (nombre, etiqueta, extra = {}) => h("label", { class: "campo" }, h("span", {}, etiqueta),
    h("input", { type: "text", name: nombre, ...extra }));
  const grupoCasillas = (nombre, etiqueta, categoria) => h("div", { class: "campo" }, h("span", {}, etiqueta),
    h("div", { class: "casillas" }, meta.categorias[categoria].map((v) => h("label", {},
      h("input", { type: "checkbox", name: nombre, value: v }), meta.etiquetas[categoria][v] || v))));
  const area = (nombre, etiqueta) => h("label", { class: "campo" }, h("span", {}, etiqueta),
    h("textarea", { name: nombre, maxlength: "2000", style: "min-height:70px" }));
  const form = h("form", { onsubmit: (ev) => ev.preventDefault() },
    h("div", { class: "rejilla" },
      campo("nombre", "Nombre de la convocatoria *", { maxlength: "300" }),
      campo("entidad", "Entidad", { maxlength: "200" }),
      campo("fecha_limite", "Fecha de cierre", { type: "date" }),
      campo("url", "Enlace oficial", { type: "url", maxlength: "500", placeholder: "https://" }),
      campo("monto_min", "Monto mínimo (COP)", { type: "number", min: "0" }),
      campo("monto_max", "Monto máximo (COP)", { type: "number", min: "0" })),
    area("objetivo", "Objetivo"), area("requisitos", "Requisitos"), area("beneficios", "Beneficios"),
    grupoCasillas("tipos", "¿A quién va dirigida?", "tipo_beneficiario"),
    grupoCasillas("areas", "Áreas", "area_conocimiento"),
    grupoCasillas("fases", "Fases", "fase_proyecto"),
    grupoCasillas("alcances", "Alcance", "alcance"));
  const marcados = (n) => [...form.querySelectorAll(`input[name="${n}"]:checked`)].map((i) => i.value);
  const valor = (n) => form.elements[n].value.trim();
  const numero = (n) => (valor(n) ? parseInt(valor(n), 10) : null);

  modal("Agregar convocatoria a mano", h("div", {},
    h("p", { class: "sub" }, "Si no marcas ninguna casilla de un grupo, se entiende que aplica a todas las opciones."), form), [
    { texto: "Cancelar" },
    { texto: "Guardar", clase: "primario", accion: async () => {
      if (valor("nombre").length < 3) { aviso("Escribe el nombre de la convocatoria.", "error"); return false; }
      await api("/admin/convocatorias", { metodo: "POST", cuerpo: {
        nombre: valor("nombre"), entidad: valor("entidad"), fecha_limite: valor("fecha_limite") || null,
        url: valor("url"), objetivo: valor("objetivo"), requisitos: valor("requisitos"),
        beneficios: valor("beneficios"), monto_min: numero("monto_min"), monto_max: numero("monto_max"),
        tipos_beneficiario: marcados("tipos"), areas: marcados("areas"),
        fases: marcados("fases"), alcances: marcados("alcances") } });
      aviso("Convocatoria guardada.");
      estado.ranking = null;
      render();
    } },
  ]);
}

function resumenInforme(inf) {
  if (!inf) return null;
  if (inf.error) return h("div", { class: "aviso-caja error" }, "La recolección falló: " + inf.error);
  return h("div", {},
    h("p", { class: "sub" }, `Última recolección: ${inf.inicio} → ${inf.fin}. Guardadas: ${inf.total_guardadas}.`),
    tabla(["Fuente", "Nuevas revisadas", "Guardadas", "Descartadas", "Vencidas", "Ya vistas / viejas", "Problemas"],
      inf.fuentes.map((f) => [f.nombre || f.fuente, String(f.enlaces), String(f.guardadas), String(f.descartadas),
        String(f.vencidas || 0), String(f.omitidas || 0),
        f.errores.length ? h("td", {}, h("ul", { class: "simple" }, f.errores.slice(0, 4).map((x) => h("li", {}, x)))) : "—"])));
}

async function vistaAdminConvocatorias() {
  const [convs, scr] = await Promise.all([api("/admin/convocatorias"), api("/admin/scrapers/estado")]);
  estado.scr = scr;
  const cambiar = (c, cuerpo, texto) => (ev) => conBoton(ev.currentTarget, async () => {
    await api(`/admin/convocatorias/${encodeURIComponent(c.id)}`, { metodo: "PATCH", cuerpo });
    aviso(texto);
    estado.ranking = null;
    render();
  });
  const filas = convs.map((c) => [
    h("td", {}, h("b", {}, c.nombre), h("div", { class: "sub" }, c.entidad || "")),
    fechaTxt(c.fecha_limite),
    c.fuente === "manual" ? "Manual" : c.fuente,
    c.verificada ? h("span", { class: "insignia ok" }, "Con visto bueno") : h("span", { class: "insignia alerta" }, "Pendiente de visto bueno"),
    c.activa ? "Sí" : h("span", { class: "insignia mal" }, "Oculta"),
    tdAcciones(
      c.verificada ? null : h("button", { class: "btn chico", onclick: cambiar(c, { verificada: true }, "Convocatoria con visto bueno: ya la ven los estudiantes.") }, "Dar visto bueno"),
      h("button", { class: "btn chico", onclick: cambiar(c, { activa: !c.activa }, c.activa ? "Convocatoria oculta." : "Convocatoria visible.") },
        c.activa ? "Ocultar" : "Mostrar"),
      urlSegura(c.url) ? h("a", { href: urlSegura(c.url), target: "_blank", rel: "noopener noreferrer" }, "Ver ↗") : null),
  ]);

  const activas = scr.fuentes.filter((f) => f.activa && f.url_listado).length;
  const botonScr = h("button", { class: "btn primario", disabled: scr.corriendo,
    onclick: (ev) => conBoton(ev.currentTarget, async () => {
      await api("/admin/scrapers/ejecutar", { metodo: "POST" });
      aviso("Recolección iniciada. Tarda unos minutos.");
      render();
    }) }, scr.corriendo ? "Recolectando…" : "Actualizar desde las fuentes");

  mostrar("a_conv", h("h2", {}, "Convocatorias"),
    h("div", { class: "tarjeta" },
      h("h3", {}, "Recolección automática"),
      h("p", {}, `El sistema lee ${activas} fuente(s) oficial(es) y guarda las convocatorias que encuentra, marcadas como “sin verificar” hasta que alguien las revise.`),
      h("ul", { class: "simple" }, scr.fuentes.map((f) => h("li", {},
        h("b", {}, f.nombre), f.activa && f.url_listado ? " · activa" : " · pendiente de configurar"))),
      h("div", { class: "fila" }, botonScr,
        h("button", { class: "btn", onclick: modalNuevaConvocatoria }, "Agregar convocatoria a mano")),
      scr.corriendo ? h("p", { class: "cargando" }, `En curso desde ${scr.inicio}…`) : resumenInforme(scr.informe)),
    h("h3", {}, `Catálogo (${convs.length})`),
    convs.length ? tabla(["Convocatoria", "Cierra", "Fuente", "Visto bueno", "Visible", "Acciones"], filas)
      : h("div", { class: "vacio" }, "No hay convocatorias."));

  detenerSondeo();
  if (scr.corriendo) {
    estado.temporizador = setTimeout(() => { if (estado.pestana === "a_conv") render(); }, 4000);
  }
}

// ---------------------------------------------------------------------------
// Administrador: otros administradores
// ---------------------------------------------------------------------------
async function vistaAdminAdmins() {
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const fd = Object.fromEntries(new FormData(ev.target).entries());
    await conBoton(ev.target.querySelector("button[type=submit]"), async () => {
      const r = await api("/admin/administradores", { metodo: "POST",
        cuerpo: { nombre: fd.nombre.trim(), correo: fd.correo.trim() } });
      aviso(`${r.nombre} quedó registrado como director/a. Ingresa con su correo.`);
      ev.target.reset();
    });
  } },
  h("div", { class: "rejilla" },
    h("label", { class: "campo" }, h("span", {}, "Nombre completo"),
      h("input", { type: "text", name: "nombre", required: true, minlength: "2", maxlength: "120" })),
    h("label", { class: "campo" }, h("span", {}, "Correo"),
      h("input", { type: "email", name: "correo", required: true, maxlength: "120" }))),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Agregar director/a")));

  mostrar("a_admin", h("h2", {}, "Directores (VRI)"),
    h("div", { class: "tarjeta" },
      h("p", {}, "Agrega a otra persona de la VRI. Tendrá el mismo acceso que tú: ver quién se postula, dar el visto bueno a postulaciones y convocatorias, y registrar integrantes. Ingresa con su correo."),
      form));
}

// ---------------------------------------------------------------------------
render();