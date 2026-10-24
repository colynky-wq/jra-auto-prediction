from __future__ import annotations

import subprocess

WORKFLOW = '.github/workflows/repair-live-research-completeness-once.yml'


def main() -> None:
    # The previous commit contains the reviewed one-off patch body. Extract only
    # its Python heredoc. GitHub Actions YAML adds ten spaces to every line in the
    # literal block, so remove exactly that YAML indentation and preserve Python's
    # own relative indentation.
    text = subprocess.check_output(['git', 'show', f'HEAD^:{WORKFLOW}'], text=True)
    start_marker = "          python - <<'PY'\n"
    end_marker = "\n          PY\n"
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    raw = text[start:end]
    code = '\n'.join(line[10:] if line.startswith('          ') else line for line in raw.splitlines()) + '\n'
    compile(code, WORKFLOW + ':embedded-repair', 'exec')
    exec(code, {'__name__': '__main__'})


if __name__ == '__main__':
    main()
