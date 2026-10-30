import runpy
import contextlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'refresh-contributors.py'
FUNCTIONS = runpy.run_path(str(SCRIPT))


class CreditsTests(unittest.TestCase):
    def test_model_trailers_deduplicate_context_variants(self):
        message = '''Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Co-authored-by: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Co-Authored-By: Claude Code <noreply@anthropic.com>
Co-Authored-By: OpenAI Codex <codex@openai.com>
Co-Authored-By: Codex <noreply@openai.com>'''
        self.assertEqual(FUNCTIONS['model_credits'](message), {
            ('Anthropic', 'Claude Opus', '5.5'), ('Anthropic', 'Claude', ''), ('OpenAI', 'Codex', '')})

    def test_session_only_credits_do_not_guess_a_version(self):
        session = 'Claude-Session: https://claude.ai/code/session_123'
        self.assertEqual(FUNCTIONS['model_credits'](session), {('Anthropic', 'Claude', '')})
        explicit = session + '\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
        self.assertEqual(FUNCTIONS['model_credits'](explicit), {('Anthropic', 'Claude Opus', '5.5')})

    def test_dates_are_normalized_before_sorting(self):
        self.assertEqual(FUNCTIONS['utc']('2026-10-03T09:31:41-07:00'), '2026-10-03T16:31:41Z')

    def test_human_trailers_and_other_domains_are_not_models(self):
        message = '''Co-Authored-By: Some Person <person@example.com>
Co-Authored-By: Claude Opus 5.5 <noreply@example.com>
Co-Authored-By: Human <human@openai.com>'''
        self.assertEqual(FUNCTIONS['model_credits'](message), set())

    def test_opt_in_requires_every_entry_line_to_be_self_authored(self):
        own, other = 'a' * 40, 'b' * 40
        blame = f'''{own} 1 1 2
\t[people.alice]
{own} 2 2
\tdisplay_name = "Chosen name"
{other} 3 3 2
\t[people.bob]
{own} 4 4
\treal_name = "Someone else's addition"
'''
        identities = {
            own: {'author': {'user': {'login': 'alice'}}},
            other: {'author': {'user': {'login': 'bob'}}},
        }
        fn = FUNCTIONS['verified_entries']
        with patch.dict(fn.__globals__, git=lambda *args: blame):
            self.assertEqual(fn({'alice': {}, 'bob': {}, 'missing': {}}, identities), {'alice'})

    def test_refresh_separates_model_authors_and_unattributed_commits(self):
        human, model, unknown = 'a' * 40, 'b' * 40, 'c' * 40
        merge = 'd' * 40
        commits = [human, model, unknown, merge]
        def git(*args):
            if args[:2] == ('rev-parse', '--is-shallow-repository'):
                return 'false'
            if args[0] == 'status':
                return ''
            if args[0] == 'rev-list':
                return '\n'.join(commits[:3] if '--no-merges' in args else commits)
            if args[0] == 'branch':
                return 'main'
            if args[0] == 'rev-parse':
                return unknown
            if '--numstat' in args:
                return '2\t1\tcode.rs\0'
            if '--format=%an <%ae>' in args:
                return 'Claude <noreply@anthropic.com>' if args[-1] == model else 'Person <person@example.com>'
            if '--format=%B' in args:
                return 'A change'
            return '2026-10-03T00:00:00Z'
        data = {}
        for i, login in enumerate(['alice', 'claude', None, 'merger']):
            data[f'c{i}'] = dict(author=dict(user=dict(login=login) if login else None),
                associatedPullRequests=dict(nodes=[], pageInfo=dict(hasNextPage=False)))
        data['c1']['authors'] = dict(nodes=[
            dict(name='Claude', email='noreply@anthropic.com', user=dict(login='claude')),
            dict(name='Carol', email='carol@example.com', user=dict(login='carol')),
        ], pageInfo=dict(hasNextPage=False))
        data['c3']['authors'] = dict(nodes=[
            dict(name='Merger', email='merger@example.com', user=dict(login='merger')),
        ], pageInfo=dict(hasNextPage=False))
        fn = FUNCTIONS['refresh']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'contributors').mkdir()
            (root / 'contributors/people.toml').write_text('')
            with patch.dict(fn.__globals__, ROOT=root, git=git, github=lambda query: data,
                            verified_entries=lambda people, identities: set()), contextlib.redirect_stdout(io.StringIO()):
                fn('owner/repo', 'refs/remotes/upstream/main')
            result = json.loads((root / 'contributors/contributors.json').read_text())
        self.assertEqual([c['login'] for c in result['contributors']], ['alice', 'carol', 'merger'])
        self.assertEqual(result['contributors'][-1]['commits'], 0)
        self.assertEqual(result['branch'], 'refs/remotes/upstream/main')
        self.assertEqual(result['head'], unknown)
        self.assertEqual(result['commits'], 3)
        self.assertEqual(result['unattributed_commits'], 1)
        self.assertEqual([(m['model'], m['commits']) for m in result['models']], [('Claude', 1)])

    def test_unresolved_identity_does_not_verify_names(self):
        sha = 'a' * 40
        fn = FUNCTIONS['verified_entries']
        with patch.dict(fn.__globals__, git=lambda *args: f'{sha} 1 1\n\t[people.alice]'):
            self.assertEqual(fn({'alice': {}}, {sha: {'author': {'user': None}}}), set())


if __name__ == '__main__':
    unittest.main()
