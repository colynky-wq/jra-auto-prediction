from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

WORKFLOW = '.github/workflows/repair-live-research-completeness-once.yml'


def main() -> None:
    # The previous commit contains the reviewed one-off patch body. Extract only
    # the Python heredoc from that immutable parent so this runner stays tiny and
    # the workflow update that triggers it cannot silently change the repair.
    text = subprocess.check_output(['git', 'show', f'HEAD^:{WORKFLOW}'], text=True)
    start_marker = "          python - <<'PY'\n"
    end_marker = "\n          PY\n"
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker, start)
    code = textwrap.dedent(text[start:end])
    compile(code, WORKFLOW + ':embedded-repair', 'exec')
    exec(code, {'__name__': '__main__'})


if __name__ == '__main__':
    main()
