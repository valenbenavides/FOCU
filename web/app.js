"use strict";

const API = "/api";
const $ = (s) => document.querySelector(s);

const estado = {
  token: null, yo: null, meta: null, pestana: null,
  ranking: null, chat: null, scr: null, temporizador: null, filtroPost: "solicitada",
};
try { estado.token = localStorage.getItem("focu_token"); } catch (e) {}

try { estado.pestana = localStorage.getItem("focu_pestana") || null; } catch (e) {}
try { estado.scrollInicial = JSON.parse(sessionStorage.getItem("focu_scroll") || "null"); } catch (e) { estado.scrollInicial = null; }
let _esperaScroll = null;
window.addEventListener("scroll", () => {
  clearTimeout(_esperaScroll);
  _esperaScroll = setTimeout(() => {
    if (!estado.token || !estado.pestana || estado.scrollInicial) return;   
    try { sessionStorage.setItem("focu_scroll", JSON.stringify({ p: estado.pestana, y: window.scrollY })); } catch (e) {}
  }, 150);
}, { passive: true });

const PESTANAS_ESTUDIANTE = [["perfil", "Mi perfil"], ["convocatorias", "Convocatorias"],
  ["postulaciones", "Postulaciones"], ["asistente", "Asistente IA"]];
const PESTANAS_DIRECTOR = [["g_sol", "Solicitudes de mi grupo"], ["g_post", "Revisar postulaciones"], ["g_mie", "Mi grupo"]];
const PESTANAS_ADMIN = [["a_post", "Postulaciones"], ["a_sol", "Solicitudes de registro"], ["a_gru", "Grupos"],
  ["a_est", "Personas"], ["a_conv", "Convocatorias"], ["a_admin", "Directores VRI"], ["asistente", "Asistente IA"]];

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
  if (valor !== undefined) el.value = valor;   
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
  const mapa = { solicitada: ["alerta", "Solicitada"], pendiente: ["alerta", "Pendiente"], aprobada: ["ok", "Aprobada"], rechazada: ["mal", "Rechazada"] };
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

function insigniaFase(fase) {
  const mapa = { en_director: ["alerta", "En revisión del director"], devuelta: ["mal", "Devuelta por el director"],
    en_vri: ["alerta", "En revisión VRI"], aprobada: ["ok", "Aprobada"], rechazada: ["mal", "Rechazada"] };
  const [clase, texto] = mapa[fase] || ["", fase];
  return h("span", { class: "insignia " + clase }, texto);
}

function resumenAvales(a) {
  if (!a) return "—";
  const t = { si: "Sí", no: "No", pendiente: "Pendiente", no_aplica: "No aplica" };
  const clase = (e) => (e === "si" ? "ok" : (e === "no" ? "mal" : "alerta"));
  const avales = a.departamento ? [
    h("span", { class: "insignia " + clase(a.departamento) }, "Dpto: " + t[a.departamento]), " ",
    h("span", { class: "insignia " + clase(a.facultad) }, "Facultad: " + t[a.facultad])] : [];
  return h("div", {}, ...avales,
    h("div", { class: "sub" }, `${a.cumplidos} de ${a.total} requisitos listos`));
}

async function modalRequisitos(postulacionId) {
  const datos = await api(`/postulaciones/${postulacionId}/requisitos`);
  const editable = datos.puede_editar;
  const etiquetas = { pendiente: "Pendiente", si: "Sí", no: "No", no_aplica: "No aplica" };
  const barra = h("span", { class: "req-barra-relleno" });
  const resumen = h("div", { class: "req-resumen" },
    h("div", { class: "req-barra" }, barra), h("p", { class: "sub req-texto" }));
  const pintarResumen = (r) => {
    resumen.querySelector(".req-texto").textContent = `${r.cumplidos} de ${r.total} requisitos obligatorios listos.`;
    barra.style.width = (r.total ? Math.round(100 * r.cumplidos / r.total) : 0) + "%";
  };
  pintarResumen(datos.resumen);
  const filas = datos.requisitos.map((r) => {
    const estadoSel = h("select", { disabled: !editable || !!r.solo_director, "aria-label": r.titulo, value: r.estado },
      ["pendiente", "si", "no", ...(r.opcional ? ["no_aplica"] : [])].map((e) => h("option", { value: e }, etiquetas[e])));
    const obs = h("input", { type: "text", maxlength: "500", placeholder: "Observación (opcional)",
      disabled: !editable || !!r.solo_director, value: r.observacion || "" });
    const icono = h("span", { class: "req-icono", "aria-hidden": "true" });
    const tarjetaReq = h("div", { class: "requisito" });
    const marcas = { si: "✓", no: "✗", pendiente: "•", no_aplica: "–" };
    const pintarEstado = () => {
      tarjetaReq.className = "requisito req-" + r.estado;
      icono.textContent = marcas[r.estado] || "•";
    };
    const guardar = async () => {
      try {
        const res = await api(`/postulaciones/${postulacionId}/requisitos/${r.clave}`,
          { metodo: "PUT", cuerpo: { estado: estadoSel.value, observacion: obs.value.trim() || null } });
        pintarResumen(res.resumen);
        r.estado = estadoSel.value;
      } catch (e) {
        aviso(e.message, "error");
        estadoSel.value = r.estado;
      }
      pintarEstado();
    };
    estadoSel.addEventListener("change", guardar);
    obs.addEventListener("change", guardar);
    pintarEstado();
    tarjetaReq.append(...[
      h("div", { class: "requisito-cab" }, icono,
        h("b", {}, r.titulo), r.formato ? h("span", { class: "insignia" }, r.formato) : null,
        r.opcional ? h("span", { class: "sub" }, " (si aplica)") : null),
      r.ayuda ? h("div", { class: "sub" }, r.ayuda) : null,
      h("div", { class: "rejilla" }, estadoSel, obs)].filter((n) => n !== null));
    return tarjetaReq;
  });
  modal("Lista de requisitos", h("div", { class: "lista-requisitos" },
    h("p", {}, h("b", {}, datos.postulacion.estudiante),
      datos.convocatoria ? h("span", { class: "sub" }, " · " + datos.convocatoria) : null),
    h("p", { class: "sub" }, datos.interna
      ? "Lista de chequeo de proyectos internos de la VRI (formato FOR-40)."
      : "Esta lista sale de los requisitos de la propia convocatoria. Revisa los términos de referencia para confirmarlos."),
    editable ? h("p", { class: "sub" }, "Marca cada requisito cuando ya lo tengas o lo cumplas. Se guarda solo."
      + (datos.interna ? " El aval de Facultad va después del de Departamento." : ""))
      : h("p", { class: "sub" }, estado.yo.rol === "admin" ? "Solo lectura."
          : "Solo lectura: la lista la marcan quien inició la postulación y el director del grupo, y ya no cambia una vez aprobada por la VRI."),
    editable && datos.requisitos.some((r) => r.solo_director)
      ? h("p", { class: "sub" }, "Los avales (Departamento y Facultad) los marca el director de tu grupo.") : null,
    resumen,
    h("div", { class: "req-leyenda" },
      h("span", { class: "req-chip req-si" }, "✓ Listo"), h("span", { class: "req-chip req-no" }, "✗ No cumple"),
      h("span", { class: "req-chip req-pendiente" }, "• Por marcar")),
    filas), [{ texto: "Cerrar", accion: async () => { render(); } }]);
}

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
    if (/^[=+\-@]/.test(t)) t = "'" + t;   
    return '"' + t.replace(/"/g, '""') + '"';
  };
  const lineas = [columnas.map((c) => esc(c.titulo)).join(",")]
    .concat(filas.map((f) => columnas.map((c) => esc(f[c.campo])).join(",")));
  descargarBlob(new Blob(["\ufeff" + lineas.join("\r\n")], { type: "text/csv;charset=utf-8" }), nombre);
}

function mostrar(pestana, ...nodos) {
  if (estado.pestana !== pestana) return;   
  
  $("#app").replaceChildren(...nodos.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));
}

function detenerSondeo() {
  if (estado.temporizador) { clearTimeout(estado.temporizador); estado.temporizador = null; }
}

function guardarToken(t) {
  estado.token = t;
  try { localStorage.setItem("focu_token", t); } catch (e) {}
}

function cerrarSesion(avisar = true) {
  estado.token = null; estado.yo = null; estado.ranking = null; estado.chat = null; estado.convId = null; estado.burbujaAbierta = false; estado.pestana = null;
  try { localStorage.removeItem("focu_token"); localStorage.removeItem("focu_pestana"); sessionStorage.removeItem("focu_scroll"); } catch (e) {}
  estado.scrollInicial = null;
  detenerSondeo();
  render();
  if (avisar) aviso("Sesión cerrada.");
}

function aplicarTamano() {
  let t = 16;
  try { t = Number(localStorage.getItem("focu_letra")) || 16; } catch (e) {}
  document.body.style.fontSize = t + "px";
}
function cambiarTamano(delta) {
  let t = 16;
  try { t = Number(localStorage.getItem("focu_letra")) || 16; } catch (e) {}
  t = delta === 0 ? 16 : Math.min(22, Math.max(13, t + delta));
  try { localStorage.setItem("focu_letra", String(t)); } catch (e) {}
  aplicarTamano();
}
aplicarTamano();

async function pintarPie() {
  let pie = document.querySelector(".pie");
  if (!pie) { pie = h("footer", { class: "pie" }); document.body.append(pie); }
  pie.hidden = !estado.token;
  let campos = [];
  try { campos = (await api("/contacto")).campos || []; } catch (e) {}
  const esCorreo = (v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v);
  pie.replaceChildren(...[
    h("p", {}, h("b", {}, "🔒 Tu información está protegida. "),
      "FOCU usa tus datos solo para recomendarte convocatorias y gestionar las postulaciones de tu grupo, "
      + "conforme a la Ley 1581 de 2012."),
    campos.length ? h("div", { class: "pie-contacto" },
      h("h4", {}, "Contáctanos"),
      campos.map((c) => h("p", {}, c.titulo + ": ",
        esCorreo(c.valor) ? h("a", { href: "mailto:" + c.valor }, c.valor) : c.valor))) : null,
  ].filter((n) => n !== null));   
}
pintarPie();

function cargoDe(yo) {
  if (yo.rol === "admin") return "Administrador VRI";
  return yo.es_director_grupo ? "Director/a de grupo" : "Estudiante";
}

function modalAyuda() {
  const yo = estado.yo;
  const pasos = yo.rol === "admin"
    ? ["Grupos y Personas: registra grupos, directores y estudiantes.",
       "Personas: agrega personas una por una o súbelas desde un archivo.",
       "Convocatorias: revisa lo que trae el recolector, da el visto bueno y elimina lo que no aplica.",
       "Postulaciones: responde las solicitudes que llegan a la VRI."]
    : yo.es_director_grupo
      ? ["Responde las solicitudes de registro de tu grupo.",
         "Revisa las convocatorias recomendadas para tu grupo y postúlalo.",
         "Usa el asistente para resolver dudas sobre requisitos."]
      : ["Mira las convocatorias que mejor encajan con tu perfil.",
         "Completa tu perfil para que las recomendaciones mejoren.",
         "Pregunta al asistente cuando tengas dudas sobre una convocatoria."];
  modal("¿Cómo funciona FOCU?", h("div", {},
    h("p", {}, "FOCU conecta a los grupos de investigación con convocatorias de financiación."),
    h("ul", { class: "simple" }, pasos.map((x) => h("li", {}, x)))),
    [{ texto: "Entendido", clase: "primario" }]);
}

