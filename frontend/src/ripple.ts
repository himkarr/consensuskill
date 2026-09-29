/**
 * Material-style ripple, installed once at startup.
 *
 * Any click on a button spawns an expanding circle at the pointer position.
 * Delegated at the document level so dynamically rendered buttons (phase
 * views, lobby rows) get it for free.
 */

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function makeRipple(host: HTMLElement, x: number, y: number): void {
  const rect = host.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height) * 2.2;
  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.width = `${size}px`;
  ripple.style.height = `${size}px`;
  ripple.style.left = `${x - rect.left - size / 2}px`;
  ripple.style.top = `${y - rect.top - size / 2}px`;
  host.appendChild(ripple);
  ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
}

export function installRipple(): void {
  if (typeof document === "undefined") return;
  const media = window.matchMedia(REDUCED_MOTION);

  document.addEventListener("pointerdown", (event) => {
    if (media.matches || !(event.target instanceof Element)) return;
    const host = event.target.closest<HTMLElement>("button");
    if (!host || host.classList.contains("no-ripple")) return;
    if (host instanceof HTMLButtonElement && host.disabled) return;
    makeRipple(host, event.clientX, event.clientY);
  });
}
