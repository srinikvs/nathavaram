document.addEventListener("submit", (event) => {
  const message = event.target.getAttribute && event.target.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-print]");
  if (button) {
    window.print();
  }
});

(function () {
  const shell = document.querySelector(".shell.nav-collapsed");
  const toggle = document.querySelector("[data-menu-toggle]");
  const backdrop = document.querySelector("[data-menu-backdrop]");
  const sidebar = document.getElementById("sidebar");
  if (!shell || !toggle) {
    return;
  }
  function setOpen(open) {
    shell.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    if (backdrop) {
      backdrop.hidden = !open;
    }
  }
  toggle.addEventListener("click", () => setOpen(!shell.classList.contains("nav-open")));
  if (backdrop) {
    backdrop.addEventListener("click", () => setOpen(false));
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
    }
  });
  if (sidebar) {
    sidebar.addEventListener("click", (event) => {
      if (event.target.closest("a")) {
        setOpen(false);
      }
    });
  }
})();

// Top horizontal scrollbar for wide tables. Wrappers marked [data-top-scroll]
// get a thin overflow-x track above them, two-way synced with the wrapper's
// own (bottom) scrollbar, which stays the native, keyboard-reachable control.
(function () {
  function setup(wrap) {
    const track = document.createElement("div");
    track.className = "top-scroll";
    track.setAttribute("aria-hidden", "true");
    track.tabIndex = -1;
    track.hidden = true;
    const spacer = document.createElement("div");
    spacer.className = "top-scroll-spacer";
    track.appendChild(spacer);
    wrap.parentNode.insertBefore(track, wrap);

    // Guard against feedback loops: skip while a mirrored write is in flight,
    // and skip when the other side already has the same scrollLeft.
    let syncing = false;
    function mirror(from, to) {
      if (syncing || to.scrollLeft === from.scrollLeft) {
        return;
      }
      syncing = true;
      to.scrollLeft = from.scrollLeft;
      requestAnimationFrame(() => {
        syncing = false;
      });
    }
    track.addEventListener("scroll", () => mirror(track, wrap));
    wrap.addEventListener("scroll", () => mirror(wrap, track));

    function update() {
      const overflows = wrap.scrollWidth > wrap.clientWidth;
      track.hidden = !overflows;
      if (overflows) {
        // Include the wrapper's border so both bars share one scroll range.
        spacer.style.width = wrap.scrollWidth + (wrap.offsetWidth - wrap.clientWidth) + "px";
        track.scrollLeft = wrap.scrollLeft;
      }
    }
    update();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(update);
      observer.observe(wrap);
      const table = wrap.querySelector("table");
      if (table) {
        observer.observe(table);
      }
    }
    window.addEventListener("resize", update);
    // The drawer can change the available width; re-measure when it toggles.
    const shell = document.querySelector(".shell");
    if (shell && typeof MutationObserver !== "undefined") {
      new MutationObserver(update).observe(shell, { attributes: true, attributeFilter: ["class"] });
    }
  }
  document.querySelectorAll("[data-top-scroll]").forEach(setup);
})();