function pintarUsuario() {
  const cont = $("#usuario");
  if (!estado.yo) { cont.replaceChildren(); return; }
  const yo = estado.yo;
  const inicial = (yo.nombre || "?").trim().charAt(0).toUpperCase();
  const menu = h("div", { class: "perfil-menu", hidden: true, role: "menu" });
  const cerrar = () => { menu.hidden = true; };
  const item = (texto, fn) => h("button", { class: "perfil-item", role: "menuitem",
    onclick: () => { cerrar(); fn(); } }, texto);
  const otrosPerfiles = (yo.perfiles || []).length > 1 ? yo.perfiles : [];
  menu.append(
    item("Mi información", () => { estado.pestana = "mi_info"; detenerSondeo(); render(); }),
    ...(otrosPerfiles.length ? [
      h("div", { class: "perfil-sep" }),
      h("div", { class: "perfil-etiqueta" }, "Cambiar de perfil"),
      ...otrosPerfiles.map((p) => h("button", { class: "perfil-item" + (p.actual ? " perfil-actual" : ""), role: "menuitem",
        disabled: p.actual, title: p.actual ? "Perfil actual" : "Cambiar a este perfil",
        onclick: async () => {
          cerrar();
          try {
            const r = await api("/yo/perfil", { metodo: "POST", cuerpo: { usuario_id: p.id } });
            guardarToken(r.token);
            estado.yo = null; estado.ranking = null; estado.chat = null; estado.convId = null;
            estado.pestana = null; estado.burbujaAbierta = false; detenerSondeo();
            try { localStorage.removeItem("focu_pestana"); } catch (e) {}
            render();
            aviso("Ahora estás como: " + p.etiqueta);
          } catch (e) { aviso(e.message, "error"); }
        } }, (p.actual ? "✓ " : "") + p.etiqueta)),
      h("div", { class: "perfil-sep" })] : []),
    h("div", { class: "perfil-tamano" }, h("span", {}, "Tamaño de letra"),
      h("div", {},
        h("button", { class: "btn chico", "aria-label": "Letra más pequeña", onclick: () => cambiarTamano(-1) }, "A−"),
        h("button", { class: "btn chico", "aria-label": "Letra normal", onclick: () => cambiarTamano(0) }, "A"),
        h("button", { class: "btn chico", "aria-label": "Letra más grande", onclick: () => cambiarTamano(1) }, "A+"))),
    item("Ayuda", modalAyuda),
    h("div", { class: "perfil-sep" }),
    item("Cerrar sesión", () => cerrarSesion()));
  const chip = h("button", { class: "perfil-chip", "aria-haspopup": "menu",
    onclick: (ev) => { ev.stopPropagation(); menu.hidden = !menu.hidden; } },
    h("span", { class: "perfil-avatar" }, inicial),
    h("span", { class: "perfil-datos" },
      ...(yo.rol !== "admin" && yo.grupo ? [h("small", { class: "perfil-grupo", title: yo.grupo }, yo.grupo)] : []),
      h("b", {}, yo.nombre), h("small", {}, cargoDe(yo))),
    h("span", { class: "perfil-flecha", "aria-hidden": "true" }, "▾"));
  cont.replaceChildren(chip, menu);
  if (!window.__perfilCierra) {
    window.__perfilCierra = true;
    document.addEventListener("click", () => document.querySelectorAll(".perfil-menu").forEach((m) => { m.hidden = true; }));
  }
}

function modalConfirmarCorreo(nuevo) {
  const codigo = cajasCodigo(6);
  modal("Confirma tu nuevo correo", h("div", {},
    h("p", {}, "Te enviamos un código de 6 dígitos a ", h("b", {}, nuevo), ". Escríbelo para confirmar el cambio."),
    h("label", { class: "campo" }, h("span", {}, "Código"), codigo)), [
    { texto: "Cancelar" },
    { texto: "Confirmar correo", clase: "primario", accion: async () => {
      if (codigo.value.length < 6) { aviso("Escribe los 6 dígitos.", "error"); return false; }
      await api("/yo/correo/confirmar", { metodo: "POST", cuerpo: { codigo: codigo.value } });
      aviso("Correo actualizado. Desde ahora ingresas con " + nuevo + ".");
      estado.yo = null;
      render();
    } },
  ]);
  setTimeout(() => codigo.focus(), 50);
}

async function crearTarjetaContacto() {
  const lista = h("div", { class: "contacto-filas" });
  let guardados = [];            
  try { guardados = (await api("/contacto")).campos || []; } catch (e) {}

  const publicar = async (campos) => {
    const r = await api("/admin/contacto", { metodo: "PUT", cuerpo: { campos } });
    guardados = r.campos;
    pintarPie();
  };

  const nueva = (dato) => {
    let guardada = dato ? { ...dato } : null;     
    const t = h("input", { type: "text", value: dato ? dato.titulo : "", maxlength: "60",
      placeholder: "Campo (ej.: Dirección)", "aria-label": "Campo" });
    const v = h("input", { type: "text", value: dato ? dato.valor : "", maxlength: "300",
      placeholder: "Información", "aria-label": "Información" });
    const fila = h("div", { class: "contacto-fila" });
    const bAct = h("button", { class: "btn chico", type: "button" }, "Actualizar");
    const bEli = h("button", { class: "btn chico peligro", type: "button" }, "Eliminar");
    const sinGuardar = () => !guardada || guardada.titulo !== t.value.trim() || guardada.valor !== v.value.trim();

    bAct.addEventListener("click", () => conBoton(bAct, async () => {
      const nuevoDato = { titulo: t.value.trim(), valor: v.value.trim() };
      if (!nuevoDato.titulo || !nuevoDato.valor) { aviso("Escribe el campo y su información.", "error"); return; }
      const lo = guardada ? guardados.map((c) => (c.titulo === guardada.titulo && c.valor === guardada.valor ? nuevoDato : c))
        : [...guardados, nuevoDato];
      await publicar(lo);
      guardada = nuevoDato;
      aviso("Contacto actualizado.");
    }));

    bEli.addEventListener("click", () => {
      const quitar = async () => {
        if (guardada) await publicar(guardados.filter((c) => !(c.titulo === guardada.titulo && c.valor === guardada.valor)));
        fila.remove();
        if (!lista.children.length) nueva();
        if (guardada) aviso("Campo eliminado.");
      };
      if (!guardada) { quitar(); return; }
      modal("Eliminar campo", h("div", {},
        h("p", {}, h("b", {}, guardada.titulo + ": "), guardada.valor),
        h("p", { class: "sub" }, "Deja de mostrarse en el pie de página.")), [
        { texto: "Cancelar" },
        { texto: "Eliminar", clase: "peligro", accion: quitar },
      ]);
    });
    fila.append(t, v, bAct, bEli);
    lista.append(fila);
  };

  guardados.forEach((c) => nueva(c));
  if (!guardados.length) nueva();
  return h("div", { class: "tarjeta" }, h("h3", {}, "Contáctanos (pie de página)"),
    h("p", { class: "sub" }, "Lo que escribas aquí aparece al final de todas las pantallas. Cada fila es un campo y su información; "
      + "usa Actualizar en la fila para publicar el cambio."),
    lista,
    h("div", { class: "fila", style: "margin-top:.75rem" },
      h("button", { class: "btn", type: "button", onclick: () => nueva() }, "+ Agregar campo")));
}

async function vistaMiInformacion() {
  estado.yo = await api("/yo");          
  const yo = estado.yo;
  const esAdmin = yo.rol === "admin";
  const puedeCorreo = esAdmin || !!yo.es_director_grupo;   
  let editando = false;
  const tarjetaContacto = esAdmin ? await crearTarjetaContacto() : null;

  const fila = (titulo, valor) => h("div", { class: "info-fila" },
    h("span", {}, titulo), h("b", {}, valor || "No registrada"));

  function pintar() {
    const tarjeta = h("div", { class: "tarjeta" }, h("h3", {}, "Información de perfil"));
    if (!editando) {
      tarjeta.append(
        fila("Nombre", yo.nombre), fila("Correo", yo.correo), fila("Cargo", cargoDe(yo)),
        ...(esAdmin ? [] : [fila("Facultad", yo.facultad), fila("Grupo de investigación", yo.grupo),
          fila("Semillero", yo.semillero)]),
        h("div", { class: "fila", style: "margin-top:1rem" },
          h("button", { class: "btn", onclick: () => { editando = true; pintar(); } }, "Actualizar datos")));
    } else {
      const nombre = h("input", { type: "text", value: yo.nombre, required: true, minlength: "2", maxlength: "120" });
      const correo = h("input", { type: "email", value: yo.correo || "", required: true, maxlength: "120" });
      const facultad = selectFacultad(yo.facultad || "");
      const semillero = h("input", { type: "text", value: /^general \(sin semillero\)$/i.test(yo.semillero || "") ? "" : (yo.semillero || ""),
        maxlength: "120", placeholder: "Ej.: Semillero de Energías Renovables" });
      const et = (t, c) => h("label", { class: "campo" }, h("span", {}, t), c);
      const form = h("form", { onsubmit: async (ev) => {
        ev.preventDefault();
        const cuerpo = { nombre: nombre.value.trim() };
        const correoNuevo = correo.value.trim();
        const cambiaCorreo = puedeCorreo && correoNuevo.toLowerCase() !== (yo.correo || "").toLowerCase();
        if (!esAdmin) {
          cuerpo.facultad = facultad.value;
          if (semillero.value.trim().length >= 2) cuerpo.semillero = semillero.value.trim();
        }
        await conBoton(ev.target.querySelector("button[type=submit]"), async () => {
          if (cuerpo.nombre !== yo.nombre || cuerpo.facultad !== undefined || cuerpo.semillero !== undefined) {
            await api("/yo", { metodo: "PATCH", cuerpo });
            aviso("Datos actualizados.");
          }
          if (cambiaCorreo) {
            await api("/yo/correo", { metodo: "POST", cuerpo: { correo: correoNuevo } });
            modalConfirmarCorreo(correoNuevo);
            return;
          }
          estado.yo = null;
          render();
        });
      } },
      h("div", { class: "rejilla" },
        et("Nombre", nombre),
        ...(puedeCorreo ? [et("Correo", correo)] : []),
        ...(esAdmin ? [] : [et("Facultad", facultad), et("Semillero (opcional)", semillero)])),
      h("p", { class: "sub" }, esAdmin ? "Si cambias el correo, te enviamos un código para confirmarlo."
        : puedeCorreo ? "Si cambias el correo, te enviamos un código para confirmarlo. El grupo no se cambia aquí: pídelo a la VRI."
        : "El correo y el grupo no se cambian aquí: si están mal, pídelo a tu director de grupo o a la VRI."),
      h("div", { class: "fila" },
        h("button", { class: "btn primario", type: "submit" }, "Guardar cambios"),
        h("button", { class: "btn", type: "button", onclick: () => { editando = false; pintar(); } }, "Cancelar")));
      tarjeta.append(form);
    }
    mostrar("mi_info", h("h2", {}, "Mi información"),
      h("div", { class: esAdmin ? "info-pagina dos" : "info-pagina" }, tarjeta, esAdmin ? tarjetaContacto : null));
  }
  pintar();
}

function pintarMenu(pestanas) {
  const menu = $("#menu");
  menu.hidden = false;
  menu.replaceChildren(...pestanas.map(([id, texto]) => h("button", {
    class: id === estado.pestana ? "activa" : "",
    onclick: () => { estado.pestana = id; detenerSondeo(); render(); },
  }, texto)));
}

function pintarPersonajes(mostrar) {
  let cont = document.querySelector(".personajes");
  if (!mostrar) { if (cont) cont.remove(); return; }
  if (cont) return;
  cont = h("div", { class: "personajes", "aria-hidden": "true" },
    ["cientifica", "medico", "ingeniero", "musica", "abogada"].map((n) =>
      h("img", { class: "pj pj-" + n, src: `/static/assets/${n}.jpg`, alt: "", draggable: "false" })));
  document.body.prepend(cont);
}

function logoFocu() {
  return h("div", { class: "logo-focu" },
    h("span", { class: "bulbo", "aria-hidden": "true" }, "💡"),
    h("span", { class: "letras" }, "FOCU"));
}
(function luzDelBombillo() {
  const ALCANCE = 520;   
  let pendiente = false, ultimo = null;
  window.addEventListener("mousemove", (ev) => {
    ultimo = ev;
    if (pendiente) return;
    pendiente = true;
    requestAnimationFrame(() => {
      pendiente = false;
      const b = document.querySelector(".logo-focu .bulbo");
      if (!b || !ultimo) return;
      const r = b.getBoundingClientRect();
      const d = Math.hypot(ultimo.clientX - (r.left + r.width / 2), ultimo.clientY - (r.top + r.height / 2));
      const t = Math.max(0, Math.min(1, 1 - d / ALCANCE));
      b.style.setProperty("--luz", (t * t * (3 - 2 * t)).toFixed(3));   
    });
  }, { passive: true });
})();

async function render() {
  const app = $("#app");
  const pie = document.querySelector(".pie");
  if (pie) pie.hidden = !estado.token;      
  document.body.classList.toggle("sin-sesion", !estado.token);   
  pintarPersonajes(!estado.token);
  if (!estado.token) {
    pintarBurbuja();
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
    if (!estado.token) return;   
    app.replaceChildren(h("div", { class: "aviso-caja error" }, e.message + " ",
      h("button", { class: "btn chico", onclick: render }, "Reintentar")));
    return;
  }
  pintarUsuario();
  const esAdmin = estado.yo.rol === "admin";
  if (!esAdmin && !estado.yo.consentimiento) {
    $("#menu").hidden = true;
    pintarBurbuja();
    return vistaConsentimiento();
  }
  const pestanas = esAdmin ? PESTANAS_ADMIN
    : (estado.yo.es_director_grupo ? [...PESTANAS_DIRECTOR, ...PESTANAS_ESTUDIANTE] : PESTANAS_ESTUDIANTE);
  if (estado.pestana !== "mi_info" && !pestanas.some(([id]) => id === estado.pestana)) estado.pestana = pestanas[0][0];
  pintarMenu(pestanas);
  pintarBurbuja();
  try { localStorage.setItem("focu_pestana", estado.pestana); } catch (e) {}
  const vistas = {
    perfil: vistaPerfil, convocatorias: vistaConvocatorias, postulaciones: vistaMisPostulaciones,
    asistente: vistaAsistente, g_sol: vistaGrupoSolicitudes, g_post: vistaGrupoPostulaciones, g_mie: vistaGrupoMiembros,
    a_sol: vistaAdminSolicitudes, a_gru: vistaAdminGrupos, a_post: vistaAdminPostulaciones, a_est: vistaAdminEstudiantes,
    a_conv: vistaAdminConvocatorias, a_admin: vistaAdminAdmins,
    mi_info: vistaMiInformacion,
  };
  try { await vistas[estado.pestana](); }
  catch (e) { if (estado.token) mostrar(estado.pestana, h("div", { class: "aviso-caja error" }, e.message)); }
  
  if (estado.scrollInicial) {
    const sc = estado.scrollInicial;
    estado.scrollInicial = null;
    if (sc.p === estado.pestana && sc.y > 0) requestAnimationFrame(() => window.scrollTo(0, sc.y));
  }
}

