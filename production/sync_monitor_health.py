"""Publish trusted workflow health independently of the live monitor's outcome."""
from datetime import datetime
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from live_bundle_publisher import Outbox, atomic
from monitor_health_runtime import (JST, REPOSITORY, apply_health, choose_health,
                                    expire_waiting_races, health_from_event, validate_event)
from monitor_health_reconciliation import recent_race_day_health
from pace_recording_display import markdown


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Unexpected API redirect', headers, fp)


def fetch_jobs(run_id, token):
    request = Request(f'https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100',
                      headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                               'X-GitHub-Api-Version': '2022-11-28'})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        return json.load(response)['jobs']


def fetch_recent_runs(token):
    request = Request(
        f'https://api.github.com/repos/{REPOSITORY}/actions/runs?per_page=100',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28'})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = json.load(response)
    runs = data.get('workflow_runs')
    if not isinstance(runs, list):
        raise ValueError('missing GitHub workflow runs')
    return runs


def main():
    now = datetime.now(JST)
    token = os.environ['GITHUB_TOKEN']
    event_name = os.environ.get('GITHUB_EVENT_NAME', '')
    healths = []
    try:
        runs = fetch_recent_runs(token)
        healths = recent_race_day_health(
            runs, lambda run_id: fetch_jobs(run_id, token), now)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A scheduled/push reconciliation with no reliable run inventory
        # must not change health. Workflow-run events retain their own
        # independently verified event fallback.
        print('::warning::監視セッション一覧の再照合ができません。', type(exc).__name__)
        if event_name != 'workflow_run':
            raise

    if event_name == 'workflow_run':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        run = validate_event(event)
        if all(h['runID'] != run['id'] for h in healths):
            try:
                jobs = fetch_jobs(run['id'], token)
            except (OSError, ValueError, KeyError):
                jobs = None
            healths.append(health_from_event(event, jobs, now))

    if not healths:
        raise ValueError('no independently verified race-day monitor runs')

    root = Path(__file__).resolve().parent.parent
    status_path = root / 'PaceRecordingStatus.json'
    status = json.loads(status_path.read_text())
    day = now.date().isoformat()
    # Re-check previously cancelled pending requests, not just the latest
    # event. A verified zero-job cancelled run can supersede an old,
    # incorrectly published "interrupted" state without hiding an actual
    # active monitor from the same date.
    for health in healths:
        if health['raceDate'] != day:
            continue
        date8 = day.replace('-', '')
        path = root/'reports'/date8/'monitor_sessions'/f'{health["runID"]}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            health = choose_health(health, json.loads(path.read_text()))
        atomic(path, (json.dumps(health, ensure_ascii=False, indent=2)+'\n').encode())

    latest = choose_health(status.get('monitoring'), *healths)
    status = expire_waiting_races(status, now)
    status = apply_health(status, latest)
    status['generatedAtJST'] = now.strftime('%Y-%m-%d %H:%M:%S')
    atomic(status_path, (json.dumps(status, ensure_ascii=False, indent=2)+'\n').encode())
    atomic(root/'reports/PaceRecordingStatus.md', markdown(status).encode())
    date8 = day.replace('-', '')
    folder = Path(os.environ['RUNNER_TEMP'])/'keibaai-monitor-health-outbox'
    publisher = Outbox(root, folder, date8, today=lambda: day, role='health')
    publisher.capture()
    for attempt in range(4):
        if publisher.publish():
            return
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
    raise SystemExit('監視の状態を公開できませんでした。未送信データは成果物へ保存します。')

if __name__ == '__main__':
    main()
