document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("submit", (event) => {
      const message = element.getAttribute("data-confirm") || "Czy na pewno?";
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
});