function cajasCodigo(largo = 6) {
  const cont = h("div", { class: "cajas-codigo", role: "group", "aria-label": `Código de ${largo} dígitos` });
  const solo = (t) => String(t || "").replace(/\D/g, "");
  const cajas = Array.from({ length: largo }, (_, i) => h("input", {
    type: "text", inputmode: "numeric", pattern: "[0-9]*",
    maxlength: i === 0 ? String(largo) : "1",           
    autocomplete: i === 0 ? "one-time-code" : "off", "aria-label": `Dígito ${i + 1}`,
  }));
  function repartir(desde, digitos) {
    digitos.slice(0, largo - desde).split("").forEach((d, k) => { cajas[desde + k].value = d; });
    cajas[Math.min(desde + digitos.length, largo - 1)].focus();
  }
  cajas.forEach((c, i) => {
    c.addEventListener("input", () => {
      const d = solo(c.value);
      if (d.length > 1) { c.value = ""; repartir(i, d); return; }
      c.value = d;
      if (d && i < largo - 1) cajas[i + 1].focus();
    });
    c.addEventListener("keydown", (ev) => {
      if (ev.key === "Backspace" && !c.value && i > 0) {
        ev.preventDefault(); cajas[i - 1].value = ""; cajas[i - 1].focus();
      } else if (ev.key === "ArrowLeft" && i > 0) { ev.preventDefault(); cajas[i - 1].focus(); }
      else if (ev.key === "ArrowRight" && i < largo - 1) { ev.preventDefault(); cajas[i + 1].focus(); }
    });
    c.addEventListener("paste", (ev) => {
      const d = solo((ev.clipboardData || window.clipboardData).getData("text"));
      if (d) { ev.preventDefault(); c.value = ""; repartir(i, d); }
    });
    c.addEventListener("focus", () => c.select());
  });
  cont.replaceChildren(...cajas);
  Object.defineProperty(cont, "value", {
    get: () => cajas.map((c) => c.value).join(""),
    set: (v) => { cajas.forEach((c, i) => { c.value = solo(v)[i] || ""; }); },
  });
  cont.focus = () => cajas[0].focus();
  cont.select = () => { cajas.forEach((c) => { c.value = ""; }); cajas[0].focus(); };
  return cont;
}

function vistaLogin() {
  const correo = h("input", { type: "email", placeholder: "tu.correo@unicauca.edu.co", autocomplete: "email",
    maxlength: "120", required: true, "aria-label": "Correo" });
  const codigo = cajasCodigo(6);
  const bPedir = h("button", { class: "btn primario", type: "submit" }, "Enviarme un código");
  const bEntrar = h("button", { class: "btn primario", type: "submit" }, "Ingresar");
  const msg = h("p", { class: "sub" });
  const irRegistro = h("button", { class: "btn", type: "button", hidden: true, onclick: () => vistaRegistro() },
    "Solicitar mi registro");
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
      irRegistro.hidden = r.estado !== "no_registrado";
      paso2.hidden = r.enviado === false;
      if (r.enviado !== false) codigo.focus();
    } catch (e) { msg.textContent = ""; irRegistro.hidden = true; aviso(e.message, "error"); }
    bPedir.disabled = false;
  }
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    if (paso2.hidden) { await pedir(); return; }
    if (codigo.value.length < 6) { aviso("Escribe los 6 dígitos del código.", "error"); return; }
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
  h("div", { class: "fila" }, bPedir), paso2, msg, irRegistro);
  $("#app").replaceChildren(h("div", { class: "ingreso" }, logoFocu(), h("div", { class: "tarjeta" },
    h("h2", {}, "Ingresar"),
    h("p", {}, "Escribe el correo con el que tu grupo de investigación te registró. Te enviaremos un código temporal."),
    form,
    h("p", { class: "sub" }, "¿Aún no tienes cuenta? ",
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); vistaRegistro(); } }, "Solicita tu registro"),
      " eligiendo tu grupo de investigación; el director del grupo la aprueba."))));
  correo.focus();
}

async function vistaRegistro() {
  const app = $("#app");
  app.replaceChildren(h("p", { class: "cargando" }, "Cargando grupos…"));
  let grupos;
  try { grupos = await api("/grupos"); }
  catch (e) { vistaLogin(); aviso(e.message, "error"); return; }

  const SIN_FACULTAD = "__sin__";
  const limpiar = (t) => (t || "").replace(/\s+/g, " ").trim();
  const facultadDe = (g) => limpiar(g.facultad) || SIN_FACULTAD;
  const facultades = [...new Set(grupos.map(facultadDe))].sort((a, b) => a.localeCompare(b, "es"));

  const facultad = h("select", { required: true, "aria-label": "Facultad" },
    h("option", { value: "" }, "Elige tu facultad…"),
    facultades.map((f) => h("option", { value: f }, f === SIN_FACULTAD ? "Sin facultad registrada" : f)));
  const buscar = h("input", { type: "text", placeholder: "Escribe para filtrar los grupos…", "aria-label": "Buscar grupo" });
  const lista = h("select", { size: "6", required: true, "aria-label": "Grupo de investigación" });
  
  const nombreElegido = h("span", { class: "grupo-elegido-nombre" });
  const cajaBusqueda = h("div", {}, buscar, lista);
  const elegido = h("div", { class: "grupo-elegido", hidden: true }, nombreElegido,
    h("button", { class: "btn chico", type: "button", onclick: () => mostrarBusqueda() }, "Cambiar"));
  const nombre = h("input", { type: "text", minlength: "2", maxlength: "120", required: true, autocomplete: "name" });
  const correo = h("input", { type: "email", maxlength: "120", required: true, autocomplete: "email",
    placeholder: "tu.correo@unicauca.edu.co" });
  const codigo = cajasCodigo(6);
  const msg = h("p", { class: "sub" });
  const bEnviar = h("button", { class: "btn primario", type: "submit" }, "Enviar solicitud");
  const bConfirmar = h("button", { class: "btn primario", type: "submit" }, "Confirmar mi correo");
  const paso2 = h("div", { hidden: true },
    h("label", { class: "campo" }, h("span", {}, "Código de 6 dígitos que te enviamos al correo"), codigo),
    h("div", { class: "fila" }, bConfirmar));

  function pintarLista() {
    const q = buscar.value.trim().toLowerCase();
    const previo = lista.value;
    const delaFacultad = grupos.filter((g) => facultad.value && facultadDe(g) === facultad.value);
    lista.replaceChildren(...delaFacultad
      .filter((g) => !q || g.nombre.toLowerCase().includes(q))
      .sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))
      .map((g) => h("option", { value: String(g.id) }, g.nombre)));
    if (previo) lista.value = previo;
    buscar.disabled = !facultad.value;
    buscar.placeholder = facultad.value ? "Escribe para filtrar los grupos…" : "Primero elige tu facultad";
  }
  function mostrarBusqueda() {
    elegido.hidden = true;
    cajaBusqueda.hidden = false;
    buscar.focus();
  }
  function mostrarElegido() {
    const g = grupos.find((x) => String(x.id) === lista.value);
    if (!g) return;
    nombreElegido.textContent = g.nombre;
    cajaBusqueda.hidden = true;
    elegido.hidden = false;
  }
  buscar.addEventListener("input", pintarLista);
  facultad.addEventListener("change", () => {
    buscar.value = ""; lista.value = ""; elegido.hidden = true; cajaBusqueda.hidden = false; pintarLista();
  });
  lista.addEventListener("change", mostrarElegido);
  pintarLista();

  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    if (!paso2.hidden) {
      if (codigo.value.length < 6) { aviso("Escribe los 6 dígitos del código.", "error"); return; }
      bConfirmar.disabled = true;
      try {
        const r = await api("/registro/confirmar", { metodo: "POST",
          cuerpo: { correo: correo.value.trim(), codigo: codigo.value.trim() } });
        app.replaceChildren(h("div", { class: "ingreso" }, logoFocu(), h("div", { class: "tarjeta" },
          h("h2", {}, "Solicitud enviada"), h("p", {}, r.mensaje),
          h("button", { class: "btn primario", onclick: vistaLogin }, "Volver al ingreso"))));
      } catch (e) { aviso(e.message, "error"); bConfirmar.disabled = false; codigo.select(); }
      return;
    }
    if (!facultad.value) { aviso("Elige tu facultad.", "error"); return; }
    if (!lista.value) { aviso("Elige tu grupo de investigación de la lista.", "error"); return; }
    bEnviar.disabled = true;
    try {
      const r = await api("/registro/solicitar", { metodo: "POST", cuerpo: {
        nombre: nombre.value.trim(), correo: correo.value.trim(),
        facultad: facultad.value === SIN_FACULTAD ? "" : facultad.value,
        grupo_id: Number(lista.value) } });
      msg.textContent = r.mensaje;
      paso2.hidden = false;
      codigo.focus();
    } catch (e) { aviso(e.message, "error"); }
    bEnviar.disabled = false;
  } },
  h("label", { class: "campo" }, h("span", {}, "1. Tu facultad"), facultad),
  h("div", { class: "campo" }, h("span", {}, "2. Tu grupo de investigación"), cajaBusqueda, elegido),
  h("div", { class: "rejilla" },
    h("label", { class: "campo" }, h("span", {}, "Tu nombre completo"), nombre),
    h("label", { class: "campo" }, h("span", {}, "Tu correo"), correo)),
  h("div", { class: "fila" }, bEnviar), msg, paso2);

  app.replaceChildren(h("div", { class: "ingreso" }, logoFocu(), h("div", { class: "tarjeta" },
    h("h2", {}, "Solicitar registro"),
    h("p", {}, "Elige tu facultad y tu grupo de investigación, escribe tu nombre y tu correo. Confirmas el correo con un código y tu solicitud le llega al director del grupo, que la aprueba o la rechaza."),
    form,
    h("p", { class: "sub" }, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); vistaLogin(); } },
      "← Ya tengo cuenta, quiero ingresar")))));
  facultad.focus();
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

async function vistaPerfil() {
  const meta = estado.meta;
  const p = estado.yo.perfil || {};
  const selector = (nombre, etiqueta, placeholder, opciones) => h("label", { class: "campo" },
    h("span", {}, etiqueta),
    h("select", { name: nombre, value: p[nombre] || "" },
      h("option", { value: "" }, placeholder),
      (opciones || meta.categorias[nombre]).map((o) => h("option", { value: o }, meta.etiquetas[nombre][o] || o))));

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
    meta.tipo_fijo ? null : selector("tipo_beneficiario", "Tipo de beneficiario", "TIPO DE BENEFICIARIO:", meta.tipos_perfil),
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
    h("p", { class: "sub" }, "Cuéntanos qué necesitas financiar. Mientras más completo, mejor el resultado. Si buscas una beca y algún campo no aplica "
      + "(por ejemplo fase o alcance), déjalo vacío: no cuenta en el porcentaje. Puedes cambiarlo cuando quieras."),
    form);
}

const pesos = (n) => (n || n === 0 ? "$ " + new Intl.NumberFormat("es-CO").format(n) + " COP" : null);

