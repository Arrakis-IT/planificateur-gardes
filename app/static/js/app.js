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
