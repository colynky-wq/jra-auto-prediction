import test from "node:test";
import assert from "node:assert/strict";
import {morningWindow, decide, check} from "./worker.mjs";

const morning = new Date("2026-11-28T07:15:00+09:00");
const env = {GITHUB_TOKEN: "synthetic-token", ENABLE_DISPATCH: "true"};
const payload = (...runs) => ({total_count: runs.length, workflow_runs: runs});
const run = (event, status, conclusion=null) => ({event,status,conclusion});

test("JST morning across UTC day boundary and excludes weekdays and late time", () => {
  assert.deepEqual(morningWindow(morning), {date:"20261128",start:"2026-11-27T15:00:00.000Z"});
  assert.equal(morningWindow(new Date("2026-11-28T09:00:00+09:00")), null);
  assert.equal(morningWindow(new Date("2026-11-27T07:15:00+09:00")), null);
  assert.equal(morningWindow(new Date("2026-11-30T08:59:59+09:00")).date, "20261130");
});
test("skip active, queued, completed successful cron or any manual launch", () => {
  for(const status of ["in_progress","queued","waiting","requested","pending"])
    assert.equal(decide(payload(run("schedule",status))),"active_or_queued");
  assert.equal(decide(payload(run("schedule","completed","success"))),"already_completed");
  assert.equal(decide(payload(run("workflow_dispatch","completed","failure"))),"already_dispatched");
  assert.equal(decide(payload(run("schedule","completed","failure"))),"eligible");
  assert.equal(decide(payload(run("push","in_progress"))),"eligible");
  assert.equal(decide({total_count:101,workflow_runs:[]}),"incomplete");
});
test("dry run never writes; dispatch uses exact workflow, target day and main", async () => {
  const calls=[];
  const request=async (url, options)=>{
    calls.push({url,options});
    return options.method==="POST"?{ok:true,status:204}:{ok:true,json:async()=>payload()};
  };
  assert.equal(await check({...env,ENABLE_DISPATCH:"false"},morning,request),"dry_run_would_dispatch");
  assert.equal(calls.length,1);
  assert.equal(await check(env,morning,request),"dispatch_accepted");
  assert.equal(calls.length,3);
  assert.equal(calls[2].options.method,"POST");
  assert.equal(calls[2].options.redirect,"error");
  assert.deepEqual(JSON.parse(calls[2].options.body),{ref:"main",inputs:{max_minutes:"335",watchdog_date:"20261128"}});
  assert.match(calls[0].url,/keibaai-live-permanent\.yml\/runs\?/);
});
test("no token, outside window, API denial or ambiguous results never dispatch", async () => {
  const forbidden=async()=>{throw Error("unexpected request")};
  assert.equal(await check({},morning,forbidden),"missing_secret");
  assert.equal(await check(env,new Date("2026-11-28T10:00:00+09:00"),forbidden),"outside_window");
  for(const status of [401,403,429,503]){
    let count=0;
    assert.equal(await check(env,morning,async()=>{count++;return {ok:false,status}}),"list_http_"+status);
    assert.equal(count,1);
  }
  let calls=0;
  assert.equal(await check(env,morning,async()=>{calls++;return {ok:true,json:async()=>({total_count:2,workflow_runs:[]})}}),"incomplete");
  assert.equal(calls,1);
});
