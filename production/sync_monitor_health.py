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
                                    health_from_event, validate_event)
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


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    run = validate_event(event)
    try:
        jobs = fetch_jobs(run['id'], os.environ['GITHUB_TOKEN'])
    except (OSError, ValueError, KeyError):
        jobs = []
        print('::warning::監視の詳細を取得できないため、実行結果だけを使います。')
    health = health_from_event(event, jobs, datetime.now(JST))
    root = Path(__file__).resolve().parent.parent
    date8 = health['raceDate'].replace('-', '')
    path = root/'reports'/date8/'monitor_sessions'/f'{health["runID"]}.json'
    if path.exists():
        health = choose_health(health, json.loads(path.read_text()))
    atomic(path, (json.dumps(health, ensure_ascii=False, indent=2)+'\n').encode())
    status_path = root/'PaceRecordingStatus.json'
    status = json.loads(status_path.read_text())
    status = apply_health(status, choose_health(status.get('monitoring'), health))
    status['generatedAtJST'] = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    atomic(status_path, (json.dumps(status, ensure_ascii=False, indent=2)+'\n').encode())
    atomic(root/'reports/PaceRecordingStatus.md', markdown(status).encode())
    folder = Path(os.environ['RUNNER_TEMP'])/'keibaai-monitor-health-outbox'
    publisher = Outbox(root, folder, date8, today=lambda: health['raceDate'], role='health')
    publisher.capture()
    for attempt in range(3):
        if publisher.publish():
            return
        if attempt < 2:
            time.sleep(2)
    raise SystemExit('監視の状態を公開できませんでした。未送信データは成果物へ保存します。')


if __name__ == '__main__':
    main()
