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
