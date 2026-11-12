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
