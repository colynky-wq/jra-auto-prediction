// Optional morning watchdog. No HTTP entrypoint, JRA requests, or predictions.
// Uses GitHub's documented workflow dispatch API, disabled by default.
const API = "https://api.github.com/repos/colynky-wq/jra-auto-prediction/actions/workflows/keibaai-live-permanent.yml";
const JST_OFFSET = 9 * 60 * 60 * 1000;

export function morningWindow(now) {
  const jst = new Date(now.getTime() + JST_OFFSET);
  const day = jst.getUTCDay();
  const hour = jst.getUTCHours();
  if (![0, 1, 6].includes(day) || hour < 7 || hour >= 9) return null;
  const date = jst.toISOString().slice(0, 10);
  const start = new Date(Date.parse(date + "T00:00:00+09:00")).toISOString();
  return {date: date.replaceAll("-", ""), start};
}

export function decide(data) {
  if (!data || !Array.isArray(data.workflow_runs) ||
      !Number.isInteger(data.total_count) ||
      data.total_count !== data.workflow_runs.length ||
      data.workflow_runs.some(r => !r || typeof r.event !== "string" || typeof r.status !== "string")) return "incomplete";
  const runs = data.workflow_runs.filter(r => r.event === "schedule" || r.event === "workflow_dispatch");
  if (runs.some(r => r.status !== "completed")) return "active_or_queued";
  // At most one accepted manual/external launch per JST day. Do not replace
  // a queued run or repeat a failed manual launch. Native cron is retained.
  if (runs.some(r => r.event === "workflow_dispatch")) return "already_dispatched";
  if (runs.some(r => r.conclusion === "success")) return "already_completed";
  return "eligible";
}

export async function check(env, now = new Date(), request = fetch) {
  const window = morningWindow(now);
  if (!window) return "outside_window";
  if (!env.GITHUB_TOKEN) return "missing_secret";
  const headers = {
    "Authorization": "Bearer " + env.GITHUB_TOKEN,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "KeibaAI-morning-watchdog"
  };
  const query = new URLSearchParams({branch: "main", created: ">=" + window.start, per_page: "100"});
  const list = await request(API + "/runs?" + query.toString(), {headers, redirect: "error"});
  if (!list.ok) return "list_http_" + list.status;
  const action = decide(await list.json());
  if (action !== "eligible") return action;
  if (env.ENABLE_DISPATCH !== "true") return "dry_run_would_dispatch";
  const response = await request(API + "/dispatches", {
    method: "POST", redirect: "error",
    headers: {...headers, "Content-Type": "application/json"},
    body: JSON.stringify({ref: "main", inputs: {max_minutes: "335", watchdog_date: window.date}})
  });
  return response.ok ? "dispatch_accepted" : "dispatch_http_" + response.status;
}

export default {
  async scheduled(_controller, env, _ctx) {
    try {
      console.log("KEIBAAI_WATCHDOG", await check(env));
    } catch {
      // Do not print credentials, upstream response bodies or request headers.
      console.error("KEIBAAI_WATCHDOG upstream_or_network_error");
    }
  }
};
