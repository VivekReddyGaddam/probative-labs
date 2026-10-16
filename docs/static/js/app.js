// Progressive enhancement only: every page works without JavaScript.
(function () {
  "use strict";

  // Mobile navigation toggle on the website.
  var toggle = document.querySelector("[data-nav-toggle]");
  var nav = document.querySelector("[data-nav]");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // "On this page": collapsed on small screens, and the section you are reading is highlighted.
  var docNav = document.querySelector("[data-doc-nav]");
  if (docNav) {
    var small = window.matchMedia("(max-width: 900px)");
    if (small.matches) docNav.removeAttribute("open");
    var links = Array.prototype.slice.call(docNav.querySelectorAll('a[href^="#"]'));
    var targets = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
    links.forEach(function (a) {
      a.addEventListener("click", function () { if (small.matches) docNav.removeAttribute("open"); });
    });
    var spy = function () {
      var current = 0;
      targets.forEach(function (t, i) { if (t && t.getBoundingClientRect().top <= 120) current = i; });
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = targets.length - 1;
      links.forEach(function (a, i) { a.classList.toggle("active", i === current); });
    };
    var queued = false;
    window.addEventListener("scroll", function () {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(function () { queued = false; spy(); });
    }, { passive: true });
    spy();
  }

  // Ask before destructive actions.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
    });
  });

  // Disable submit buttons after the first click so long uploads are not sent twice.
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type=submit]");
      if (button && !form.hasAttribute("data-confirm")) {
        window.setTimeout(function () { button.disabled = true; }, 0);
      }
    });
  });

  // Poll a running analysis and reload when it finishes.
  var poll = document.querySelector("[data-poll]");
  if (poll) {
    var url = poll.getAttribute("data-poll");
    var tick = function () {
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "succeeded" || data.status === "failed") window.location.reload();
          else window.setTimeout(tick, 2000);
        })
        .catch(function () { window.setTimeout(tick, 5000); });
    };
    window.setTimeout(tick, 2000);
  }
})();
