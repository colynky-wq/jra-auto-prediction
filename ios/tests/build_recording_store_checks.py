"""Compile the exact private production store with a deterministic HTTP fixture.

Only the surrounding Swift imports/test program are added. No production source
is rewritten, and URLSession.shared itself is exercised by the test protocol.
"""
from pathlib import Path
import argparse


def build(source, checks, output):
    text = Path(source).read_text()
    start = '// MARK: - Research recording status\n'
    end = '\nprivate struct KeibaAIRecordingStatusScreen: View {'
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError('production store boundary changed; inspect before updating the test')
    block = text.split(start, 1)[1].split(end, 1)[0]
    if not block.startswith('@MainActor\nprivate final class KeibaAIRecordingStatusStore: ObservableObject {'):
        raise ValueError('unexpected production store declaration')
    if 'URLSession.shared.data(for: request)' not in block:
        raise ValueError('production transport changed; inspect before updating the test')
    Path(output).write_text('import Foundation\nimport Combine\nimport Dispatch\n\n' + block + '\n\n' + Path(checks).read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--checks', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    build(args.source, args.checks, args.output)
