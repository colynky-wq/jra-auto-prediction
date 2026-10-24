from __future__ import annotations

import subprocess
from pathlib import Path

WORKFLOW = '.github/workflows/repair-live-research-completeness-once.yml'
START = "          python - <<'PY'\n"
END = "\n          PY\n"
TEST_PATH = 'production/test_live_recording_resume.py'


def current_file(path: str) -> str:
    return subprocess.check_output(['git', 'show', f'HEAD:{path}'], text=True)


def repair_test_file() -> None:
    # Rebuild this one test file from current main. The reviewed historical
    # patch had a YAML/heredoc indentation artifact in its test insertion;
    # production repair logic remains unchanged.
    text = current_file(TEST_PATH)
    old = 'from live_recording_resume import published_race_ids'
    new = 'from live_recording_resume import published_race_ids, research_sidecars_complete'
    if text.count(old) != 1:
        raise SystemExit('test import anchor mismatch')
    text = text.replace(old, new, 1)
    marker = '    def test_concurrent_display_writes_use_distinct_temporary_files(self):\n'
    if text.count(marker) != 1:
        raise SystemExit('test method anchor mismatch')
    method = '''    def test_resume_can_require_complete_research_sidecars(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.race();schedule=[{'race_id':r['raceID'],'start_time':'14:55'}]
            root=Path(t);(root/'RaceData.json').write_text(json.dumps([r]))
            self.assertEqual(published_race_ids(root,'20261018',schedule,require_research=True),set())
            day=root/'reports/20261018'
            for kind in ('win','place','frame_quinella','quinella','exacta'):
                p=day/'all_bet_v1'/kind/(r['raceID']+'.json')
                p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            for kind in ('trio','trifecta'):
                p=day/'triple_v1'/kind/(r['raceID']+'.json')
                p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            p=day/'wide_axis_v1'/(r['raceID']+'.json')
            p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            self.assertTrue(research_sidecars_complete(root,'20261018',r['raceID']))
            self.assertEqual(published_race_ids(root,'20261018',schedule,require_research=True),{r['raceID']})

'''
    text = text.replace(marker, method + marker, 1)
    Path(TEST_PATH).write_text(text)


def main() -> None:
    revisions = subprocess.check_output(
        ['git', 'log', '--format=%H', '--', WORKFLOW], text=True
    ).splitlines()
    chosen = None
    raw = None
    for rev in revisions:
        try:
            text = subprocess.check_output(
                ['git', 'show', f'{rev}:{WORKFLOW}'], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            continue
        if START not in text or END not in text:
            continue
        start = text.index(START) + len(START)
        end = text.index(END, start)
        candidate = text[start:end]
        if 'research_sidecars_complete' not in candidate or 'published_race_ids' not in candidate:
            continue
        chosen, raw = rev, candidate
        break
    if raw is None:
        raise SystemExit('reviewed completeness-repair heredoc not found in workflow history')
    code = '\n'.join(line[10:] if line.startswith('          ') else line for line in raw.splitlines()) + '\n'
    compile(code, f'{WORKFLOW}@{chosen}:embedded-repair', 'exec')
    print('Applying reviewed repair from', chosen)
    exec(code, {'__name__': '__main__'})
    repair_test_file()


if __name__ == '__main__':
    main()
