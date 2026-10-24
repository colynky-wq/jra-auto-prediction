from __future__ import annotations

import subprocess

WORKFLOW = '.github/workflows/repair-live-research-completeness-once.yml'
START = "          python - <<'PY'\n"
END = "\n          PY\n"


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
        # Guard against accidentally selecting a tiny unrelated heredoc.
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


if __name__ == '__main__':
    main()
