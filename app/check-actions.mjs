// Asserts which SponsorPay app controls appear per role and status (run: node app/check-actions.mjs app/index.html).
import { readFileSync } from "node:fs";
const html = readFileSync(process.argv[2], "utf8");
const js = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
const body = js.slice(js.indexOf("function actions"), js.indexOf("async function show"));
const esc = (s) => String(s ?? "");
const gen = (w, d = 4) => (Number(w) / 1e18).toFixed(d).replace(/\.?0+$/, "");
const make = new Function("esc", "gen", "ctx", `let me = ctx.me; ${body}; return actions;`);
const B = "0xbrand", C = "0xCreator", O = "0xother", now = Date.now() / 1000;
const base = { brand: B, creator: C, status: "OPEN", deadline: now + 9999, attempts_left: 3, hosts: ["x.com"], code: "sponsorpay-0", live_until: 0 };
const run = (me, c = {}, recov = 0) => make(esc, gen, { me })({ ...base, ...c }, recov);
const check = (name, ok) => { console.log(ok ? "ok  " : "FAIL", name); if (!ok) process.exitCode = 1; };

check("no wallet: prompt only", run(null).includes("Connect a wallet"));
check("creator (case-insensitive) can submit while open", run("0xcreator").includes('data-form="submit_post"') && run(C).includes("3 attempts left"));
check("brand and others cannot submit", !run(B).includes("submit_post") && !run(O).includes("submit_post"));
check("no attempts left or past deadline: no submit", !run(C, { attempts_left: 0 }).includes("submit_post") && !run(C, { deadline: now - 1 }).includes("submit_post"));
check("past deadline: anyone can refund the brand", run(O, { deadline: now - 1 }).includes('data-act="expire"') && !run(O).includes("expire"));
check("live: anyone can recheck; settle label after the live period", run(O, { status: "LIVE", live_until: now + 99 }).includes("Recheck the post now") && run(O, { status: "LIVE", live_until: now - 1 }).includes("Recheck and settle"));
for (const s of ["COMPLETED", "REFUNDED", "EXPIRED"]) check(`${s.toLowerCase()}: no actions`, run(C, { status: s }) === "");
check("recover only with a failed transfer", run(O, { status: "COMPLETED" }, 5).includes('data-form="recover"'));
