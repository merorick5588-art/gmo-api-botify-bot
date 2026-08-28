from pathlib import Path
import unittest


class GitHubActionsTests(unittest.TestCase):
    def test_workflow_is_dispatch_only_and_has_required_secrets(self):
        text = Path('.github/workflows/api_check.yml').read_text(encoding='utf-8')
        self.assertIn('workflow_dispatch:', text)
        self.assertNotIn('schedule:', text)
        for name in (
            'OPENAI_API_KEY', 'GMO_FX_API_KEY', 'GMO_FX_API_SECRET',
            'DISCORD_FOREX_MAIN', 'DISCORD_FOREX_OTHER',
        ):
            self.assertIn(f'secrets.{name}', text)
        self.assertIn('pip install -r requirements.txt', text)
        self.assertIn('python run_bot.py --symbols_file symbols.csv', text)

    def test_workflow_persists_state_on_github_hosted_runner(self):
        text = Path('.github/workflows/api_check.yml').read_text(encoding='utf-8')
        self.assertIn('uses: actions/cache@v4', text)
        self.assertIn('path: state', text)
        self.assertIn('BOT_STATE_DB: state/fxbot.sqlite3', text)


if __name__ == '__main__':
    unittest.main()