function modalFichaConvocatoria(r) {
  const seccion = (titulo, ...contenido) => {
    const util = contenido.filter((c) => c !== null && c !== undefined && c !== false);
    return util.length ? h("div", { class: "ficha-seccion" }, h("h4", {}, titulo), util) : null;
  };
  const lista = (items) => (items && items.length ? h("ul", { class: "simple" }, items.map((t) => h("li", {}, t))) : null);
  const dato = (etiqueta, valor) => (valor ? h("div", {}, h("b", {}, etiqueta + ": "), valor) : null);
  const enlace = urlSegura(r.url);
  const datos = [
    dato("Estado", r.estado_fuente), dato("Apertura", r.fecha_apertura ? fechaTxt(r.fecha_apertura) : null),
    dato("Cierre", r.fecha_limite ? fechaTxt(r.fecha_limite) : null),
    dato("Recursos totales", pesos(r.recursos_totales)),
    dato("Monto mínimo por proyecto", pesos(r.monto_min)), dato("Monto máximo por proyecto", pesos(r.monto_max)),
    dato("Duración máxima de los proyectos", r.duracion_meses ? `${r.duracion_meses} meses` : null),
    dato("Contrapartida", r.contrapartida), dato("Entidad", r.entidad),
  ];
  const cuerpo = h("div", { class: "ficha-conv" },
    r.verificada ? null : h("div", { class: "aviso-caja" },
      "Estos datos se extrajeron automáticamente de la página oficial y aún no los revisa la VRI. Confírmalos en la fuente."),
    seccion("Datos clave", ...datos),
    seccion("A quién va dirigida", r.dirigida_a ? h("p", {}, r.dirigida_a) : null),
    seccion("De qué trata", r.objetivo ? h("p", {}, r.objetivo) : null),
    seccion("Ejes o temas que prioriza", lista(r.ejes_tematicos)),
    seccion("Requisitos", r.leido_tdr ? h("p", { class: "sub" }, "Leídos de los términos de referencia (PDF) de la convocatoria.") : null,
      lista(r.requisitos_lista),
      !(r.requisitos_lista || []).length && r.requisitos ? h("p", {}, r.requisitos) : null),
    seccion("Qué ofrece", r.beneficios ? h("p", {}, r.beneficios) : null),
    seccion("Criterios de evaluación", lista(r.criterios_evaluacion)),
    seccion("Cronograma", (r.cronograma || []).length
      ? tabla(["Actividad", "Fecha"], r.cronograma.map((f) => [f.actividad, f.fecha || fechaTxt(f.fecha_iso)])) : null),
    seccion("Documentos de la convocatoria", (r.documentos || []).length
      ? h("ul", { class: "simple" }, r.documentos.map((d) => h("li", {},
          h("a", { href: urlSegura(d.url) || "#", target: "_blank", rel: "noopener noreferrer" }, d.titulo)))) : null),
    seccion("Qué te falta para cumplir", (r.faltantes || []).length
      ? h("ul", { class: "simple" }, r.faltantes.map((t) => h("li", {}, t)))
      : h("p", {}, "Con lo que indicaste en tu perfil no se detectan faltantes.")),
    seccion("Por qué este porcentaje", (() => {
      const { contados, nota } = lineasPorcentaje(r);
      return [...contados, ...nota];
    })()),
    seccion("Fuente oficial", enlace ? h("a", { href: enlace, target: "_blank", rel: "noopener noreferrer" }, "Abrir la página de la convocatoria ↗") : null));
  modal(r.nombre, cuerpo, [{ texto: "Cerrar" }]);
}

const ETIQUETAS_CALCULO = { area_conocimiento: "Área", fase_proyecto: "Fase", monto: "Monto",
  alcance: "Alcance", afinidad_tematica: "Afinidad temática" };
function lineasPorcentaje(r) {
  const filas = r.desglose || [];
  if (!filas.length) return { contados: [], nota: [] };
  const tab = tabla(["Criterio", "Tu perfil", "La convocatoria", "Resultado", "Peso", "Aporta"],
    filas.map((f) => [h("td", { style: f.cuenta ? "" : "color:var(--gris)" }, h("b", {}, f.criterio)),
      f.tu_perfil, f.convocatoria, f.resultado,
      f.cuenta ? f.peso + " %" : "—", f.cuenta ? f.aporte.toFixed(1) + " pts" : "—"]));
  const pie = h("div", {},
    h("p", {}, h("b", {}, `Compatibilidad total: ${r.porcentaje} %`),
      "  (suma de lo que aporta cada criterio que sí cuenta)"),
    h("p", { class: "sub" }, "El tema del proyecto es el criterio que más pesa. Los criterios que no indicaste, o que la convocatoria no "
      + "especifica, no cuentan: su peso se reparte entre los que sí se pueden comparar."));
  return { contados: [tab], nota: [pie] };
}

function tarjetaConvocatoria(r, yaPostulado) {
  const { contados, nota } = lineasPorcentaje(r);
  const enlace = urlSegura(r.url);
  const botonPostular = yaPostulado
    ? h("div", {}, insigniaFase(yaPostulado.fase),
        h("div", { class: "sub" }, yaPostulado.mia ? "La iniciaste tú, para tu grupo"
          : `Postulación de tu grupo, iniciada por ${yaPostulado.por}`),
        (yaPostulado.fase === "en_director" && estado.yo.es_director_grupo && !yaPostulado.mia)
          ? h("button", { class: "btn chico primario", onclick: () => { estado.pestana = "g_post"; render(); } },
              "Te falta revisarla: ir a Revisar postulaciones") : null)
    : h("button", { class: "btn primario", onclick: () => modalPostular(r) }, "Quiero postularme");
  return h("div", { class: "tarjeta" },
    h("div", { class: "cabeza" },
      h("div", {},
        h("h3", {}, r.nombre),
        h("div", { class: "sub" }, `${r.entidad || "Entidad no indicada"} · cierra ${fechaTxt(r.fecha_limite)}`),
        r.verificada ? null : h("span", { class: "insignia alerta" }, "Datos extraídos automáticamente: verifícalos en la fuente")),
      r.calculable === false
        ? h("div", { class: "porcentaje" }, h("b", {}, "—"), h("span", { class: "sub" }, "sin calcular"))
        : h("div", { class: "porcentaje" }, h("b", {}, r.porcentaje + "%"), h("span", { class: "sub" }, "compatibilidad"))),
    r.calculable === false ? null
      : h("div", { class: "barra" }, h("i", { style: `width:${Math.max(0, Math.min(100, r.porcentaje))}%` })),
    r.objetivo ? h("details", {}, h("summary", {}, "De qué trata"), h("p", {}, r.objetivo)) : null,
    h("details", {}, h("summary", {}, "Ventajas y beneficios"), h("p", {}, r.beneficios || "No especificadas.")),
    h("details", {}, h("summary", {}, "Requisitos y documentos"), contenidoRequerimientos(r, false)),
    h("details", {}, h("summary", {}, `Qué te falta para cumplir${(r.faltantes || []).length ? " (" + r.faltantes.length + ")" : ""}`),
      (r.faltantes || []).length ? h("ul", { class: "simple" }, r.faltantes.map((t) => h("li", {}, t)))
        : h("p", {}, "Con lo que indicaste en tu perfil no se detectan faltantes."),
      h("p", { class: "sub" }, "Revisa también los requisitos propios de la convocatoria (documentos, avales, fechas) en «Requisitos y documentos».")),
    (contados.length || nota.length) ? h("details", {}, h("summary", {}, "Por qué este porcentaje"),
      ...contados, ...nota) : null,
    h("div", { class: "fila" },
      botonPostular,
      h("button", { class: "btn", onclick: () => modalFichaConvocatoria(r) }, "Ver ficha completa"),
      enlace ? h("a", { href: enlace, target: "_blank", rel: "noopener noreferrer" }, "Ir a la convocatoria ↗") : null));
}

function modalPostular(r) {
  const titulo = h("input", { type: "text", maxlength: "200", placeholder: "Opcional: título del proyecto" });
  const nota = h("textarea", { maxlength: "1000", placeholder: "Opcional: cuéntale a tu director por qué quieren postularse." });
  modal("Postular a mi grupo", h("div", {},
    h("p", {}, "Vas a proponer que tu grupo se postule a:"),
    h("p", {}, h("b", {}, r.nombre)),
    h("p", { class: "sub" }, "La postulación es del grupo: todo el grupo la verá en la pestaña Postulaciones. Primero la revisa el director y luego la VRI."),
    titulo, nota), [
    { texto: "Cancelar" },
    { texto: "Enviar solicitud", clase: "primario", accion: async () => {
      await api("/postulaciones", { metodo: "POST", cuerpo: { convocatoria_id: r.id, titulo: titulo.value.trim() || null, nota: nota.value.trim() || null } });
      aviso("Postulación del grupo enviada.");
      render();
    } },
  ]);
}

async function vistaConvocatorias() {
  mostrar("convocatorias", h("h2", {}, "Convocatorias"),
    h("p", { class: "cargando" }, "Calculando la compatibilidad… la primera vez puede tardar unos segundos."));
  if (!estado.ranking) estado.ranking = await api("/convocatorias/buscar", { metodo: "POST" });
  const posts = await api("/postulaciones");
  const yaPostulado = new Map(posts.map((p) => [String(p.convocatoria_id),
    { fase: p.fase, por: p.estudiante, mia: p.usuario_id === estado.yo.id }]));
  const sinPerfil = !Object.keys(estado.yo.perfil || {}).length;
  const elegibles = estado.ranking.filter((r) => r.elegible);
  const no = estado.ranking.filter((r) => !r.elegible);
  mostrar("convocatorias",
    h("h2", {}, "Convocatorias"),
    h("p", { class: "sub" }, `${elegibles.length} convocatoria(s) disponibles, de mayor a menor compatibilidad con tu perfil.`),
    sinPerfil ? h("div", { class: "aviso-caja" }, "Aún no completas tu perfil, así que no se puede calcular el porcentaje. Se muestran todas, "
      + "las que cierran primero arriba. ",
      h("button", { class: "btn chico", onclick: () => { estado.pestana = "perfil"; render(); } }, "Completar mi perfil")) : null,
    elegibles.length ? elegibles.map((r) => tarjetaConvocatoria(r, yaPostulado.get(r.id) || null))
      : h("div", { class: "vacio" }, "Por ahora ninguna convocatoria encaja con tu perfil. Prueba ajustando tu perfil o vuelve más tarde."),
    no.length ? h("details", {}, h("summary", {}, `Convocatorias que no te aplican (${no.length})`),
      h("ul", { class: "simple" }, no.map((r) => h("li", {}, h("b", {}, r.nombre + ": "), r.motivos_exclusion.join(" "))))) : null,
    h("div", { class: "fila" }, h("button", { class: "btn", onclick: () => { estado.ranking = null; render(); } }, "Actualizar resultados")));
}

function modalEliminarPostulacion(f, comoAdmin) {
  modal(comoAdmin ? "Eliminar postulación" : "Cancelar postulación", h("div", {},
    h("p", {}, h("b", {}, f.convocatoria)),
    h("p", {}, "Iniciada por ", h("b", {}, f.estudiante), f.grupo ? " · " + f.grupo : ""),
    h("p", { class: "sub" }, comoAdmin
      ? "Se borra la postulación y su lista de requisitos, aunque ya esté aprobada. Se avisa por correo a quien la inició. No se puede deshacer."
      : "Se borra la postulación de tu grupo y su lista de requisitos. Después puedes volver a postularte si la convocatoria sigue abierta. No se puede deshacer.")), [
    { texto: "Volver" },
    { texto: comoAdmin ? "Eliminar" : "Cancelar postulación", clase: "peligro", accion: async () => {
      await api(comoAdmin ? `/admin/postulaciones/${f.id}` : `/postulaciones/${f.id}`, { metodo: "DELETE" });
      aviso(comoAdmin ? "Postulación eliminada." : "Postulación cancelada.");
      render();
    } },
  ]);
}

function modalReenviar(f) {
  const rechazada = f.fase === "rechazada";
  const nota = h("textarea", { maxlength: "1000", placeholder: rechazada
    ? "Explica qué corregiste o aclaras respecto al motivo del rechazo." : "Corrige o aclara lo que pidió tu director." });
  nota.value = f.nota || "";
  modal("Corregir y reenviar", h("div", {},
    h("p", {}, h("b", {}, f.convocatoria)),
    h("p", {}, rechazada ? "Motivo del rechazo de la VRI:" : "Observación de tu director:"),
    h("p", { class: "sub" }, (rechazada ? f.comentario_vri : f.comentario_director) || "—"),
    rechazada ? h("p", { class: "sub" }, "Antes de reenviar, revisa y corrige tu lista de requisitos. Volverá a pasar por el mismo proceso: tu director y luego la VRI.") : null,
    nota), [
    { texto: "Cancelar" },
    { texto: "Reenviar", clase: "primario", accion: async () => {
      await api(`/postulaciones/${f.id}/reenviar`, { metodo: "POST", cuerpo: { nota: nota.value.trim() || null } });
      aviso(rechazada ? "Postulación reenviada: vuelve a empezar su recorrido." : "Postulación reenviada a tu director.");
      render();
    } },
  ].filter((b) => b !== null));
}

