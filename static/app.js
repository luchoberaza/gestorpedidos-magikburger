const Magik = (() => {
  // ===== State =====
  let BOOT = null; // {products, ingredients, couriers}
  let board = { new: [], kitchen: [], way: [], done: [] };
  let liquidation = null;

  let current = {
    tmpId: null,
    phone: "",
    address: "",
    courier_id: "",
    payment_method: "cash",
    items: [] // { product_id, qty, removed_ingredient_ids:[], added_ingredient_ids:[] }
  };

  // ===== Edit order (modal) =====
  let edit = {
    orderId: null,
    phone: "",
    address: "",
    courier_id: "",
    payment_method: "cash",
    items: []
  };

  // ===== Helpers =====
  const $ = (id) => document.getElementById(id);
  const byId = (arr) => Object.fromEntries((arr || []).map(x => [x.id, x]));
  const money = (cents) => Math.round((cents || 0) / 100).toString();

  function toast(msg) {
    alert(msg);
  }

  async function fetchJson(url, opts) {
    let r;
    try {
      r = await fetch(url, opts);
    } catch (e) {
      console.error(e);
      return { ok: false, _net: true };
    }
    try {
      const j = await r.json();
      j._http_ok = r.ok;
      j._status = r.status;
      return j;
    } catch (e) {
      console.error(e);
      return { ok: false, _bad_json: true, _status: r.status };
    }
  }

  function setActiveButton(activeId) {
    ["btnViewNew", "btnViewOrders", "btnViewSummary"].forEach(id => {
      const b = $(id);
      if (!b) return;
      b.classList.toggle("active-pill", id === activeId);
    });
  }

  function showView(viewId) {
    ["viewNew", "viewOrders", "viewSummary"].forEach(id => {
      const v = $(id);
      if (!v) return;
      v.style.display = (id === viewId ? "" : "none");
    });
  }

  async function goNew() {
    setActiveButton("btnViewNew");
    showView("viewNew");
  }

  async function goOrders() {
    setActiveButton("btnViewOrders");
    showView("viewOrders");
    await refreshOrders();
  }

  async function goSummary() {
    setActiveButton("btnViewSummary");
    showView("viewSummary");
    await refreshSummary();
  }

  // ===== Loaders =====
  async function loadBootstrap() {
    const j = await fetchJson("/api/bootstrap");
    if (!j || j._net) return toast("No se pudo conectar con el servidor.");
    BOOT = j;
  }

  async function loadBoard() {
    const j = await fetchJson("/api/board");
    if (!j || j._net) return false;
    board = j;
    return true;
  }

  async function loadLiquidation() {
    const j = await fetchJson("/api/liquidation");
    if (!j || j._net) return false;
    liquidation = j;
    return true;
  }

  // ===== UI: couriers =====
  function renderCouriers() {
    const sel = $("courier");
    if (!sel) return;

    sel.innerHTML = "";
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "— Sin asignar";
    sel.appendChild(opt0);

    (BOOT.couriers || []).forEach(c => {
      const o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.name;
      sel.appendChild(o);
    });
  }

  // ===== Payment =====
  function setPayMethod(method) {
    // Normalizamos SIEMPRE a los 2 valores soportados por el backend
    const m = (method || "cash").toString().trim().toLowerCase();
    current.payment_method = (m === "transfer" ? "transfer" : "cash");

    const cash = $("payCash");
    const tr = $("payTransfer");
    // Usamos la versión normalizada como fuente de verdad
    if (cash) cash.classList.toggle("active", current.payment_method === "cash");
    if (tr) tr.classList.toggle("active", current.payment_method === "transfer");
  }

  function setEditPayMethod(method) {
    const m = (method || "cash").toString().trim().toLowerCase();
    edit.payment_method = (m === "transfer" ? "transfer" : "cash");

    const cash = $("editPayCash");
    const tr = $("editPayTransfer");
    if (cash) cash.classList.toggle("active", edit.payment_method === "cash");
    if (tr) tr.classList.toggle("active", edit.payment_method === "transfer");
  }

  function getSelectedEditPayMethod() {
    const cash = $("editPayCash");
    const tr = $("editPayTransfer");

    if (tr && tr.classList.contains("active")) return "transfer";
    if (cash && cash.classList.contains("active")) return "cash";
    return edit.payment_method || "cash";
  }

  // Fuente de verdad para el método de pago al guardar:
  // si el usuario ve el botón "Transferencia" activo, guardamos "transfer" sí o sí.
  function getSelectedPayMethod() {
    const cash = $("payCash");
    const tr = $("payTransfer");

    if (tr && tr.classList.contains("active")) return "transfer";
    if (cash && cash.classList.contains("active")) return "cash";

    return current.payment_method || "cash";
  }

  // ===== Products =====
  function renderProducts() {
    const wrap = $("products");
    if (!wrap) return;

    const q = ($("search")?.value || "").trim().toLowerCase();
    wrap.innerHTML = "";

    (BOOT.products || [])
      .filter(p => !q || (p.name || "").toLowerCase().includes(q))
      .forEach(p => {
        const el = document.createElement("div");
        el.className = "product-tile";
        el.onclick = () => addProduct(p.id);
        el.innerHTML = `
          <p class="product-name">${p.name}</p>
          <p class="product-price">$ ${money(p.price_cents)}</p>
        `;
        wrap.appendChild(el);
      });
  }

  function renderEditCouriers() {
    const sel = $("editCourier");
    if (!sel) return;

    sel.innerHTML = "";
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "— Sin asignar";
    sel.appendChild(opt0);

    (BOOT.couriers || []).forEach(c => {
      const o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.name;
      sel.appendChild(o);
    });
  }

  function renderEditProducts() {
    const wrap = $("editProducts");
    if (!wrap) return;

    const q = ($("editSearch")?.value || "").trim().toLowerCase();
    wrap.innerHTML = "";

    (BOOT.products || [])
      .filter(p => !q || (p.name || "").toLowerCase().includes(q))
      .forEach(p => {
        const el = document.createElement("div");
        el.className = "product-tile";
        el.onclick = () => {
          if (!edit.orderId) return;
          edit.items.push({
            product_id: p.id,
            qty: 1,
            removed_ingredient_ids: [],
            added_ingredient_ids: []
          });
          renderEditItems();
        };
        el.innerHTML = `
          <p class="product-name">${p.name}</p>
          <p class="product-price">$ ${money(p.price_cents)}</p>
        `;
        wrap.appendChild(el);
      });
  }

  // ===== Current order =====
  function ensureTmpId() {
    if (!current.tmpId) current.tmpId = "TMP-" + Math.floor(1000 + Math.random() * 9000);
  }

  function addProduct(productId) {
    ensureTmpId();
    current.items.push({
      product_id: productId,
      qty: 1,
      removed_ingredient_ids: [],
      added_ingredient_ids: []
    });
    renderCurrent();
  }

  function removeItem(idx) {
    current.items.splice(idx, 1);
    renderCurrent();
  }

  function calcItemTotal(item) {
    const prodMap = byId(BOOT.products || []);
    const ingMap = byId(BOOT.ingredients || []);

    const p = prodMap[item.product_id];
    let total = p ? (p.price_cents || 0) : 0;

    (item.added_ingredient_ids || []).forEach(iid => {
      const ing = ingMap[iid];
      if (ing) total += (ing.extra_price_cents || 0);
    });

    const qty = Math.max(1, parseInt(item.qty || 1, 10));
    return total * qty;
  }

  function calcTotal() {
    return (current.items || []).reduce((acc, it) => acc + calcItemTotal(it), 0);
  }

  function renderCurrent() {
    const tmp = $("tmpOrderId");
    const total = $("currentTotal");
    const wrap = $("currentItems");

    if (tmp) tmp.textContent = current.tmpId || "—";
    if (total) total.textContent = money(calcTotal());
    if (!wrap) return;

    wrap.innerHTML = "";

    if (!current.items.length) {
      wrap.innerHTML = `<div class="muted" style="font-size:13px;">Todavía no agregaste productos. Elegí uno a la izquierda</div>`;
      return;
    }

    const prodMap = byId(BOOT.products || []);
    const ingMap = byId(BOOT.ingredients || []);

    current.items.forEach((it, idx) => {
      const p = prodMap[it.product_id];

      const removed = (it.removed_ingredient_ids || []).map(id => ingMap[id]?.name).filter(Boolean);
      const added = (it.added_ingredient_ids || []).map(id => ingMap[id]).filter(Boolean);

      const mods = [];
      removed.forEach(r => mods.push(`<span class="mod remove">Sin ${r}</span>`));
      added.forEach(a => mods.push(`<span class="mod add">+ ${a.name} ($${money(a.extra_price_cents)})</span>`));

      const el = document.createElement("div");
      el.className = "item";
      el.innerHTML = `
        <div class="d-flex align-items-start justify-content-between gap-2">
          <div>
            <h6>${p ? p.name : "Producto"}</h6>
            <div class="muted" style="font-size:12px;">Ítem: $ ${money(calcItemTotal(it))}</div>
          </div>
          <div class="d-flex gap-2">
            <button class="btn-ghost" type="button" data-action="edit">Editar</button>
            <button class="btn-danger-soft" type="button" data-action="del">X</button>
          </div>
        </div>
        <div class="mods">${mods.join("") || `<span class="muted" style="font-size:12px;">Sin modificaciones</span>`}</div>
      `;

      el.querySelector('[data-action="edit"]').onclick = () => editItem(idx);
      el.querySelector('[data-action="del"]').onclick = () => removeItem(idx);

      wrap.appendChild(el);
    });
  }

  // ===== Extras qty helpers =====
  function countAdded(item, ingId) {
    return (item.added_ingredient_ids || []).reduce((acc, x) => acc + (x === ingId ? 1 : 0), 0);
  }
  function addOneAdded(item, ingId) {
    if (!item.added_ingredient_ids) item.added_ingredient_ids = [];
    item.added_ingredient_ids.push(ingId);
  }
  function removeOneAdded(item, ingId) {
    const arr = item.added_ingredient_ids || [];
    const i = arr.findIndex(x => x === ingId);
    if (i >= 0) arr.splice(i, 1);
    item.added_ingredient_ids = arr;
  }

  function bindModalBackdropFixOnce(modalId) {
    const modalEl = document.getElementById(modalId);
    if (!modalEl) return;

    if (modalEl.dataset._mbFixBound === "1") return;
    modalEl.dataset._mbFixBound = "1";

    modalEl.addEventListener("hidden.bs.modal", () => {
      // Solo limpiamos backdrops/scroll cuando NO queda ningún modal abierto.
      // Esto evita romper el caso de "modal sobre modal" (ej: editar ítem dentro de editar pedido).
      setTimeout(() => {
        if (document.querySelectorAll(".modal.show").length > 0) return;
        document.querySelectorAll(".modal-backdrop").forEach(b => b.remove());
        document.body.classList.remove("modal-open");
        document.body.style.removeProperty("overflow");
        document.body.style.removeProperty("padding-right");
      }, 0);
    });
  }

  function openModsForItem(item, onChanged) {
    if (!item) return;

    const prodMap = byId(BOOT.products || []);
    const ingMap = byId(BOOT.ingredients || []);
    const p = prodMap[item.product_id];

    const baseIds = (p?.base_ingredient_ids || []);
    const baseList = baseIds.map(id => ingMap[id]).filter(Boolean);

    const content = $("modsContent");
    if (!content) return;

    content.innerHTML = `
      <div class="col-12">
        <div class="chip"><span class="chip-dot"></span> ${p ? p.name : "Producto"}</div>
        <div class="muted mt-2" style="font-size:12px;">Tildá para quitar ingredientes base. Extras suman al precio.</div>
      </div>

      <div class="col-12 col-lg-6">
        <div class="glass" style="box-shadow:none;">
          <div class="glass-header"><h5>Quitar ingredientes</h5></div>
          <div class="glass-body" id="baseIng"></div>
        </div>
      </div>

      <div class="col-12 col-lg-6">
        <div class="glass" style="box-shadow:none;">
          <div class="glass-header"><h5>Agregar extras</h5></div>
          <div class="glass-body" id="extras"></div>
        </div>
      </div>
    `;

    // Quitar ingredientes
    const baseWrap = document.getElementById("baseIng");
    baseWrap.innerHTML = "";

    if (!baseList.length) {
      baseWrap.innerHTML = `<div class="muted" style="font-size:12px;">Este producto no tiene ingredientes base configurados.</div>`;
    } else {
      baseList.forEach(ing => {
        const checked = (item.removed_ingredient_ids || []).includes(ing.id);
        const row = document.createElement("div");
        row.className = "d-flex align-items-center justify-content-between mb-2";
        row.innerHTML = `
          <div style="font-weight:700;">${ing.name}</div>
          <div class="form-check form-switch m-0">
            <input class="form-check-input" type="checkbox" ${checked ? "checked" : ""}>
          </div>
        `;
        row.querySelector("input").addEventListener("change", (e) => {
          if (e.target.checked) {
            if (!item.removed_ingredient_ids.includes(ing.id)) item.removed_ingredient_ids.push(ing.id);
          } else {
            item.removed_ingredient_ids = item.removed_ingredient_ids.filter(x => x !== ing.id);
          }
          if (typeof onChanged === "function") onChanged();
        });
        baseWrap.appendChild(row);
      });
    }

    // Extras con +/-
    const extraWrap = document.getElementById("extras");
    extraWrap.innerHTML = "";

    (BOOT.ingredients || []).forEach(ing => {
      const qty = countAdded(item, ing.id);

      const row = document.createElement("div");
      row.className = "d-flex align-items-center justify-content-between mb-2";
      row.innerHTML = `
        <div>
          <div style="font-weight:800;">${ing.name}</div>
          <div class="muted" style="font-size:12px;">+$ ${money(ing.extra_price_cents || 0)}</div>
        </div>

        <div class="qtybox">
          <div class="qtybtn" data-action="minus">−</div>
          <div class="qtyval" data-role="val">${qty}</div>
          <div class="qtybtn" data-action="plus">+</div>
        </div>
      `;

      const minus = row.querySelector('[data-action="minus"]');
      const plus = row.querySelector('[data-action="plus"]');
      const val = row.querySelector('[data-role="val"]');

      function syncMinus() {
        if (countAdded(item, ing.id) <= 0) {
          minus.style.opacity = "0.45";
          minus.style.pointerEvents = "none";
        } else {
          minus.style.opacity = "";
          minus.style.pointerEvents = "";
        }
      }

      syncMinus();

      plus.addEventListener("click", () => {
        addOneAdded(item, ing.id);
        val.textContent = countAdded(item, ing.id);
        syncMinus();
        if (typeof onChanged === "function") onChanged();
      });

      minus.addEventListener("click", () => {
        removeOneAdded(item, ing.id);
        val.textContent = countAdded(item, ing.id);
        syncMinus();
        if (typeof onChanged === "function") onChanged();
      });

      extraWrap.appendChild(row);
    });

    bindModalBackdropFixOnce("modsModal");
    const modalEl = document.getElementById("modsModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
  }

  function editItem(idx) {
    const item = current.items[idx];
    openModsForItem(item, renderCurrent);
  }

  function editOrderItem(idx) {
    const item = edit.items[idx];
    openModsForItem(item, renderEditItems);
  }

  function calcEditTotal() {
    return (edit.items || []).reduce((acc, it) => acc + calcItemTotal(it), 0);
  }

  function renderEditItems() {
    const total = $("editTotal");
    const wrap = $("editItems");
    if (total) total.textContent = money(calcEditTotal());
    if (!wrap) return;

    wrap.innerHTML = "";
    if (!edit.items.length) {
      wrap.innerHTML = `<div class="muted" style="font-size:13px;">Pedido vacío.</div>`;
      return;
    }

    const prodMap = byId(BOOT.products || []);
    const ingMap = byId(BOOT.ingredients || []);

    edit.items.forEach((it, idx) => {
      const p = prodMap[it.product_id];

      const removed = (it.removed_ingredient_ids || []).map(id => ingMap[id]?.name).filter(Boolean);
      const added = (it.added_ingredient_ids || []).map(id => ingMap[id]).filter(Boolean);

      const mods = [];
      removed.forEach(r => mods.push(`<span class="mod remove">Sin ${r}</span>`));
      added.forEach(a => mods.push(`<span class="mod add">+ ${a.name} ($${money(a.extra_price_cents)})</span>`));

      const el = document.createElement("div");
      el.className = "item";
      el.innerHTML = `
        <div class="d-flex align-items-start justify-content-between gap-2">
          <div>
            <h6>${p ? p.name : "Producto"}</h6>
            <div class="muted" style="font-size:12px;">Ítem: $ ${money(calcItemTotal(it))}</div>
          </div>
          <div class="d-flex gap-2">
            <button class="btn-ghost" type="button" data-action="edit">Editar</button>
            <button class="btn-danger-soft" type="button" data-action="del">X</button>
          </div>
        </div>
        <div class="mods">${mods.join("") || `<span class="muted" style="font-size:12px;">Sin modificaciones</span>`}</div>
      `;

      el.querySelector('[data-action="edit"]').onclick = () => editOrderItem(idx);
      el.querySelector('[data-action="del"]').onclick = () => {
        edit.items.splice(idx, 1);
        renderEditItems();
      };

      wrap.appendChild(el);
    });
  }

  async function openEditOrder(orderId) {
    const j = await fetchJson(`/api/orders/${orderId}`);
    if (j?._net) return toast("No se pudo conectar con el servidor.");
    if (j?._bad_json) return toast("Respuesta inválida del servidor.");
    if (!j._http_ok || !j.ok) return toast(j.error || "No se pudo cargar el pedido.");

    edit.orderId = j.order.id;
    edit.address = j.order.address || "";
    edit.phone = j.order.phone || "";
    edit.courier_id = (j.order.courier_id || "").toString();
    edit.payment_method = (j.order.payment_method || "cash");
    edit.items = (j.items || []).map(it => ({
      product_id: it.product_id,
      qty: it.qty || 1,
      removed_ingredient_ids: it.removed_ingredient_ids || [],
      added_ingredient_ids: it.added_ingredient_ids || []
    }));

    const idEl = $("editOrderId");
    if (idEl) idEl.textContent = edit.orderId;
    const addr = $("editAddr");
    const phone = $("editPhone");
    const courier = $("editCourier");
    if (addr) addr.value = edit.address;
    if (phone) phone.value = edit.phone;
    renderEditCouriers();
    if (courier) courier.value = edit.courier_id;
    setEditPayMethod(edit.payment_method);

    renderEditProducts();
    renderEditItems();

    bindModalBackdropFixOnce("editOrderModal");
    const modalEl = document.getElementById("editOrderModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
  }

  async function saveEditOrder() {
    if (!edit.orderId) return;

    edit.address = ($("editAddr")?.value || "").trim();
    edit.phone = ($("editPhone")?.value || "").trim();
    edit.courier_id = ($("editCourier")?.value || "").trim();

    if (!edit.address || !edit.phone) return toast("Faltan datos: Dirección y Teléfono.");
    if (!edit.items.length) return toast("No podés guardar un pedido vacío.");

    const payload = {
      phone: edit.phone,
      address: edit.address,
      courier_id: edit.courier_id ? parseInt(edit.courier_id, 10) : null,
      payment_method: getSelectedEditPayMethod(),
      items: edit.items
    };

    const j = await fetchJson(`/api/orders/${edit.orderId}/update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (j?._net) return toast("No se pudo conectar con el servidor.");
    if (j?._bad_json) return toast("Respuesta inválida del servidor.");
    if (!j._http_ok || !j.ok) return toast(j.error || "No se pudo guardar el pedido.");

    // cerrar modal
    const modalEl = document.getElementById("editOrderModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.hide();

    await refreshOrders();
    const vs = $("viewSummary");
    if (vs && vs.style.display !== "none") {
      await refreshSummary();
    }
  }

  function resetCurrent() {
    current = {
      tmpId: null,
      phone: "",
      address: "",
      courier_id: "",
      payment_method: current.payment_method || "cash",
      items: []
    };
    const addr = $("addr");
    const phone = $("phone");
    const courier = $("courier");
    if (addr) addr.value = "";
    if (phone) phone.value = "";
    if (courier) courier.value = "";
    renderCurrent();
  }

  // ===== Save order =====
  async function saveOrder() {
    current.address = ($("addr")?.value || "").trim();
    current.phone = ($("phone")?.value || "").trim();
    current.courier_id = ($("courier")?.value || "").trim();

    if (!current.address || !current.phone) return toast("Faltan datos: Dirección y Teléfono.");
    if (!current.items.length) return toast("No podés guardar un pedido vacío.");

    const payload = {
      phone: current.phone,
      address: current.address,
      courier_id: current.courier_id ? parseInt(current.courier_id, 10) : null,
      payment_method: getSelectedPayMethod(),
      items: current.items
    };

    const j = await fetchJson("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (j?._net) return toast("No se pudo conectar con el servidor.");
    if (j?._bad_json) return toast("El servidor respondió algo inválido (mirá la terminal / consola).");
    if (!j._http_ok || !j.ok) return toast(j.error || "No se pudo guardar el pedido.");

    const oid = j.id || j.order_id;
    if (oid) window.open(`/orders/${oid}/ticket`, "_blank");

    resetCurrent();
    await refreshOrders();
  }

  // ===== Orders view =====
  function flattenBoard() {
    const all = [];
    ["new", "kitchen", "way", "done"].forEach(st => {
      (board[st] || []).forEach(o => all.push({ ...o, _status: st }));
    });
    all.sort((a, b) => (b.id || 0) - (a.id || 0));
    return all;
  }

  function statusLabel(st) {
    const map = { new: "Nuevo", kitchen: "Cocina", way: "En camino", done: "Entregado" };
    return map[st] || st;
  }

  async function setStatus(orderId, status) {
    const j = await fetchJson(`/api/orders/${orderId}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status })
    });

    if (j?._net) return { ok: false, error: "No se pudo conectar con el servidor." };
    if (j?._bad_json) return { ok: false, error: "Respuesta inválida del servidor." };
    if (!j._http_ok || !j.ok) return { ok: false, error: j.error || "No se pudo cambiar el estado." };
    return { ok: true };
  }

  function renderOrdersList() {
    const wrap = $("ordersList");
    if (!wrap) return;

    wrap.innerHTML = "";
    const list = flattenBoard();

    if (!list.length) {
      wrap.innerHTML = `<div class="muted" style="font-size:13px;">Todavía no hay pedidos.</div>`;
      return;
    }

    list.forEach(o => {
      const el = document.createElement("div");
      el.className = "item";

      if (o._status === "done") {
        el.style.background = "rgba(34,197,94,.22)";
        el.style.borderColor = "rgba(34,197,94,.50)";
      }

      el.innerHTML = `
        <div class="d-flex align-items-start justify-content-between gap-2">
          <div>
            <div style="font-weight:900;">${o.address} • $ ${money(o.total_cents)}</div>
            <div class="muted" style="font-size:12px;">${o.phone || ""} ${o.courier_name ? "• " + o.courier_name : ""}</div>
          </div>

          <div class="d-flex flex-column align-items-end gap-1">
            <label class="muted" style="font-size:12px; display:flex; align-items:center; gap:.4rem; user-select:none;">
              <input type="checkbox" data-action="delivered" ${o._status === "done" ? "checked" : ""} />
              Entregado
            </label>
            <span class="chip">${statusLabel(o._status)}</span>
          </div>
        </div>

        <div class="d-flex gap-2 mt-2">
          <a class="btn-ghost" style="padding:.45rem .7rem;" target="_blank" href="/orders/${o.id}/ticket">Imprimir</a>
          <button class="btn-ghost" type="button" style="padding:.45rem .7rem;" data-action="edit">Editar</button>
          <button class="btn-danger-soft" type="button" style="padding:.45rem .7rem;" data-action="delete">Eliminar</button>
        </div>
      `;

      const cb = el.querySelector('[data-action="delivered"]');
      cb.onchange = async () => {
        const target = cb.checked ? "done" : "way";
        cb.disabled = true;

        const res = await setStatus(o.id, target);
        cb.disabled = false;

        if (!res.ok) {
          cb.checked = !cb.checked;
          toast(res.error);
          return;
        }

        await refreshOrders();

        const vs = $("viewSummary");
        if (vs && vs.style.display !== "none") {
          await refreshSummary();
        }
      };

      const editBtn = el.querySelector('[data-action="edit"]');
      editBtn.onclick = async () => {
        editBtn.disabled = true;
        await openEditOrder(o.id);
        editBtn.disabled = false;
      };

      const delBtn = el.querySelector('[data-action="delete"]');
      delBtn.onclick = async () => {
        if (!confirm("¿Eliminar este pedido?")) return;

        delBtn.disabled = true;
        const j = await fetchJson(`/api/orders/${o.id}/delete`, { method: "POST" });
        delBtn.disabled = false;

        if (j?._net) return toast("No se pudo conectar con el servidor.");
        if (j?._bad_json) return toast("Respuesta inválida del servidor.");
        if (!j._http_ok || !j.ok) return toast(j.error || "No se pudo eliminar el pedido.");

        await refreshOrders();

        const vs = $("viewSummary");
        if (vs && vs.style.display !== "none") {
          await refreshSummary();
        }
      };

      wrap.appendChild(el);
    });
  }

  async function refreshOrders() {
    await loadBoard();
    renderOrdersList();
  }

  // ===== Summary view =====
  function renderSummary() {
    const top = $("summaryTop");
    const wrap = $("courierSummary");
    if (!top || !wrap) return;

    top.innerHTML = "";
    wrap.innerHTML = "";

    if (!liquidation) {
      wrap.innerHTML = `<div class="muted" style="font-size:13px;">No se pudo cargar liquidación.</div>`;
      return;
    }

    const totalC = liquidation.total_cents ?? 0;
    const transferC = liquidation.transfer_cents ?? 0;
    const cashToRenderC = liquidation.cash_to_render_cents ?? (totalC - transferC);

    top.innerHTML = `
      <div class="summary-pill">
        <div>
          <b>Total</b>
          <div class="muted" style="font-size:12px;">Hoy</div>
        </div>
        <div style="text-align:right;">
          <b>$ ${money(totalC)}</b>
          <div class="muted" style="font-size:12px;">Transf: $ ${money(transferC)} • Efectivo a rendir: $ ${money(cashToRenderC)}</div>
        </div>
      </div>
    `;

    const couriers = liquidation.couriers || [];
    if (!couriers.length) {
      wrap.innerHTML = `<div class="muted" style="font-size:13px;">Todavía no hay pedidos asignados a repartidores.</div>`;
      return;
    }

    couriers.forEach(c => {
      const name = c.name || c.courier_name || "—";
      const ordersCount = c.orders_count ?? 0;
      const tot = c.total_cents ?? 0;
      const cashC = c.cash_cents ?? tot;
      const trC = c.transfer_cents ?? 0;

      const el = document.createElement("div");
      el.className = "summary-pill";
      el.innerHTML = `
        <div>
          <b>${name}</b>
          <div class="muted" style="font-size:12px;">Pedidos: ${ordersCount}</div>
        </div>
        <div style="text-align:right;">
          <b>$ ${money(tot)}</b>
          <div class="muted" style="font-size:12px;">Efectivo: $ ${money(cashC)} • Transf: $ ${money(trC)}</div>
        </div>
      `;
      wrap.appendChild(el);
    });
  }

  async function refreshSummary() {
    await loadLiquidation();
    renderSummary();
  }

  // ===== Init =====
  async function init() {
    await loadBootstrap();
    renderCouriers();
    renderProducts();
    renderCurrent();

    const search = $("search");
    if (search) search.addEventListener("input", renderProducts);

    const bNew = $("btnViewNew");
    const bOrders = $("btnViewOrders");
    const bSum = $("btnViewSummary");
    if (bNew) bNew.addEventListener("click", goNew);
    if (bOrders) bOrders.addEventListener("click", goOrders);
    if (bSum) bSum.addEventListener("click", goSummary);

    const payCash = $("payCash");
    const payTransfer = $("payTransfer");
    if (payCash) payCash.addEventListener("click", () => setPayMethod("cash"));
    if (payTransfer) payTransfer.addEventListener("click", () => setPayMethod("transfer"));
    setPayMethod(current.payment_method || "cash");

    bindModalBackdropFixOnce("modsModal");
    bindModalBackdropFixOnce("editOrderModal");

    // Edit modal bindings (una sola vez)
    const editSearch = $("editSearch");
    if (editSearch) editSearch.addEventListener("input", renderEditProducts);

    const editPayCash = $("editPayCash");
    const editPayTransfer = $("editPayTransfer");
    if (editPayCash) editPayCash.addEventListener("click", () => setEditPayMethod("cash"));
    if (editPayTransfer) editPayTransfer.addEventListener("click", () => setEditPayMethod("transfer"));

    const editClear = $("editClear");
    if (editClear) editClear.addEventListener("click", () => {
      edit.items = [];
      renderEditItems();
    });

    const editSave = $("editSave");
    if (editSave) editSave.addEventListener("click", saveEditOrder);

    await goNew();
    await loadBoard();
  }

  return {
    init,
    renderProducts,
    saveOrder,
    resetCurrent,
  };
})();
