import os
import unittest

os.environ.setdefault("GSM_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.config import settings


class ConfigDefaultsTests(unittest.TestCase):
    def test_file_search_defaults(self):
        self.assertEqual(settings.file_search_max_results, 100)
        self.assertEqual(settings.file_search_max_depth, 10)
        self.assertEqual(settings.file_search_max_file_size, 1 * 1024 * 1024)

    def test_buffer_and_upload_defaults(self):
        self.assertEqual(settings.max_log_buffer_lines, 500)
        self.assertEqual(settings.max_upload_size_mb, 50)
        self.assertEqual(settings.max_extract_size_gb, 20)
        self.assertEqual(settings.max_extract_files, 10000)

    def test_notification_event_defaults(self):
        self.assertEqual(
            settings.smtp_events,
            ["crash", "backup_failed", "start", "stop"],
        )
        self.assertEqual(
            settings.discord_events,
            ["start", "stop", "crash", "backup"],
        )
        self.assertEqual(
            settings.telegram_events,
            ["start", "stop", "crash", "backup", "high_cpu", "high_memory"],
        )


if __name__ == "__main__":
    unittest.main()
