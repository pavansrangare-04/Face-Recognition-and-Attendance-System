const navToggle = document.querySelector(".nav-toggle");
const navLinks = document.querySelector("[data-nav-links]");
const year = new Date().getFullYear();

if (navToggle && navLinks) {
  navToggle.addEventListener("click", () => {
    const isOpen = navLinks.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", String(isOpen));
    navToggle.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
  });

  navLinks.addEventListener("click", (event) => {
    if (event.target.matches("a")) {
      navLinks.classList.remove("open");
      navToggle.setAttribute("aria-expanded", "false");
      navToggle.setAttribute("aria-label", "Open navigation");
    }
  });
}

const footerText = document.querySelector(".footer p");

if (footerText) {
  footerText.insertAdjacentText("beforeend", ` • ${year}`);
}