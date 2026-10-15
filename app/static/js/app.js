// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services
//
// Live features for clinic pages: presence heartbeat, SSE events, planning refresh, modal.
// Everything degrades silently: if SSE or the heartbeat fail, the pages keep working
// (no popups); edits still apply and the page is simply not refreshed live.
(function () {
  "use strict";

  var HEARTBEAT_MS = 10000;
  var RECONNECT_MS = 30000;
  var body = document.body;

  // --- PWA: static-assets-only service worker (see app/routers/pwa.py) -------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {
        /* not critical: the app works the same without it */
      });
    });
  }

  // --- "Plus" bottom sheet (a <details>; JS only adds tap-outside / Escape to close) -----
  function closeMore() {
    document.querySelectorAll("details.more[open]").forEach(function (sheet) {
      sheet.open = false;
    });
  }
  document.addEventListener("click", function (event) {
    if (event.target instanceof HTMLElement && event.target.dataset.closeMore !== undefined) {
      closeMore();
    }
  });

  // --- Modal ------------------------------------------------------------------------
  function closeModal() {
    var modal = document.getElementById("modal");
    if (modal) modal.innerHTML = "";
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    // Backdrop click (not inside the dialog) or explicit close button.
    if (target instanceof HTMLElement && target.dataset.closeModal !== undefined) closeModal();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeModal();
      closeMore();
    }
  });
  document.body.addEventListener("closeModal", closeModal); // HX-Trigger from the server

  // --- Toasts -------------------------------------------------------------------------
  // Server-rendered (flash) or sent as an HX-Trigger "toast" event {message, level}. CSS fades
  // them out; they are removed once the fade-out animation ends.
  function removeWhenFaded(toast) {
    toast.addEventListener("animationend", function (event) {
      if (event.animationName === "toast-out") toast.remove();
    });
  }
  document.querySelectorAll("#toasts .toast").forEach(removeWhenFaded);
  function showToast(message, level) {
    var container = document.getElementById("toasts");
    if (!container || !message) return;
    var toast = document.createElement("output");
    level = level === "error" || level === "warn" ? level : "success";
    toast.className = "toast toast-" + level;
    toast.setAttribute("role", level === "error" ? "alert" : "status");
    toast.textContent = message;
    removeWhenFaded(toast);
    container.appendChild(toast);
  }
  document.body.addEventListener("toast", function (event) {
    var detail = event.detail || {};
    showToast(detail.message, detail.level);
  });

  // --- Failed HTMX requests ------------------------------------------------------------
  // An HTMX request that fails (HTTP error, network error, or an interstitial / challenge
  // from the CDN in front of the app) used to fail silently: the modal simply never
  // appeared. A failed GET from a link falls back to a normal navigation to its href (the
  // server renders the same content as a full page); anything else shows an error toast,
  // unless the server already sent its own toast.
  function onHtmxFailure(event) {
    var detail = event.detail || {};
    var elt = detail.elt;
    var xhr = detail.xhr;
    var verb = ((detail.requestConfig || {}).verb || "").toLowerCase();
    if (xhr && /"toast"/.test(xhr.getResponseHeader("HX-Trigger") || "")) return;
    if (elt && elt.tagName === "A" && elt.href && verb === "get") {
      window.location.assign(elt.href);
      return;
    }
    var status = xhr && xhr.status ? " (erreur " + xhr.status + ")" : "";
    showToast("La requête a échoué" + status + ". Réessayez ou rechargez la page.", "error");
  }
  document.body.addEventListener("htmx:responseError", onHtmxFailure);
  document.body.addEventListener("htmx:sendError", onHtmxFailure);

  // --- Group palette: confirm before recoloring every doctor ----------------------------------
  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || input.name !== "palette") return;
    var form = input.closest("[data-palette-form]");
    var dialog = form && form.querySelector("[data-palette-dialog]");
    if (!dialog || input.value === form.dataset.current) return;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  });
  function cancelPalette(form) {
    var current = form.querySelector('input[name="palette"][value="' + form.dataset.current + '"]');
    if (current) current.checked = true;
    var dialog = form.querySelector("[data-palette-dialog]");
    if (dialog && dialog.open) dialog.close ? dialog.close() : dialog.removeAttribute("open");
  }
  document.addEventListener("click", function (event) {
    var button = event.target instanceof Element && event.target.closest("[data-palette-cancel]");
    if (button) cancelPalette(button.closest("[data-palette-form]"));
  });
  document.addEventListener("cancel", function (event) {  // Escape on the dialog
    var form = event.target instanceof Element && event.target.closest("[data-palette-form]");
    if (form) cancelPalette(form);
  }, true);

  // --- Doctor color dropdowns: live preview swatch ------------------------------------------
  document.addEventListener("change", function (event) {
    var select = event.target;
    if (!(select instanceof HTMLSelectElement) || select.dataset.colorPreview === undefined) return;
    var preview = select.parentElement && select.parentElement.querySelector(".color-preview");
    if (preview) preview.style.setProperty("--doctor-color", select.value);
    var row = select.closest("tr");
    var chip = row && row.querySelector(".code");
    if (chip) chip.style.setProperty("--doctor-color", select.value);
  });

  // --- Copy buttons (e.g. the iCal subscription link) --------------------------------------
  document.addEventListener("click", function (event) {
    var button = event.target instanceof Element && event.target.closest("[data-copy-target]");
    if (!button) return;
    var input = document.getElementById(button.getAttribute("data-copy-target"));
    if (!input) return;
    input.select();
    var copied = function () { showToast("Lien copié.", "success"); };
    var legacy = function () { if (document.execCommand("copy")) copied(); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(copied, legacy);
    } else {
      legacy();
    }
  });

  // Cells of locked months are inert buttons: say why instead of doing nothing on tap.
  document.addEventListener("click", function (event) {
    var cell = event.target instanceof Element && event.target.closest("[data-locked-message]");
    if (!cell) return;
    event.preventDefault();
    var message = cell.getAttribute("data-locked-message");
    var shown = Array.prototype.some.call(
      document.querySelectorAll("#toasts .toast"),
      function (toast) { return toast.textContent === message; }
    );
    if (!shown) showToast(message, "warn");
  });

  // --- Planning refresh ---------------------------------------------------------------
  var refreshPending = false;
  function refreshPlanning() {
    if (!document.getElementById("planning") || !window.htmx || refreshPending) return;
    refreshPending = true;
    window.htmx
      .ajax("GET", window.location.pathname + window.location.search, {
        target: "#planning",
        select: "#planning",
        swap: "outerHTML",
      })
      .finally(function () {
        refreshPending = false;
      });
  }
  document.body.addEventListener("planningChanged", refreshPlanning);

  if (!body.dataset.live) return; // presence / SSE only for clinic users

  // --- Presence widget --------------------------------------------------------------
  var self = body.dataset.self;
  function renderPresence(present) {
    var widget = document.getElementById("presence");
    if (!widget || !Array.isArray(present)) return;
    var others = present.filter(function (user) {
      return user.code !== self;
    });
    if (others.length === 0) {
      widget.hidden = true;
      widget.textContent = "";
      return;
    }
    widget.textContent =
      "Aussi en ligne : " +
      others
        .map(function (user) {
          return user.code;
        })
        .join(", ");
    widget.title = others
      .map(function (user) {
        return user.name;
      })
      .join(", ");
    widget.hidden = false;
  }

  // --- Heartbeat ------------------------------------------------------------------------
  var csrf = "";
  try {
    csrf = JSON.parse(body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
  } catch (error) {
    csrf = "";
  }
  function heartbeat() {
    fetch("/sse/heartbeat", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      credentials: "same-origin",
    }).catch(function () {
      /* offline: presence simply expires */
    });
  }
  heartbeat();
  window.setInterval(heartbeat, HEARTBEAT_MS);

  // --- Server-Sent Events ------------------------------------------------------------------
  if (!("EventSource" in window)) return;
  function parse(event) {
    try {
      return JSON.parse(event.data);
    } catch (error) {
      return {};
    }
  }
  function connect() {
    var source = new EventSource("/sse/presence");
    ["presence", "user_entered", "user_left"].forEach(function (name) {
      source.addEventListener(name, function (event) {
        renderPresence(parse(event).present);
      });
    });
    ["draft_changed", "draft_cleared"].forEach(function (name) {
      source.addEventListener(name, function (event) {
        renderPresence(parse(event).present);
        refreshPlanning();
      });
    });
    source.onerror = function () {
      // The browser retries by itself while CONNECTING; if it gave up, retry later.
      if (source.readyState === EventSource.CLOSED) {
        window.setTimeout(connect, RECONNECT_MS);
      }
    };
  }
  connect();
})();
