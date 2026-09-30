/** Screenshot every major screen for visual review. */
import { chromium } from "playwright";

const BASE = process.env.SMOKE_URL ?? "http://127.0.0.1:4173";
const OUT = new URL("../shots/", import.meta.url).pathname;

const shot = (page, name, opts = {}) =>
  page.screenshot({ path: `${OUT}${name}.png`, ...opts });

async function main() {
  const browser = await chromium.launch();

  // --- home ---------------------------------------------------------------
  const hostCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const host = await hostCtx.newPage();
  await host.goto(BASE, { waitUntil: "networkidle" });
  await shot(host, "1-home-desktop");

  const mobileCtx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
  });
  const phone = await mobileCtx.newPage();
  await phone.goto(BASE, { waitUntil: "networkidle" });
  await shot(phone, "2-home-mobile");

  // --- lobby (host, projector) -------------------------------------------
  await host.fill('input[placeholder="e.g. Fox"]', "HostBot");
  await host.click(".home-form button.primary");
  await host.waitForSelector(".big-code", { timeout: 10_000 });
  await host.waitForTimeout(700); // let phase-in settle
  await shot(host, "3-lobby-projector");

  // guest joins via deep link
  const guestCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 2 });
  const guest = await guestCtx.newPage();
  const code = (await host.locator(".big-code").textContent())?.trim() ?? "";
  await guest.goto(`${BASE}/?code=${code}`, { waitUntil: "networkidle" });
  await guest.fill('input[placeholder="e.g. Fox"]', "GuestOne");
  await guest.click('.home-form button.primary');
  await guest.waitForSelector('.lobby-list li:has-text("GuestOne")', { timeout: 10_000 });

  const guest2Ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 2 });
  const guest2 = await guest2Ctx.newPage();
  await guest2.goto(`${BASE}/?code=${code}`, { waitUntil: "networkidle" });
  await guest2.fill('input[placeholder="e.g. Fox"]', "GuestTwo");
  await guest2.click('.home-form button.primary');
  await guest2.waitForSelector('.lobby-list li:has-text("GuestTwo")', { timeout: 10_000 });

  await shot(guest, "4-lobby-phone");
  await host.waitForSelector('button:has-text("Start game")', { timeout: 10_000 });

  // --- question / discussion ---------------------------------------------
  await host.click('button:has-text("Start game")');
  await host.waitForSelector(".question-text", { timeout: 10_000 });
  await host.waitForTimeout(700);
  await shot(host, "5-question-projector");
  await shot(guest, "6-question-phone");

  // chat during discussion
  await host
    .locator(".phase-pill", { hasText: "DISCUSSION" })
    .waitFor({ timeout: 25_000 })
    .catch(() => {});
  await guest.fill(".chat-form input", "trust me, pick A");
  await guest.click('.chat-form button:has-text("Send")');
  await host.fill(".chat-form input", "A is clearly right");
  await host.click('.chat-form button:has-text("Send")');
  await host.waitForTimeout(400);
  await shot(host, "7-discussion-projector");

  // --- vote ---------------------------------------------------------------
  await host.waitForSelector(".vote-btn", { timeout: 30_000 });
  await host.waitForTimeout(600);
  await shot(host, "8-vote-projector");
  await guest.click(".vote-btn.a");
  await guest.waitForSelector(".vote-locked", { timeout: 5_000 });
  await shot(guest, "9-vote-locked-phone");

  // --- reveal -------------------------------------------------------------
  await host.waitForSelector(".bar-row", { timeout: 25_000 });
  await host.waitForTimeout(900); // bar fills
  await shot(host, "10-reveal-projector");
  await shot(guest, "11-reveal-phone");

  await browser.close();
  console.log("shots written to", OUT);
}

main().catch((err) => {
  console.error("SHOT CRASHED:", err);
  process.exit(1);
});
