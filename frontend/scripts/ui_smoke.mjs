/**
 * Browser smoke test: drive the real UI against a live stack.
 *
 * Requires: redis + engine (:8001) + gateway (:8000) + `vite preview` (:4173).
 * Run:      node scripts/ui_smoke.mjs
 */

import { chromium } from "playwright";

const BASE = process.env.SMOKE_URL ?? "http://127.0.0.1:4173";
const results = [];

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

async function main() {
  const browser = await chromium.launch();

  // --- host creates a room ------------------------------------------------
  const hostCtx = await browser.newContext();
  const host = await hostCtx.newPage();
  const hostErrors = [];
  host.on("pageerror", (err) => hostErrors.push(String(err)));
  host.on("console", (msg) => {
    if (msg.type() === "error") hostErrors.push(msg.text());
  });

  await host.goto(BASE, { waitUntil: "networkidle" });
  check("home renders", await host.locator("h1.logo").isVisible());

  await host.fill('input[placeholder="e.g. Fox"]', "HostBot");
  await host.click('.home-form button.primary');
  await host.waitForSelector(".big-code", { timeout: 10_000 });
  const roomCode = (await host.locator(".big-code").textContent())?.trim() ?? "";
  check("room code shown", /^[A-Z2-9]{6}$/.test(roomCode), roomCode);
  check(
    "QR code rendered",
    (await host.locator(".lobby-hero svg").count()) > 0,
  );
  check(
    "lobby shows host",
    await host.locator('.lobby-list li:has-text("HostBot")').isVisible(),
  );
  check("start disabled below min players", await host.locator('button:has-text("Waiting for players…")').isDisabled());

  // --- guest joins via deep link -----------------------------------------
  const guestCtx = await browser.newContext();
  const guest = await guestCtx.newPage();
  const guestErrors = [];
  guest.on("pageerror", (err) => guestErrors.push(String(err)));

  await guest.goto(`${BASE}/?code=${roomCode}`, { waitUntil: "networkidle" });
  const codeInput = guest.locator(".code-input");
  await codeInput.waitFor({ timeout: 5_000 });
  check("deep link prefills code", (await codeInput.inputValue()) === roomCode);

  await guest.fill('input[placeholder="e.g. Fox"]', "GuestOne");
  await guest.click('.home-form button.primary');
  await guest.waitForSelector(".big-code", { timeout: 10_000 });
  check("guest sees same room code", ((await guest.locator(".big-code").textContent())?.trim() ?? "") === roomCode);

  // A separate context: the same context would auto-reconnect the stored
  // GuestOne token instead of showing the join form.
  const guest2Ctx = await browser.newContext();
  const guest2 = await guest2Ctx.newPage();
  await guest2.goto(`${BASE}/?code=${roomCode}`, { waitUntil: "networkidle" });
  await guest2.fill('input[placeholder="e.g. Fox"]', "GuestTwo");
  await guest2.click('.home-form button.primary');
  await guest2.waitForSelector(".big-code", { timeout: 10_000 });

  await host.waitForSelector('button:has-text("Start game")', { timeout: 10_000 });
  check("start enabled with 3 players", await host.locator('button:has-text("Start game")').isEnabled());

  // --- host starts, everyone lands on the question ------------------------
  await host.click('button:has-text("Start game")');
  await host.waitForSelector(".question-text", { timeout: 10_000 });
  const question = (await host.locator(".question-text").textContent())?.trim() ?? "";
  check("question displayed", question.length > 0, question.slice(0, 40));
  check("options A/B displayed", (await host.locator(".option").count()) === 2);

  await guest.waitForSelector(".question-text", { timeout: 10_000 });
  check("guest sees question too", true);
  check(
    "phase pill shows QUESTION",
    (await host.locator(".phase-pill").textContent())?.includes("QUESTION") ?? false,
  );
  check("countdown running", (await host.locator(".countdown").first().textContent()) !== null);

  // --- chat during discussion --------------------------------------------
  await host
    .locator(".phase-pill", { hasText: "DISCUSSION" })
    .waitFor({ timeout: 25_000 })
    .catch(() => {});
  if ((await host.locator(".phase-pill").textContent())?.includes("DISCUSSION")) {
    await guest.fill(".chat-form input", "trust me, pick A");
    await guest.click('.chat-form button:has-text("Send")');
    await host.waitForSelector('.chat-log li:has-text("trust me, pick A")', { timeout: 5_000 });
    check("chat reaches other players", true);
  } else {
    check("chat reaches other players", false, "never reached DISCUSSION");
  }

  // --- vote UI ------------------------------------------------------------
  await host.waitForSelector(".vote-btn", { timeout: 30_000 });
  check("vote buttons visible", true);
  await guest.click(".vote-btn.a");
  await guest.waitForSelector(".vote-locked", { timeout: 5_000 });
  check("vote locks after click", true);
  check(
    "vote button disabled after vote",
    await guest.locator(".vote-btn").first().isDisabled(),
  );

  // --- reveal bars --------------------------------------------------------
  await host.waitForSelector(".bar-row", { timeout: 25_000 });
  check("reveal bars rendered", (await host.locator(".bar-row").count()) === 2);

  // --- reload resumes the session from the stored token --------------------
  await guest.reload({ waitUntil: "networkidle" });
  await guest.waitForSelector(".shell", { timeout: 10_000 });
  const resumedCode = (await guest.locator(".room-code").textContent())?.trim();
  check("reload resumes into the same room", resumedCode === roomCode, resumedCode ?? "");

  check("no host page errors", hostErrors.length === 0, hostErrors.slice(0, 2).join(" | "));
  check("no guest page errors", guestErrors.length === 0, guestErrors.slice(0, 2).join(" | "));

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((err) => {
  console.error("SMOKE CRASHED:", err);
  process.exit(1);
});
