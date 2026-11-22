import unittest
from datetime import datetime
from monitor_health_runtime import JST, MONITOR_STEP, REPOSITORY, WORKFLOW, choose_health
from monitor_health_reconciliation import recent_race_day_health


def run(run_id, state, conclusion=None, created="2026-11-22T02:39:11Z"):
    return {"id": run_id, "name": WORKFLOW, "event": "schedule",
            "head_branch": "main", "head_repository": {"full_name": REPOSITORY},
            "status": state, "conclusion": conclusion, "created_at": created,
            "run_started_at": created}


class SessionReconciliation(unittest.TestCase):
    now = datetime(2026, 11, 22, 14, 47, tzinfo=JST)

    def test_actual_nov_22_interruption_false_positive_corrected(self):
        runs = [run(44977082119, "completed", "cancelled"),
                run(44973990350, "in_progress", None,
                    "2026-11-22T01:59:50Z")]
        def jobs(rid):
            if rid == 44977082119:
                return []  # independently verified zero workflow jobs
            return [{"status": "in_progress", "started_at": "2026-11-22T01:59:50Z",
                     "steps": [{"name": MONITOR_STEP, "status": "in_progress"}]}]
        healths = recent_race_day_health(runs, jobs, self.now)
        self.assertEqual({x["runID"]: x["status"] for x in healths},
                         {44977082119: "superseded", 44973990350: "active"})

    def test_missing_jobs_never_reclassifies_interrupted(self):
        r = run(44977082119, "completed", "cancelled")
        def bad(_):
            raise OSError("unavailable")
        self.assertEqual(recent_race_day_health([r], bad, self.now)[0]["status"],
                         "interrupted")

    def test_queued_request_is_not_live_success(self):
        x = recent_race_day_health([run(44979334468, "pending", None,
                 "2026-11-22T03:09:33Z")], lambda _: [], self.now)[0]
        self.assertEqual(x["status"], "queued")

    def test_other_days_and_untrusted_workflows_excluded(self):
        wrong = run(3, "completed", "success")
        wrong["head_branch"] = "other"
        self.assertEqual(recent_race_day_health([run(1, "completed", "success",
             "2026-11-21T02:00:00Z"), wrong], lambda _: [], self.now), [])

    def test_late_skipped_heartbeat_cannot_mask_completed_race_day(self):
        # The Nov 22 19:33 JST backup was successful in Actions but all
        # actual live monitoring steps were skipped after the phase cutoff.
        now = datetime(2026, 11, 22, 19, 40, tzinfo=JST)
        good = run(44994368639, "completed", "success",
                   "2026-11-22T06:34:59Z")
        late = run(45012997807, "completed", "success",
                   "2026-11-22T10:33:11Z")
        def jobs(rid):
            steps = ([{"name": MONITOR_STEP, "conclusion": "success"}]
                     if rid == good["id"] else
                     [{"name": MONITOR_STEP, "conclusion": "skipped"}])
            return [{"status": "completed", "conclusion": "success", "steps": steps}]
        healths = recent_race_day_health([good, late], jobs, now)
        self.assertEqual({h["runID"]: h["status"] for h in healths},
                         {good["id"]: "completed", late["id"]: "not_started"})
        selected = choose_health(*healths)
        self.assertEqual(selected["runID"], good["id"])
        self.assertEqual(selected["status"], "completed")
        self.assertFalse(selected["needsAttention"])

    def test_late_skipped_run_alone_remains_visible_and_daytime_failures_not_hidden(self):
        now = datetime(2026, 11, 22, 19, 40, tzinfo=JST)
        good = run(44994368639, "completed", "success",
                   "2026-11-22T06:34:59Z")
        daytime_bad = run(45000000001, "completed", "success",
                          "2026-11-22T07:00:00Z")
        late_bad = run(45012997807, "completed", "success",
                       "2026-11-22T10:33:11Z")
        def jobs(rid):
            return [{"status": "completed", "conclusion": "success",
                     "steps": ([{"name": MONITOR_STEP, "conclusion": "success"}]
                               if rid == good["id"] else
                               [{"name": MONITOR_STEP, "conclusion": "skipped"}])}]
        healths = recent_race_day_health([good, daytime_bad, late_bad], jobs, now)
        self.assertEqual(choose_health(*[h for h in healths if h["runID"] == late_bad["id"]])["status"],
                         "not_started")
        self.assertEqual(choose_health(*healths)["runID"], daytime_bad["id"])
        self.assertTrue(choose_health(*healths)["needsAttention"])

    def test_limit_and_clock_fail_closed(self):
        with self.assertRaises(ValueError):
            recent_race_day_health([run(1, "pending"), run(2, "pending")],
                                   lambda _: [], self.now, limit=1)
        with self.assertRaises(ValueError):
            recent_race_day_health([], lambda _: [], datetime(2026, 11, 22, 12))


if __name__ == '__main__':
    unittest.main()