async function vistaMisPostulaciones() {
  const filas = await api("/postulaciones");
  mostrar("postulaciones", h("h2", {}, "Postulaciones de mi grupo"),
    h("p", { class: "sub" }, "Son del grupo: todos los integrantes las ven. Recorrido: director del grupo → VRI."),
    filas.length
      ? tabla(["Convocatoria", "Iniciada por", "Cierra", "Solicitada", "Estado", "Avales", "Respuesta", "Acciones"],
          filas.map((f) => [h("td", {}, f.convocatoria, f.titulo ? h("div", { class: "sub" }, f.titulo) : null),
            f.estudiante, fechaTxt(f.fecha_limite), fechaHora(f.creada),
            h("td", {}, insigniaFase(f.fase)),
            h("td", {}, resumenAvales(f.avales)),
            f.fase === "devuelta" ? (f.comentario_director || "—") : (f.comentario_vri || f.comentario_director || "—"),
            tdAcciones(
              h("button", { class: "btn chico", onclick: () => modalRequisitos(f.id) }, "Ver requisitos"),
              (f.fase === "devuelta" || f.fase === "rechazada")
                ? h("button", { class: "btn chico primario", onclick: () => modalReenviar(f) }, "Corregir y reenviar") : null,
              (f.fase !== "aprobada" && (estado.yo.es_director_grupo || f.usuario_id === estado.yo.id))
                ? h("button", { class: "btn chico peligro", onclick: () => modalEliminarPostulacion(f, false) }, "Cancelar postulación") : null)]))
      : h("div", { class: "vacio" }, "Tu grupo aún no tiene postulaciones. Propón una desde la pestaña Convocatorias."));
}

function textoFormateado(texto) {
  const enLinea = (t) => t.split(/(\*\*[^*]+\*\*)/).filter(Boolean).map((p) =>
    /^\*\*[^*]+\*\*$/.test(p) ? h("b", {}, p.slice(2, -2)) : p.replace(/\*/g, ""));
  const salida = [];
  let lista = null;
  for (const crudo of String(texto || "").split("\n")) {
    const l = crudo.trim();
    if (!l || /^[-*_]{3,}$/.test(l) || /^\|?[\s:|-]+\|?$/.test(l)) { lista = null; continue; }
    const item = l.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item) {
      if (!lista) { lista = h("ul", { class: "chat-lista" }); salida.push(lista); }
      lista.append(h("li", {}, ...enLinea(item[1])));
      continue;
    }
    lista = null;
    const titulo = l.match(/^#{1,6}\s+(.*)$/);
    salida.push(h("p", {}, ...(titulo ? [h("b", {}, titulo[1].replace(/\*\*/g, ""))] : enLinea(l.replace(/^\|/, "").replace(/\|$/, "").replace(/\s*\|\s*/g, " · ")))));
  }
  return salida;
}

function saludoChat() {
  if (estado.yo && estado.yo.rol === "admin") {
    return { rol: "asistente", texto: "¡Hola! Soy FOCU. Puedo ayudarte a consultar la plataforma: grupos, personas, " +
      "postulaciones y convocatorias. Por ejemplo: «¿cómo vamos?», «postulaciones que esperan a la VRI» o «grupos sin director»." };
  }
  return { rol: "asistente", texto: "¡Hola! Soy FOCU. Cuéntame qué buscas: un proyecto o idea que quieras financiar, " +
    "una beca o apoyo para estudiar, o cualquier otra oportunidad. Con eso te ayudo a encontrar convocatorias." };
}

function fechaCorta(txt) {
  const d = new Date(String(txt).replace(" ", "T") + "Z");
  if (isNaN(d)) return "";
  return d.toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

async function vistaAsistente() {
  if (!estado.chat) estado.chat = [saludoChat()];
  let historial = [], dias = 4;
  try { const r = await api("/chat/conversaciones"); historial = r.conversaciones || []; dias = r.dias || 4; }
  catch (e) {}

  const lista = h("div", { class: "mensajes" });
  const pintar = () => {
    lista.replaceChildren(...estado.chat.map((m) => h("div",
      { class: "burbuja " + m.rol + (m.pensando ? " pensando" : "") },
      m.rol === "asistente" && !m.pensando ? textoFormateado(m.texto) : m.texto)));
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
    let nueva = false;
    try {
      const r = await api("/chat", { metodo: "POST", cuerpo: { mensaje: texto, conversacion_id: estado.convId || null } });
      espera.texto = r.respuesta || "(sin respuesta)";
      estado.yo.perfil = r.perfil || estado.yo.perfil;
      estado.ranking = null;
      if (r.conversacion_id && r.conversacion_id !== estado.convId) { estado.convId = r.conversacion_id; nueva = true; }
    } catch (e) { espera.texto = "⚠️ " + e.message; }
    espera.pensando = false;
    enviar.disabled = false;
    if (nueva) { vistaAsistente(); return; }   
    pintar();
  } }, entrada, enviar);

  const abrir = async (c) => {
    try {
      const r = await api(`/chat/conversaciones/${c.id}`);
      estado.convId = c.id;
      estado.chat = [saludoChat(), ...(r.mensajes || [])];
    } catch (e) { aviso(e.message, "error"); estado.convId = null; estado.chat = [saludoChat()]; }
    vistaAsistente();
  };
  const nuevaConversacion = () => { estado.convId = null; estado.chat = [saludoChat()]; vistaAsistente(); };
  const borrar = (c) => modal("Borrar conversación", h("p", {}, `¿Borrar «${c.titulo}»? No se puede recuperar.`), [
    { texto: "Cancelar" },
    { texto: "Borrar", clase: "peligro", accion: async () => {
      await api(`/chat/conversaciones/${c.id}`, { metodo: "DELETE" });
      if (estado.convId === c.id) { estado.convId = null; estado.chat = [saludoChat()]; }
      vistaAsistente();
    } }]);

  const items = historial.map((c) => h("div", { class: "hist-item" + (c.id === estado.convId ? " activa" : "") },
    h("button", { class: "hist-abrir", type: "button", onclick: () => abrir(c), title: "Abrir: " + c.titulo },
      h("span", { class: "hist-icono", "aria-hidden": "true" }, (c.titulo || "?").charAt(0).toUpperCase()),
      h("span", { class: "hist-texto" },
        h("span", { class: "hist-titulo" }, c.titulo), h("span", { class: "hist-fecha" }, fechaCorta(c.actualizado)))),
    h("button", { class: "hist-borrar", type: "button", title: "Borrar esta conversación",
      "aria-label": "Borrar conversación", onclick: () => borrar(c) }, "×")));

  const lateral = h("aside", { class: "hist" },
    h("h3", {}, "Historial de chats"),
    items.length ? h("div", { class: "hist-lista" }, items) : h("p", { class: "sub" }, "Aún no tienes conversaciones guardadas."),
    h("p", { class: "sub hist-nota" }, `Las conversaciones se borran solas a los ${dias} días sin usarse.`));

  mostrar("asistente", h("h2", {}, "Asistente FOCU"),
    h("p", { class: "sub" }, estado.yo.rol === "admin"
      ? "Consulta lo que hay en la plataforma: grupos, personas, postulaciones y convocatorias. Solo lee datos: no cambia nada."
      : "Pregúntame lo que quieras sobre tus opciones de financiación. No escribas datos sensibles."),
    h("div", { class: "chat-layout" }, lateral,
      h("div", { class: "chat-col" }, h("div", { class: "chat" }, lista, form),
        h("div", { class: "chat-pie" },
          h("button", { class: "btn primario nueva-conv", type: "button", onclick: nuevaConversacion }, "+ Nueva conversación")))));
  pintar();
}

function iconoChat() {
  const d = new DOMParser().parseFromString(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="30" height="30" fill="#fff" aria-hidden="true">' +
    '<path d="M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-5 4v-4H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>' +
    '<circle cx="8" cy="10.5" r="1.3" fill="#011149"/><circle cx="12" cy="10.5" r="1.3" fill="#011149"/>' +
    '<circle cx="16" cy="10.5" r="1.3" fill="#011149"/></svg>', "image/svg+xml");
  return document.importNode(d.documentElement, true);
}

function pintarMensajesBurbuja() {
  const lista = document.getElementById("burbuja-lista");
  if (!lista) return;
  lista.replaceChildren(...(estado.chat || [saludoChat()]).map((m) => h("div",
    { class: "burbuja " + m.rol + (m.pensando ? " pensando" : "") },
    m.rol === "asistente" && !m.pensando ? textoFormateado(m.texto) : m.texto)));
  lista.scrollTop = lista.scrollHeight;
}

function pintarBurbuja() {
  const previa = document.getElementById("burbuja-chat");
  const visible = !!estado.token && !!estado.yo && (estado.yo.rol === "admin" || !!estado.yo.consentimiento)
    && estado.pestana !== "asistente";
  if (previa) previa.remove();
  if (!visible) return;
  if (!estado.chat) estado.chat = [saludoChat()];

  const cerrar = () => { estado.burbujaAbierta = false; pintarBurbuja(); };
  const entrada = h("input", { type: "text", placeholder: "Escribe tu mensaje…", maxlength: "2000",
    autocomplete: "off", "aria-label": "Mensaje para el asistente" });
  const enviar = h("button", { class: "btn primario", type: "submit" }, "Enviar");
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const texto = entrada.value.trim();
    if (!texto) return;
    entrada.value = "";
    estado.chat.push({ rol: "usuario", texto });
    const espera = { rol: "asistente", texto: "Pensando…", pensando: true };
    estado.chat.push(espera);
    pintarMensajesBurbuja();
    enviar.disabled = true;
    try {
      const r = await api("/chat", { metodo: "POST", cuerpo: { mensaje: texto, conversacion_id: estado.convId || null } });
      espera.texto = r.respuesta || "(sin respuesta)";
      estado.yo.perfil = r.perfil || estado.yo.perfil;
      estado.ranking = null;
      if (r.conversacion_id) estado.convId = r.conversacion_id;
    } catch (e) { espera.texto = "⚠️ " + e.message; }
    espera.pensando = false;
    enviar.disabled = false;
    pintarMensajesBurbuja();
  } }, entrada, enviar);

  const panel = h("section", { class: "burbuja-panel", role: "dialog", "aria-label": "Asistente FOCU" },
    h("div", { class: "burbuja-cab" },
      h("b", {}, "Asistente FOCU"),
      h("span", { class: "burbuja-acciones" },
        h("button", { type: "button", title: "Nueva conversación", "aria-label": "Nueva conversación",
          onclick: () => { estado.convId = null; estado.chat = [saludoChat()]; pintarBurbuja(); } }, "＋"),
        h("button", { type: "button", title: "Abrir en pantalla completa (con historial)", "aria-label": "Abrir en pantalla completa",
          onclick: () => { estado.burbujaAbierta = false; estado.pestana = "asistente"; detenerSondeo(); render(); } }, "⤢"),
        h("button", { type: "button", title: "Cerrar", "aria-label": "Cerrar el chat", onclick: cerrar }, "×"))),
    h("div", { class: "mensajes", id: "burbuja-lista" }),
    h("p", { class: "burbuja-nota" }, "No escribas datos sensibles."),
    form);
  const fab = h("button", { class: "burbuja-fab", type: "button", title: "Habla con el asistente FOCU",
    "aria-label": "Abrir el chat con el asistente FOCU",
    onclick: () => { estado.burbujaAbierta = true; pintarBurbuja(); } }, iconoChat());

  const caja = h("div", { id: "burbuja-chat", class: "burbuja-chat" }, estado.burbujaAbierta ? panel : fab);
  document.body.append(caja);
  if (estado.burbujaAbierta) { pintarMensajesBurbuja(); setTimeout(() => entrada.focus(), 30); }
}

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
      ? tabla(["Estudiante", "Semillero / grupo", "Convocatoria", "Cierra", "Nota", "Estado", "Avales", "Acciones"],
          filas.map((f) => [f.estudiante, [f.semillero, f.grupo].filter(Boolean).join(" / ") || "—",
            f.convocatoria, fechaTxt(f.fecha_limite), f.nota || "—",
            h("td", {}, insigniaEstado(f.estado), f.comentario_vri ? h("div", { class: "sub" }, f.comentario_vri) : null),
            h("td", {}, resumenAvales(f.avales)),
            tdAcciones(
              h("button", { class: "btn chico", onclick: () => modalRequisitos(f.id) }, "Ver lista"),
              h("button", { class: "btn chico primario", onclick: () => modalDecision(f, "aprobada") }, "Aprobar"),
              h("button", { class: "btn chico peligro", onclick: () => modalDecision(f, "rechazada") }, "Rechazar"),
              h("button", { class: "btn chico", onclick: () => modalEliminarPostulacion(f, true) }, "Eliminar"))]))
      : h("div", { class: "vacio" }, "No hay postulaciones en esta lista."));
}

