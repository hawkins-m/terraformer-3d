// Drives the setup wizard through every page and saves a 1024x768 screenshot of each.
// Run by scripts/installer_screenshots.py (it serves the wizard on a synthetic machine):
//   node scripts/installer_screenshots.mjs URL OUTDIR
import { createRequire } from "module";
import readline from "readline";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [url, out] = process.argv.slice(2);
const rl = readline.createInterface({ input: process.stdin });
const ask = (msg) => new Promise(res => { rl.once("line", res); console.log(msg); });

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
await page.goto(url);
await page.waitForFunction(() => window.wizard && document.body.dataset.page === "welcome");
const settle = () => page.evaluate(() => Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }))));
const shot = async (n) => { await settle(); await page.evaluate(() => document.querySelectorAll(".pad").forEach(e => { e.scrollTop = 0; })); await page.waitForTimeout(250); await page.screenshot({ path: `${out}/page-${String(n).padStart(2, "0")}.png` }); console.log(`page-${n}`); };
const next = async (id) => { await page.click("#btn-next"); await page.waitForFunction((p) => document.body.dataset.page === p, id); };

await shot(1);
await next("license");
await page.click("input[name=lic][value='1']");
await shot(2);
await next("hardware");
await shot(3);
await next("components");
await page.click(".listbox .it[data-id='flux1-redux-dev'] .name");
await page.hover(".listbox .it[data-id='flux1-redux-dev']");
await shot(4);
await next("destination");
await page.click("[data-test=storage]");
await page.waitForSelector("[data-result=storage] .result.ok");
await shot(5);
await next("connections");
for (const id of ["comfy_url", "comfy_models_dir", "blender"]) {
  await page.click(`[data-test=${id}]`);
  await page.waitForSelector(`[data-result=${id}] .result:not(:empty)`);
}
await shot(6);
await next("ready");
await shot(7);
await next("installing");
await page.waitForFunction(() => {
  const v = document.querySelector("#strip video");
  return v && v.currentTime > 1.2 && !v.paused && document.querySelector("#fill").style.width !== "0.0%";
}, null, { timeout: 20000 });
await shot(8);
await ask("finish-install");
await page.waitForFunction(() => !document.querySelector("#btn-next").disabled, null, { timeout: 10000 });
await next("finish");
await shot(9);
await browser.close();
rl.close();
