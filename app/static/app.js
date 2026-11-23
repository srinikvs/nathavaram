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

// VoterId links sit in the row but are not a selection control. Stop their clicks here so
// that nothing above them treats the click as a row or select-all action.
document.querySelectorAll("[data-voter-link]").forEach((link) => {
  link.addEventListener("click", (event) => event.stopPropagation());
});

// Bulk row selection (admin pages only; the server renders the markup for admins).
// Select-all acts only on the rows rendered in this table. Filtering or paging reloads the
// page, so a selection never carries over to rows that are not on screen.
(function () {
  function setup(root) {
    const rows = Array.from(root.querySelectorAll("[data-row-select]"));
    const selectAlls = Array.from(root.querySelectorAll("[data-select-all]"));
    const bar = root.querySelector("[data-bulk-bar]");
    const count = root.querySelector("[data-bulk-count]");
    const clear = root.querySelector("[data-bulk-clear]");
    if (!bar || rows.length === 0) {
      return;
    }
    function selectable(box) {
      const row = box.closest("tr");
      return !box.disabled && !(row && row.hidden);
    }
    function update() {
      const usable = rows.filter(selectable);
      const picked = usable.filter((box) => box.checked).length;
      bar.hidden = picked === 0;
      if (count) {
        count.textContent = String(picked);
      }
      selectAlls.forEach((box) => {
        box.checked = usable.length > 0 && picked === usable.length;
        box.indeterminate = picked > 0 && picked < usable.length;
      });
    }
    function setAll(checked) {
      rows.filter(selectable).forEach((box) => {
        box.checked = checked;
      });
      update();
    }
    rows.forEach((box) => box.addEventListener("change", update));
    selectAlls.forEach((box) => box.addEventListener("change", () => setAll(box.checked)));
    if (clear) {
      clear.addEventListener("click", () => setAll(false));
    }
    setAll(false);
  }
  document.querySelectorAll("[data-bulk]").forEach(setup);
})();