function modalDecidirSolicitud(sol, aprobar, base, pestana) {
  const comentario = h("textarea", { maxlength: "1000", placeholder: "Opcional: comentario para la persona." });
  modal(aprobar ? "Aprobar solicitud" : "Rechazar solicitud", h("div", {},
    h("p", {}, h("b", {}, sol.nombre), " (", sol.correo, ")"),
    h("p", {}, "Grupo: ", sol.grupo, sol.semillero ? " · " + sol.semillero : ""), comentario), [
    { texto: "Cancelar" },
    { texto: aprobar ? "Aprobar" : "Rechazar", clase: aprobar ? "primario" : "peligro", accion: async () => {
      await api(`${base}/${sol.id}/decidir`, { metodo: "POST",
        cuerpo: { aprobar, comentario: comentario.value.trim() || null } });
      aviso(aprobar ? "Aprobada: ya puede ingresar." : "Solicitud rechazada.");
      render();
    } },
  ]);
}

function tablaSolicitudes(filas, base, pestana, conGrupo) {
  if (!filas.length) return h("div", { class: "vacio" }, "No hay solicitudes en esta lista.");
  return tabla(["Nombre", "Correo", ...(conGrupo ? ["Grupo"] : []), "Semillero", "Fecha", "Estado", "Acciones"],
    filas.map((f) => [f.nombre, f.correo, ...(conGrupo ? [f.grupo + (f.sin_director ? " (sin director)" : "")] : []),
      f.semillero || "—", fechaHora(f.creada), insigniaEstado(f.estado),
      f.estado === "pendiente" ? tdAcciones(
        h("button", { class: "btn chico primario", onclick: () => modalDecidirSolicitud(f, true, base, pestana) }, "Aprobar"),
        h("button", { class: "btn chico peligro", onclick: () => modalDecidirSolicitud(f, false, base, pestana) }, "Rechazar"))
        : (f.comentario || "—")]));
}

function filtroSolicitudes() {
  return h("select", { value: estado.filtroSol, onchange: (ev) => { estado.filtroSol = ev.target.value; render(); } },
    h("option", { value: "pendiente" }, "Pendientes"), h("option", { value: "aprobada" }, "Aprobadas"),
    h("option", { value: "rechazada" }, "Rechazadas"));
}

async function vistaGrupoSolicitudes() {
  estado.filtroSol = estado.filtroSol || "pendiente";
  const filas = await api("/grupo/solicitudes?estado=" + estado.filtroSol);
  mostrar("g_sol", h("h2", {}, "Solicitudes de mi grupo"),
    h("p", { class: "sub" }, "Personas que pidieron unirse a tu grupo. Solo tú (y la VRI) las ven. Al aprobar, la persona puede ingresar con su correo."),
    h("div", { class: "fila" }, h("label", { class: "campo" }, h("span", {}, "Mostrar"), filtroSolicitudes())),
    tablaSolicitudes(filas, "/grupo/solicitudes", "g_sol", false));
}

function modalDecidirPostulacion(f, aprobar) {
  const comentario = h("textarea", { maxlength: "1000",
    placeholder: aprobar ? "Opcional: comentario para la persona." : "Obligatorio: dile qué debe corregir." });
  modal(aprobar ? "Aprobar postulación" : "Devolver postulación", h("div", {},
    h("p", {}, h("b", {}, f.estudiante)),
    h("p", {}, f.convocatoria),
    aprobar ? h("p", { class: "sub" }, "Al aprobarla, pasa a la VRI.") : null,
    comentario), [
    { texto: "Cancelar" },
    { texto: aprobar ? "Aprobar" : "Devolver", clase: aprobar ? "primario" : "peligro", accion: async () => {
      if (!aprobar && !comentario.value.trim()) { aviso("Escribe una observación para devolverla.", "error"); return false; }
      await api(`/grupo/postulaciones/${f.id}/decidir`, { metodo: "POST",
        cuerpo: { aprobar, comentario: comentario.value.trim() || null } });
      aviso(aprobar ? "Aprobada: ahora la revisa la VRI." : "Devuelta con tu observación.");
      render();
    } },
  ]);
}

async function vistaGrupoPostulaciones() {
  estado.filtroPostGrupo = estado.filtroPostGrupo || "pendiente";
  const filas = await api("/grupo/postulaciones?etapa=" + estado.filtroPostGrupo);
  const filtro = h("select", { value: estado.filtroPostGrupo, onchange: (ev) => { estado.filtroPostGrupo = ev.target.value; render(); } },
    h("option", { value: "pendiente" }, "Por revisar"), h("option", { value: "devuelta" }, "Devueltas"),
    h("option", { value: "aprobada" }, "Aprobadas por mí"));
  mostrar("g_post", h("h2", {}, "Revisar postulaciones"),
    h("p", { class: "sub" }, "Las personas de tu grupo proponen postular al grupo a una convocatoria. Tú las revisas primero; las que apruebas pasan a la VRI."),
    h("div", { class: "fila" }, h("label", { class: "campo" }, h("span", {}, "Mostrar"), filtro)),
    filas.length
      ? tabla(["Integrante", "Convocatoria", "Cierra", "Nota", "Estado", "Avales", "Acciones"],
          filas.map((f) => [f.estudiante, h("td", {}, f.convocatoria, f.titulo ? h("div", { class: "sub" }, f.titulo) : null), fechaTxt(f.fecha_limite), f.nota || "—",
            h("td", {}, insigniaFase(f.fase), f.comentario_director ? h("div", { class: "sub" }, f.comentario_director) : null),
            h("td", {}, resumenAvales(f.avales)),
            tdAcciones(
              h("button", { class: "btn chico", onclick: () => modalRequisitos(f.id) }, "Ver lista"),
              ...(f.etapa_grupo === "pendiente" ? [
                h("button", { class: "btn chico primario", onclick: () => modalDecidirPostulacion(f, true) }, "Aprobar"),
                h("button", { class: "btn chico peligro", onclick: () => modalDecidirPostulacion(f, false) }, "Devolver")] : []))]))
      : h("div", { class: "vacio" }, "No hay postulaciones en esta lista."));
}

async function vistaGrupoMiembros() {
  const { grupo, miembros } = await api("/grupo/miembros");
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const formEl = ev.target;
    const fd = Object.fromEntries(new FormData(ev.target).entries());
    await conBoton(ev.target.querySelector("button[type=submit]"), async () => {
      const r = await api("/grupo/miembros", { metodo: "POST", cuerpo: {
        nombre: fd.nombre.trim(), correo: fd.correo.trim(), facultad: fd.facultad,
        semillero: fd.semillero.trim(), descripcion: fd.descripcion.trim() || null } });
      aviso(`${r.nombre} quedó agregado/a a tu grupo.`);
      formEl.reset();
      render();
    });
  } },
  h("div", { class: "rejilla" },
    h("label", { class: "campo" }, h("span", {}, "Nombre completo *"),
      h("input", { type: "text", name: "nombre", required: true, minlength: "2", maxlength: "120" })),
    h("label", { class: "campo" }, h("span", {}, "Correo *"),
      h("input", { type: "email", name: "correo", required: true, maxlength: "120" })),
    h("label", { class: "campo" }, h("span", {}, "Facultad *"), selectFacultad("", "facultad")),
    h("label", { class: "campo" }, h("span", {}, "Semillero (opcional)"),
      h("input", { type: "text", name: "semillero", maxlength: "120" })),
    h("label", { class: "campo" }, h("span", {}, "Tema de trabajo, una frase (opcional)"),
      h("input", { type: "text", name: "descripcion", maxlength: "2000" }))),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Agregar a mi grupo")));

  mostrar("g_mie", h("h2", {}, grupo.nombre),
    grupo.descripcion ? h("p", {}, grupo.descripcion) : null,
    h("div", { class: "tarjeta" }, h("h3", {}, "Agregar a alguien manualmente"), form),
    h("h3", {}, `Integrantes (${miembros.length})`),
    tabla(["Nombre", "Correo", "Cargo", "Facultad", "Semillero", "Acceso", "Acciones"], miembros.map((m) => [
      m.nombre, m.correo || "—",
      h("td", {}, h("span", { class: "insignia " + (m.es_director_grupo ? "alerta" : "ok") }, m.es_director_grupo ? "Director/a" : "Estudiante")),
      m.facultad || "—", /^general \(sin semillero\)$/i.test(m.semillero || "") ? "—" : (m.semillero || "—"),
      h("td", {}, m.activo ? h("span", { class: "insignia ok" }, "Activo") : h("span", { class: "insignia mal" }, "Desactivado")),
      m.es_director_grupo ? h("td", {}, "") : tdAcciones(
        h("button", { class: "btn chico", onclick: (ev) => conBoton(ev.currentTarget, async () => {
          await api(`/grupo/miembros/${m.id}`, { metodo: "PATCH", cuerpo: { activo: !m.activo } });
          aviso(m.activo ? "Acceso desactivado." : "Acceso activado.");
          render();
        }) }, m.activo ? "Desactivar" : "Activar"),
        h("button", { class: "btn chico peligro", onclick: () => modal("Eliminar integrante", h("div", {},
          h("p", {}, h("b", {}, m.nombre), m.correo ? " · " + m.correo : ""),
          h("p", { class: "sub" }, "Se borra su registro de tu grupo. Solo se puede si no ha iniciado postulaciones; si las tiene, desactiva su acceso. No se puede deshacer.")), [
          { texto: "Cancelar" },
          { texto: "Eliminar", clase: "peligro", accion: async () => {
            await api(`/grupo/miembros/${m.id}`, { metodo: "DELETE" });
            aviso("Integrante eliminado.");
            render();
          } },
        ]) }, "Eliminar"))])));
}

async function vistaAdminSolicitudes() {
  estado.filtroSol = estado.filtroSol || "pendiente";
  const filas = await api("/admin/solicitudes?estado=" + estado.filtroSol);
  mostrar("a_sol", h("h2", {}, "Solicitudes de registro"),
    h("p", { class: "sub" }, "Normalmente las responde el director de cada grupo. Tú las ves todas y puedes responder las de grupos sin director."),
    h("div", { class: "fila" }, h("label", { class: "campo" }, h("span", {}, "Mostrar"), filtroSolicitudes())),
    tablaSolicitudes(filas, "/admin/solicitudes", "a_sol", true));
}

function modalEditarGrupo(g) {
  const c = {
    nombre: h("input", { type: "text", value: g.nombre || "", maxlength: "160", required: true }),
    descripcion: h("textarea", { maxlength: "2000", style: "min-height:90px" }, g.descripcion || ""),
    facultad: selectFacultad(g.facultad || ""),
    director: h("input", { type: "text", value: g.director || "", maxlength: "120", required: true }),
    correo_director: h("input", { type: "email", value: g.director_correo || "", maxlength: "120", required: true }),
  };
  const et = (t, x) => h("label", { class: "campo" }, h("span", {}, t), x);
  modal("Editar " + g.nombre, h("div", { class: "rejilla" },
    et("Nombre del grupo", c.nombre), et("Descripción del grupo", c.descripcion), et("Facultad", c.facultad),
    et("Director/a (nombre)", c.director), et("Correo del director/a", c.correo_director)), [
    { texto: "Cancelar" },
    { texto: "Guardar", clase: "primario", accion: async () => {
      const cambios = {};
      if (c.nombre.value.trim().length < 2) { aviso("El nombre del grupo es obligatorio.", "error"); return false; }
      if (!c.facultad.value || !c.director.value.trim() || !c.correo_director.value.trim()) {
        aviso("Facultad, director/a y correo del director/a son obligatorios.", "error"); return false;
      }
      const previo = { nombre: g.nombre || "", descripcion: g.descripcion || "", facultad: g.facultad || "",
        director: g.director || "", correo_director: g.director_correo || "" };
      for (const k of Object.keys(c)) if (c[k].value.trim() !== previo[k]) cambios[k] = c[k].value.trim();
      if (cambios.director !== undefined || cambios.correo_director !== undefined) {
        cambios.director = c.director.value.trim(); cambios.correo_director = c.correo_director.value.trim();
      }
      if (!Object.keys(cambios).length) { aviso("No hiciste ningún cambio."); return false; }
      await api(`/admin/grupos/${g.id}`, { metodo: "PATCH", cuerpo: cambios });
      aviso("Grupo actualizado.");
      render();
    } },
  ]);
}

function modalEliminarGrupo(g) {
  modal("Eliminar grupo", h("div", {},
    h("p", {}, h("b", {}, g.nombre)),
    h("p", { class: "sub" }, "Se borra el grupo, su director y sus solicitudes. Solo se puede si no tiene integrantes ni postulaciones. No se puede deshacer.")), [
    { texto: "Cancelar" },
    { texto: "Eliminar", clase: "peligro", accion: async () => {
      await api(`/admin/grupos/${g.id}`, { metodo: "DELETE" });
      aviso("Grupo eliminado.");
      render();
    } },
  ]);
}

