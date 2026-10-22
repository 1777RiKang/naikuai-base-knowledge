"""Scoring and attribution rules; network calls are mocked."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'build'))
import leaderboard


def document(entry_id, status='confirmed'):
    return {'id': entry_id, 'review': {'status': status},
            'contributors': [{'name': '自选贡献名', 'role': 'annotator'}]}


class LeaderboardTest(unittest.TestCase):
    def test_repository_configuration(self):
        leaderboard.load_profiles(leaderboard.ROOT / 'credits/display-names.json')
        leaderboard.load_seed_attribution(leaderboard.ROOT / 'credits/m0-seed-attribution.json')
        seeds = json.loads((leaderboard.ROOT / 'credits/m0-seed-ids.json').read_text(encoding='utf-8'))
        self.assertIsInstance(seeds, list)
        self.assertEqual(len(seeds), len(set(seeds)))
        self.assertTrue(all(isinstance(entry_id, str) for entry_id in seeds))

    def test_seed_status_dedup_and_author_identity(self):
        docs = [(Path('a'), document('item.seed')),
                (Path('b'), document('item.a')),
                (Path('c'), document('item.b')),
                (Path('d'), document('item.c', 'draft')),
                (Path('e'), document('item.d', 'disputed')),
                (Path('f'), document('item.e', 'deprecated'))]
        calls = []

        def origin(path, doc):
            calls.append(doc['id'])
            return {'github_user_id': 42, 'display_name': '自选贡献名', 'merged_at': '2026-10-22'}

        seed_attribution = {'github_user_id': 42, 'display_name': '种子贡献者', 'recorded_on': '2026-10-22'}
        board = leaderboard.calculate(docs, {'item.seed'}, {}, origin, seed_attribution)
        self.assertEqual(calls, ['item.a', 'item.b'])
        self.assertEqual(board['total_points'], 3)
        self.assertEqual(len(board['contributors']), 1)
        self.assertEqual(board['contributors'][0]['entry_ids'], ['item.a', 'item.b', 'item.seed'])
        with self.assertRaises(ValueError):
            leaderboard.calculate(docs + [docs[1]], {'item.seed'}, {}, origin, seed_attribution)

    def test_pending_seeds_are_attributed_and_score_after_confirmation(self):
        docs = [(Path('a'), document('item.a')),
                (Path('b'), document('item.b', 'draft')),
                (Path('c'), document('item.c', 'disputed')),
                (Path('d'), document('item.d', 'deprecated'))]
        attribution = {'github_user_id': 42, 'display_name': '种子贡献者', 'recorded_on': '2026-10-22'}

        def no_pr_lookup(path, doc):
            self.fail('种子词条应使用指定归属，不查找不存在的初始 PR')

        seeds = {doc['id'] for _, doc in docs}
        board = leaderboard.calculate(docs, seeds, {'42': '自选公开名'}, no_pr_lookup, attribution)
        self.assertEqual(board['total_points'], 1)
        self.assertEqual(board['seed_entries_excluded'], 0)
        self.assertEqual(board['seed_entries_attributed'], 4)
        self.assertEqual(board['seed_entries_pending'], 2)
        person = board['contributors'][0]
        self.assertEqual(person['display_name'], '自选公开名')
        self.assertEqual(person['pending_entry_ids'], ['item.b', 'item.c'])
        self.assertEqual(person['total_entries'], 3)
        docs[1][1]['review']['status'] = 'confirmed'
        board = leaderboard.calculate(docs, seeds, {}, no_pr_lookup, attribution)
        self.assertEqual(board['total_points'], 2)
        self.assertEqual(board['seed_entries_pending'], 1)

    def test_seed_credit_requires_explicit_attribution_and_complete_registry(self):
        with self.assertRaises(ValueError):
            leaderboard.calculate([(Path('a'), document('item.a'))], {'item.a'}, {}, lambda *_: {})
        with self.assertRaises(ValueError):
            leaderboard.calculate([], {'item.missing'}, {}, lambda *_: {})

    def test_same_name_is_not_same_identity_and_ties_share_rank(self):
        def origin(path, doc):
            return {'github_user_id': int(path.name), 'display_name': '同名贡献者', 'merged_at': '2026-10-22'}
        board = leaderboard.calculate([(Path('1'), document('item.a')), (Path('2'), document('item.b'))],
                                      set(), {'1': '自选新名称'}, origin)
        self.assertEqual([r['rank'] for r in board['contributors']], [1, 1])
        self.assertEqual(board['contributors'][0]['display_name'], '自选新名称')

    def test_rendering_escapes_markup(self):
        board = {'source_commit': 'local', 'built_at': '2026-10-22T00:00:00+00:00',
                 'contributors': [{'rank': 1, 'display_name': '<script>&|[链接](地址)',
                                   'github_user_id': 42, 'points': 1}]}
        rendered = leaderboard.svg(board)
        ET.fromstring(rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<script>', leaderboard.markdown(board))
        self.assertIn('&#124;', leaderboard.markdown(board))
        snapshot = leaderboard.readme_snapshot(board)
        self.assertNotIn('<script>', snapshot)
        self.assertIn('&#124;', snapshot)
        original = '介绍\n' + leaderboard.SNAPSHOT_START + '\n旧表\n' + leaderboard.SNAPSHOT_END + '\n其他内容\n'
        updated = leaderboard.update_readme_snapshot(original, snapshot)
        self.assertTrue(updated.startswith('介绍\n'))
        self.assertTrue(updated.endswith('\n其他内容\n'))
        self.assertNotIn('旧表', updated)
        self.assertEqual(updated, leaderboard.update_readme_snapshot(updated, snapshot))
        with self.assertRaises(ValueError):
            leaderboard.update_readme_snapshot('缺少标记', snapshot)

    def test_origin_uses_original_pr_author_not_merger(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
            git('init', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            path = root / 'terms/item/example.yaml'
            path.parent.mkdir(parents=True)
            original = document('item.example', 'draft')
            path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
            git('add', '.')
            git('commit', '-m', 'Introduce draft')
            first_commit = git('rev-parse', 'HEAD').stdout.decode().strip()
            current = document('item.example')
            current['contributors'][0]['name'] = '后续编辑者'
            path.write_text(json.dumps(current, ensure_ascii=False), encoding='utf-8')
            git('add', '.')
            git('commit', '-m', 'Confirm')
            pulls = [{'number': 1, 'merged_at': '2026-10-22', 'user': {'id': 42},
                      'merged_by': {'id': 43}, 'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}}}]
            with patch.object(leaderboard, 'ROOT', root), patch.object(leaderboard, 'github_api', return_value=pulls) as api:
                origin = leaderboard.GitHubOrigins('owner/repo')(path, current)
                self.assertEqual(origin['github_user_id'], 42)
                self.assertEqual(origin['display_name'], '自选贡献名')
                self.assertIn(first_commit, api.call_args.args[1])

    def test_unverified_origin_fails_instead_of_faking_points(self):
        with patch.object(leaderboard, 'git', return_value='abc'), patch.object(leaderboard, 'github_api', return_value=[]):
            with self.assertRaises(ValueError):
                leaderboard.GitHubOrigins('owner/repo')(leaderboard.ROOT / 'terms/item/new.yaml', document('item.new'))


if __name__ == '__main__':
    unittest.main()
