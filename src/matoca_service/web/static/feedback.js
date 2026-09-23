export function feedback(document, message, error = false) {
  const target = document.querySelector("#operation-feedback");
  if (!target) return;
  target.textContent = message;
  target.classList.toggle("is-error", error);
}