const FACULTADES = [
  "Facultad de Artes",
  "Facultad de Ciencias Agrarias",
  "Facultad de Ciencias Contables, Económicas y Administrativas",
  "Facultad de Ciencias de la Salud",
  "Facultad de Ciencias Humanas y Sociales",
  "Facultad de Ciencias Naturales, Exactas y de la Educación",
  "Facultad de Derecho, Ciencias Políticas y Sociales",
  "Facultad de Ingeniería Civil",
  "Facultad de Ingeniería Electrónica y Telecomunicaciones",
];

function selectFacultad(actual, nombre) {
  const extra = actual && !FACULTADES.includes(actual) ? [actual] : [];
  const attrs = { required: true, "aria-label": "Facultad" };
  if (nombre) attrs.name = nombre;
  return h("select", attrs,
    h("option", { value: "" }, "Elige la facultad…"),
    [...FACULTADES, ...extra].map((f) => h("option", { value: f, selected: f === actual }, f)));
}

async function vistaAdminGrupos() {
  const grupos = await api("/admin/grupos");
  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const formEl = ev.target;
    const fd = Object.fromEntries(new FormData(ev.target).entries());
    await conBoton(ev.target.querySelector("button[type=submit]"), async () => {
      await api("/admin/grupos", { metodo: "POST", cuerpo: {
        nombre: fd.nombre.trim(), descripcion: fd.descripcion.trim(), facultad: fd.facultad.trim(),
        director: fd.director.trim(), correo_director: fd.correo_director.trim() } });
      aviso("Grupo guardado.");
      formEl.reset();   
      render();
    });
  } },
  h("div", { class: "rejilla" },
    h("label", { class: "campo" }, h("span", {}, "Nombre del grupo"),
      h("input", { type: "text", name: "nombre", required: true, minlength: "2", maxlength: "160" })),
    h("label", { class: "campo" }, h("span", {}, "Facultad"), selectFacultad("", "facultad")),
    h("label", { class: "campo" }, h("span", {}, "Director/a"), h("input", { type: "text", name: "director", required: true, minlength: "2", maxlength: "120" })),
    h("label", { class: "campo" }, h("span", {}, "Correo del director/a"), h("input", { type: "email", name: "correo_director", required: true, maxlength: "120" })),
    h("label", { class: "campo" }, h("span", {}, "Descripción del grupo"), h("textarea", { name: "descripcion", maxlength: "2000", style: "min-height:70px" }))),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Guardar grupo")));

  mostrar("a_gru", h("h2", {}, "Grupos de investigación"),
    h("p", { class: "sub" }, "La base guarda cada grupo, su descripción y su director. Quien quiera registrarse elige un grupo y su solicitud llega solo a ese director."),
    h("div", { class: "tarjeta" }, h("h3", {}, "Agregar o completar un grupo"), form),
    h("h3", {}, `Grupos (${grupos.length})`),
    tabla(["Grupo", "Director/a", "Correo del director", "Integrantes", "Acciones"], grupos.map((g) => [
      g.nombre, g.director || "—", g.director_correo || h("td", {}, h("span", { class: "insignia mal" }, "Falta correo")),
      String(g.miembros), tdAcciones(
        h("button", { class: "btn chico", onclick: () => modalEditarGrupo(g) }, "Editar"),
        h("button", { class: "btn chico peligro", onclick: () => modalEliminarGrupo(g) }, "Eliminar"))])));
}

function modalEditarEstudiante(e) {
  const campos = {
    nombre: h("input", { type: "text", value: e.nombre, maxlength: "120" }),
    correo: h("input", { type: "text", value: e.correo || "", maxlength: "120" }),
    grupo: h("input", { type: "text", value: e.grupo || "", maxlength: "120" }),
    semillero: h("input", { type: "text", value: e.semillero || "", maxlength: "120" }),
  };
  const facultad = selectFacultad(e.facultad || "");
  const cargo = h("select", { "aria-label": "Cargo" },
    h("option", { value: "estudiante", selected: !e.es_director_grupo }, "Estudiante"),
    h("option", { value: "director", selected: !!e.es_director_grupo }, "Director/a de grupo"));
  const etiqueta = (t, c) => h("label", { class: "campo" }, h("span", {}, t), c);
  modal("Editar persona", h("div", { class: "rejilla" },
    etiqueta("Nombre", campos.nombre), etiqueta("Correo", campos.correo),
    etiqueta("Cargo", cargo), etiqueta("Facultad", facultad),
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
      if (facultad.value && facultad.value !== (e.facultad || "")) cambios.facultad = facultad.value;
      if ((cargo.value === "director") !== !!e.es_director_grupo) cambios.es_director = cargo.value === "director";
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
  
  const esGeneral = (n) => /^general \(sin semillero\)$/i.test(String(n).trim());
  const opcionesSem = (lista) => [...new Set(lista.map((s) => s.nombre).filter((n) => !esGeneral(n)))]
    .sort((x, y) => x.localeCompare(y, "es")).map((n) => h("option", { value: n }));
  const dlSem = h("datalist", { id: "dl-semilleros" }, opcionesSem(grupos.flatMap((g) => g.semilleros)));
  const campo = (nombre, etiqueta, extra) => h("label", { class: "campo" }, h("span", {}, etiqueta),
    h("input", { type: "text", name: nombre, maxlength: "120", ...extra }));

  const form = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const formEl = ev.target;
    const fd = Object.fromEntries(new FormData(ev.target).entries());
    const boton = ev.target.querySelector("button[type=submit]");
    await conBoton(boton, async () => {
      const r = await api("/admin/estudiantes", { metodo: "POST", cuerpo: {
        nombre: fd.nombre.trim(), correo: fd.correo.trim(),
        semillero: fd.semillero.trim(), grupo: fd.grupo.trim(),
        facultad: fd.facultad.trim() || null, descripcion: fd.descripcion.trim() || null } });
      aviso(`${r.nombre} quedó registrado.`);
      formEl.reset();
      render();
    });
  } },
  h("div", { class: "rejilla" },
    campo("nombre", "Nombre completo", { required: true, minlength: "2" }),
    campo("correo", "Correo", { type: "email", required: true }),
    campo("grupo", "Grupo de investigación", { required: true, list: "dl-grupos", minlength: "2" }),
    campo("semillero", "Semillero (opcional)", { list: "dl-semilleros" }),
    h("label", { class: "campo" }, h("span", {}, "Facultad"), selectFacultad("", "facultad")),
    campo("descripcion", "Tema de trabajo, una frase (opcional)", { maxlength: "2000" })),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Agregar estudiante")),
  dlGrupos, dlSem);
  
  form.querySelector("[name=grupo]").addEventListener("input", (ev) => {
    const g = grupos.find((x) => x.nombre.toLowerCase() === ev.target.value.trim().toLowerCase());
    dlSem.replaceChildren(...opcionesSem(g ? g.semilleros : grupos.flatMap((x) => x.semilleros)));
  });

  const nDirectores = estudiantes.filter((e) => e.es_director_grupo).length;
  const nEstudiantes = estudiantes.length - nDirectores;
  estado.filtroCargo = estado.filtroCargo || "todos";
  const lista = estudiantes.filter((e) => estado.filtroCargo === "todos"
    || (estado.filtroCargo === "director") === !!e.es_director_grupo);
  const filtroCargo = h("select", { "aria-label": "Mostrar por cargo", onchange: (ev) => { estado.filtroCargo = ev.target.value; render(); } },
    [["todos", "Todos"], ["estudiante", "Solo estudiantes"], ["director", "Solo directores"]]
      .map(([v, t]) => h("option", { value: v, selected: v === estado.filtroCargo }, t)));
  const filas = lista.map((e) => [
    e.nombre, e.correo || "—",
    h("td", {}, h("span", { class: "insignia " + (e.es_director_grupo ? "alerta" : "ok") }, e.es_director_grupo ? "Director/a" : "Estudiante")),
    e.facultad || "—", e.semillero || "—", e.grupo || "—",
    h("td", {}, e.activo ? h("span", { class: "insignia ok" }, "Activo") : h("span", { class: "insignia mal" }, "Desactivado"),
      e.consentimiento ? null : h("div", { class: "sub" }, "Sin aceptar datos")),
    String(e.postulaciones),
    tdAcciones(
      h("button", { class: "btn chico", onclick: () => modalEditarEstudiante(e) }, "Editar"),
      h("button", { class: "btn chico", onclick: (ev) => conBoton(ev.currentTarget, async () => {
        await api(`/admin/estudiantes/${e.id}`, { metodo: "PATCH", cuerpo: { activo: !e.activo } });
        aviso(e.activo ? "Acceso desactivado." : "Acceso activado.");
        render();
      }) }, e.activo ? "Desactivar" : "Activar"),
      h("button", { class: "btn chico peligro", onclick: () => modal("Eliminar persona", h("div", {},
        h("p", {}, h("b", {}, e.nombre), e.correo ? " · " + e.correo : ""),
        h("p", { class: "sub" }, "Se borra su registro y su historial con el asistente. Solo se puede si no ha iniciado postulaciones. No se puede deshacer.")), [
        { texto: "Cancelar" },
        { texto: "Eliminar", clase: "peligro", accion: async () => {
          await api(`/admin/estudiantes/${e.id}`, { metodo: "DELETE" });
          aviso("Persona eliminada.");
          render();
        } },
      ]) }, "Eliminar")),
  ]);

  
  const grupoSel = h("select", { name: "grupo_id", required: true, "aria-label": "Grupo" },
    h("option", { value: "" }, "Elige el grupo…"),
    grupos.map((g) => h("option", { value: String(g.id) }, g.nombre)));
  const formDir = h("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const formEl = ev.target;
    const fd = Object.fromEntries(new FormData(ev.target).entries());
    await conBoton(ev.target.querySelector("button[type=submit]"), async () => {
      const r = await api("/admin/directores", { metodo: "POST", cuerpo: {
        nombre: fd.nombre.trim(), correo: fd.correo.trim(),
        grupo_id: Number(fd.grupo_id), facultad: fd.facultad, semillero: (fd.semillero || "").trim() || null } });
      aviso(`${r.nombre} quedó como director/a de ${r.grupo}.`);
      formEl.reset();
      render();
    });
  } },
  h("div", { class: "rejilla" },
    campo("nombre", "Nombre completo", { required: true, minlength: "2" }),
    campo("correo", "Correo", { type: "email", required: true }),
    h("label", { class: "campo" }, h("span", {}, "Grupo de investigación"), grupoSel),
    campo("semillero", "Semillero (opcional)", { list: "dl-semilleros" }),
    h("label", { class: "campo" }, h("span", {}, "Facultad"), selectFacultad("", "facultad"))),
  h("div", { class: "fila" }, h("button", { class: "btn primario", type: "submit" }, "Agregar director/a")));
  
  grupoSel.addEventListener("change", () => {
    const g = grupos.find((x) => String(x.id) === grupoSel.value);
    dlSem.replaceChildren(...opcionesSem(g ? g.semilleros : grupos.flatMap((x) => x.semilleros)));
  });

  mostrar("a_est", h("h2", {}, "Personas"),
    h("div", { class: "dos-columnas" },
      h("div", { class: "tarjeta" }, h("h3", {}, "Agregar estudiante"), form),
      h("div", { class: "tarjeta" }, h("h3", {}, "Agregar director/a de grupo"), formDir)),
    tarjetaCargaExcel(),
    h("div", { class: "fila", style: "align-items:end; justify-content:space-between" },
      h("h3", {}, `Personas registradas: ${estudiantes.length}`,
        h("span", { class: "sub", style: "font-weight:400; margin-left:.6rem" },
          `${nEstudiantes} estudiante(s) · ${nDirectores} director(es)/a(s)`)),
      h("label", { class: "campo" }, h("span", {}, "Mostrar"), filtroCargo)),
    lista.length
      ? tabla(["Nombre", "Correo", "Cargo", "Facultad", "Semillero", "Grupo", "Acceso", "Postulaciones", "Acciones"], filas)
      : h("div", { class: "vacio" }, estudiantes.length ? "No hay personas con ese cargo."
        : "Aún no hay personas. Agrégalas una por una o sube un archivo en el recuadro de arriba."));
}

async function descargarPlantilla() {
  const r = await fetch(API + "/admin/plantilla?t=" + Date.now(), { cache: "no-store", headers: { Authorization: "Bearer " + estado.token } });
  if (!r.ok) throw new Error("No se pudo descargar la plantilla.");
  descargarBlob(await r.blob(), "plantilla_personas.xlsx");
}

