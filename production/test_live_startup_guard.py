"""Exercise the actual Actions shell guard with isolated HTTP responses."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / '.github/scripts/keibaai-race-day-check.sh'


class TestLiveStartupGuard(unittest.TestCase):
    def guard(self, body, exit_code=0, date='20261017'):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            curl = root/'curl'
            curl.write_text('#!/usr/bin/env python3\n'
                            'import os,sys\n'
                            'from pathlib import Path\n'
                            'args=sys.argv[1:]\n'
                            'body=os.environ["TEST_BODY"]\n'
                            'if "-o" in args: Path(args[args.index("-o")+1]).write_text(body)\n'
                            'else: sys.stdout.write(body)\n'
                            'sys.exit(int(os.environ["TEST_EXIT"]))\n')
            curl.chmod(0o755)
            output = root/'output'; output.touch()
            env = {**os.environ, 'PATH': str(root)+os.pathsep+os.environ['PATH'],
                   'TEST_BODY': body, 'TEST_EXIT': str(exit_code), 'GITHUB_OUTPUT': str(output)}
            result = subprocess.run(['bash', str(SCRIPT), date], env=env, text=True, capture_output=True)
            fields = dict(line.split('=', 1) for line in output.read_text().splitlines())
            return result, fields

    def test_race_day_counts_distinct_races(self):
        result, fields = self.guard('<a href="?race_id=202605040601">a</a>'
                                    '<a href="?race_id=202605040601">a</a>'
                                    '<a href="?race_id=202605040602">b</a>')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(fields['has_races'], 'true')
        self.assertEqual(fields['target_date'], '20261017')
        self.assertIn('races=2', result.stdout)

    def test_nonempty_no_race_response_keeps_normal_skip(self):
        result, fields = self.guard('<div>no races</div>')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(fields['has_races'], 'false')

    def test_http_failure_is_not_a_successful_no_race_day(self):
        result, fields = self.guard('', 22)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(fields.get('has_races'), 'false')
        self.assertIn('::error::', result.stdout+result.stderr)

    def test_timeout_does_not_accept_partial_race_list(self):
        result, fields = self.guard('race_id=202605040601', 28)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(fields.get('has_races'), 'true')

    def test_connection_failure_is_visible(self):
        result, fields = self.guard('', 6)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(fields.get('has_races'), 'false')

    def test_empty_success_response_is_unknown(self):
        result, fields = self.guard('')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(fields.get('has_races'), 'false')

    def test_whitespace_success_response_is_unknown(self):
        result, fields = self.guard(' \n\t ')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(fields.get('has_races'), 'false')

    def test_invalid_date_stops_before_http(self):
        result, fields = self.guard('race_id=202605040601', date='../../wrong')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fields, {})

    def test_invalid_calendar_date_stops_before_http(self):
        result, fields = self.guard('race_id=202605040601', date='20260229')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fields, {})


if __name__ == '__main__':
    unittest.main()