function tarjetaCargaExcel() {
  const resultado = h("div", {});
  const archivo = h("input", { type: "file", accept: ".xlsx" });
  const botonSubir = h("button", { class: "btn primario" }, "Subir y registrar integrantes");
  botonSubir.addEventListener("click", () => conBoton(botonSubir, async () => {
    if (!archivo.files.length) { aviso("Elige primero un archivo.", "error"); return; }
    const fd = new FormData();
    fd.append("archivo", archivo.files[0]);
    const r = await api("/admin/cargar-excel", { metodo: "POST", formData: fd });
    resultado.replaceChildren(
      h("div", { class: "aviso-caja" }, `Se registraron ${r.creados.length} integrantes, se completó el correo de ${(r.actualizados || []).length} y se omitieron ${r.omitidos.length} filas. Cada uno ingresa con su correo y recibe un código temporal.`),
      r.creados.length ? tabla(["Nombre", "Correo", "Cargo", "Semillero", "Grupo"],
        r.creados.map((c) => [c.nombre, c.correo, c.cargo || "Estudiante", c.semillero, c.grupo])) : null,
      r.omitidos.length ? h("details", {}, h("summary", {}, `Filas omitidas (${r.omitidos.length})`),
        h("ul", { class: "simple" }, r.omitidos.map((o) => h("li", {}, `Fila ${o.fila}: ${o.motivo}`)))) : null);
    archivo.value = "";
  }));

  return h("div", {},
    h("div", { class: "tarjeta" }, h("h3", {}, "Subir archivo de personas"),
      h("p", {}, "Sube un archivo con las columnas obligatorias ", h("b", {}, "nombre, correo y grupo"),
        ". En la columna grupo se elige de una lista desplegable con los grupos que existen. El cargo (Estudiante o Director/a) también se elige de una lista; si queda vacío, es Estudiante. El semillero es opcional: si la persona no tiene, queda solo en su grupo."),
      h("ul", { class: "simple" },
        h("li", {}, "Las filas a las que les falte alguna de esas tres casillas se omiten y se te dice cuál falta."),
        h("li", {}, "Los estudiantes repetidos (mismo nombre en el semillero, o mismo correo) se omiten."),
        h("li", {}, "Puedes volver a subir el archivo cuando haya gente nueva.")),
      h("div", { class: "fila" },
        h("button", { class: "btn", onclick: (ev) => conBoton(ev.currentTarget, descargarPlantilla) }, "Descargar plantilla"),
        archivo, botonSubir)),
    resultado);
}

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

function modalConvocatoriaPorEnlace() {
  const url = h("input", { type: "url", maxlength: "500", placeholder: "https://", "aria-label": "Enlace de la convocatoria" });
  modal("Agregar convocatoria por enlace", h("div", {},
    h("p", { class: "sub" }, "Pega la dirección de la página de la convocatoria. La IA la lee y la deja “sin verificar” hasta que le des el visto bueno. Puede tardar unos segundos."),
    h("label", { class: "campo" }, h("span", {}, "Enlace de la convocatoria"), url)), [
    { texto: "Cancelar" },
    { texto: "Leer y agregar", clase: "primario", accion: async () => {
      if (!/^https?:\/\//i.test(url.value.trim())) { aviso("Escribe una dirección completa que empiece por https://", "error"); return false; }
      const r = await api("/admin/convocatorias/desde-url", { metodo: "POST", cuerpo: { url: url.value.trim() } });
      aviso(r.vencida
        ? `Esa convocatoria ya cerró (${String(r.fecha_limite).slice(0, 10)}). Quedó en «Convocatorias vencidas».`
        : `Agregada: ${r.nombre}. Revísala y dale el visto bueno.`);
      estado.ranking = null;
      render();
    } },
  ]);
}

function trozosDeTexto(texto) {
  return String(texto || "")
    .split(/\n+|\s[•·▪]\s|;\s+|(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ0-9])/)
    .map((t) => t.replace(/^\s*(?:[-*•·▪]|\d+[.)])\s*/, "").trim())
    .filter((t) => t.length > 2);
}

function contenidoRequerimientos(c, avisoSinVerificar) {
  const docs = (c.documentos || []).filter((d) => urlSegura(d.url));
  const lista = (c.requisitos_lista || []).length ? c.requisitos_lista : trozosDeTexto(c.requisitos);
  const criterios = c.criterios_evaluacion || [];
  const enlace = urlSegura(c.url);
  const seccion = (titulo, ...contenido) => h("div", { class: "ficha-seccion" }, h("h4", {}, titulo), contenido);
  const hayAlgo = docs.length || lista.length || criterios.length;
  return h("div", { class: "ficha-conv" },
    avisoSinVerificar && !c.verificada ? h("div", { class: "aviso-caja" },
      "Datos extraídos automáticamente de la página oficial. Compáralos con la fuente antes de dar el visto bueno.") : null,
    docs.length ? seccion("Documentos de la convocatoria",
      h("p", { class: "sub" }, "Se abren en otra pestaña (los PDF se ven en el navegador; los Word se descargan)."),
      h("ul", { class: "simple" }, docs.map((d) => h("li", {},
        h("a", { href: urlSegura(d.url), target: "_blank", rel: "noopener noreferrer" }, d.titulo + " ↗"))))) : null,
    lista.length ? seccion("Requisitos", (c.requisitos_lista || []).length ? null
      : h("p", { class: "sub" }, "Texto de la fuente, ordenado en lista."),
      h("ol", { class: "simple" }, lista.map((t) => h("li", {}, t)))) : null,
    criterios.length ? seccion("Criterios de evaluación", h("ul", { class: "simple" }, criterios.map((t) => h("li", {}, t)))) : null,
    hayAlgo ? null : h("div", { class: "vacio" }, "La fuente no publica requisitos ni documentos para esta convocatoria."
      + (enlace ? " Revisa la página oficial." : "")),
    enlace ? seccion("Fuente oficial", h("a", { href: enlace, target: "_blank", rel: "noopener noreferrer" }, "Abrir la página de la convocatoria ↗")) : null);
}

function modalRequerimientos(c) {
  modal("Requerimientos: " + c.nombre, contenidoRequerimientos(c, true), [{ texto: "Cerrar" }]);
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
  const hoy = new Date().toISOString().slice(0, 10);
  const eliminar = (c) => () => modal("Eliminar convocatoria", h("div", {},
    h("p", {}, h("b", {}, c.nombre)),
    h("p", { class: "sub" }, "Se borra del catálogo y no se vuelve a traer sola. No se puede deshacer.")), [
    { texto: "Cancelar" },
    { texto: "Eliminar", clase: "peligro", accion: async () => {
      await api(`/admin/convocatorias/${encodeURIComponent(c.id)}`, { metodo: "DELETE" });
      aviso("Convocatoria eliminada.");
      estado.ranking = null;
      render();
    } },
  ]);
  const esVencida = (c) => !!c.fecha_limite && String(c.fecha_limite).slice(0, 10) < hoy;
  const enDepuracion = (c) => !c.fecha_limite && !c.verificada && !!c.depuracion_desde;
  const vigentes = convs.filter((c) => !esVencida(c) && !enDepuracion(c));
  const aDepurar = convs.filter(enDepuracion);
  const diasParaBorrar = (c) => {
    const pasados = Math.floor((Date.now() - new Date(c.depuracion_desde + "T00:00:00").getTime()) / 86400000);
    return Math.max(0, 14 - pasados);
  };
  const filasDepurar = aDepurar.map((c) => [
    h("td", {}, h("b", {}, c.nombre), h("div", { class: "sub" }, c.entidad || "")),
    fechaTxt(c.fecha_limite),
    c.fuente === "manual" ? "Manual" : (c.fuente === "enlace" ? "Por enlace" : c.fuente),
    h("span", { class: "insignia alerta" }, `Se borra en ${diasParaBorrar(c)} día(s)`),
    tdAcciones(
      h("button", { class: "btn chico", onclick: () => modalRequerimientos(c) }, "Requerimientos"),
      h("button", { class: "btn chico", onclick: cambiar(c, { recuperar: true }, "Convocatoria recuperada: vuelve al catálogo.") }, "Recuperar"),
      h("button", { class: "btn chico peligro", onclick: eliminar(c) }, "Eliminar"),
      urlSegura(c.url) ? h("a", { href: urlSegura(c.url), target: "_blank", rel: "noopener noreferrer" }, "Ver ↗") : null),
  ]);
  const vencidas = convs.filter(esVencida)
    .sort((a, b) => String(b.fecha_limite).localeCompare(String(a.fecha_limite)));   
  const filasVencidas = vencidas.map((c) => [
    h("td", {}, h("b", {}, c.nombre), h("div", { class: "sub" }, c.entidad || "")),
    fechaTxt(c.fecha_limite),
    c.fuente === "manual" ? "Manual" : (c.fuente === "enlace" ? "Por enlace" : c.fuente),
    tdAcciones(
      h("button", { class: "btn chico", onclick: () => modalRequerimientos(c) }, "Requerimientos"),
      h("button", { class: "btn chico peligro", onclick: eliminar(c) }, "Eliminar"),
      urlSegura(c.url) ? h("a", { href: urlSegura(c.url), target: "_blank", rel: "noopener noreferrer" }, "Ver ↗") : null),
  ]);
  const filas = vigentes.map((c) => {
    const vencida = false;
    const motivo = !c.verificada ? "Sin visto bueno" : (!c.activa ? "Oculta" : (vencida ? "Vencida" : null));
    return [
      h("td", {}, h("b", {}, c.nombre), h("div", { class: "sub" }, c.entidad || "")),
      fechaTxt(c.fecha_limite),
      c.fuente === "manual" ? "Manual" : (c.fuente === "enlace" ? "Por enlace" : c.fuente),
      c.verificada ? h("span", { class: "insignia ok" }, "Con visto bueno") : h("span", { class: "insignia alerta" }, "Pendiente de visto bueno"),
      motivo ? h("span", { class: "insignia mal" }, "No · " + motivo.toLowerCase()) : "Sí",
      tdAcciones(
        h("button", { class: "btn chico", onclick: () => modalRequerimientos(c) }, "Requerimientos"),
        c.verificada
          ? h("button", { class: "btn chico", onclick: cambiar(c, { activa: !c.activa }, c.activa ? "Convocatoria oculta." : "Convocatoria visible.") },
              c.activa ? "Ocultar" : "Mostrar")
          : h("button", { class: "btn chico", onclick: cambiar(c, { verificada: true }, "Convocatoria con visto bueno: ya la ven los estudiantes.") }, "Dar visto bueno"),
        h("button", { class: "btn chico peligro", onclick: eliminar(c) }, "Eliminar"),
        urlSegura(c.url) ? h("a", { href: urlSegura(c.url), target: "_blank", rel: "noopener noreferrer" }, "Ver ↗") : null),
    ];
  });

  const activas = scr.fuentes.filter((f) => f.activa && (f.url_listado || (f.urls_directas || []).length)).length;
  const botonScr = h("button", { class: "btn primario", disabled: scr.corriendo,
    onclick: (ev) => conBoton(ev.currentTarget, async () => {
      await api("/admin/scrapers/ejecutar", { metodo: "POST" });
      aviso("Recolección iniciada. Tarda unos minutos.");
      render();
    }) }, scr.corriendo ? "Recolectando…" : "Actualizar desde las fuentes");

  mostrar("a_conv", h("h2", {}, "Convocatorias"),
    h("div", { class: "tarjeta" },
      h("h3", {}, "Recolección automática"),
      h("p", {}, `El sistema lee ${activas} fuente(s) oficial(es)`),
      h("div", { class: "fila" }, botonScr,
        h("button", { class: "btn", onclick: modalNuevaConvocatoria }, "Agregar convocatoria a mano"),
        h("button", { class: "btn", onclick: modalConvocatoriaPorEnlace }, "Agregar por enlace")),
      scr.corriendo ? h("p", { class: "cargando" }, `En curso desde ${scr.inicio}…`) : resumenInforme(scr.informe)),
    h("h3", {}, `Catálogo (${vigentes.length})`),
    vigentes.length ? tabla(["Convocatoria", "Cierra", "Fuente", "Visto bueno", "Visible", "Acciones"], filas)
      : h("div", { class: "vacio" }, "No hay convocatorias."),
    h("h3", {}, `Sin revisar, por depurar (${aDepurar.length})`),
    h("p", { class: "sub" }, "Sin fecha de cierre y más de 2 meses sin visto bueno. Los estudiantes no las ven. Si no las recuperas en 2 semanas, se borran."),
    aDepurar.length ? tabla(["Convocatoria", "Cierra", "Fuente", "Estado", "Acciones"], filasDepurar)
      : h("div", { class: "vacio" }, "No hay convocatorias por depurar."),
    h("h3", {}, `Convocatorias vencidas (${vencidas.length})`),
    h("p", { class: "sub" }, "Ya cerraron: los estudiantes no las ven y no se puede postular. Quedan aquí para consulta de la VRI."),
    vencidas.length ? tabla(["Convocatoria", "Cerró", "Fuente", "Acciones"], filasVencidas)
      : h("div", { class: "vacio" }, "Todavía no hay convocatorias vencidas."));

  detenerSondeo();
  if (scr.corriendo) {
    estado.temporizador = setTimeout(() => { if (estado.pestana === "a_conv") render(); }, 4000);
  }
}

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

render();