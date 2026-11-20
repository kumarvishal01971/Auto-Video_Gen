import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import store


class TrashStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.jobs_dir = root / "jobs"
        self.output_dir = root / "output"
        self.jobs_dir.mkdir()
        self.output_dir.mkdir()
        self.patches = (
            patch.object(store, "DB_PATH", root / "pipeline.db"),
            patch.object(store, "JOBS_DIR", self.jobs_dir),
            patch.object(store, "OUTPUT_DIR", self.output_dir),
        )
        for item in self.patches:
            item.start()
        store.init()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def create_job(self):
        job_id = store.create({"topic": "Test"})
        return job_id, self.jobs_dir / job_id

    def test_purge_requires_trash_and_removes_only_managed_files(self):
        job_id, job_path = self.create_job()
        published = self.output_dir / "published"
        published.mkdir()
        store.update(job_id, state={"published_to": str(published)})

        with self.assertRaisesRegex(ValueError, "Move the video to Trash"):
            store.purge(job_id)

        store.trash(job_id)
        store.purge(job_id)

        self.assertIsNone(store.get(job_id))
        self.assertFalse(job_path.exists())
        self.assertFalse(published.exists())

    def test_purge_refuses_published_path_outside_output_directory(self):
        job_id, job_path = self.create_job()
        outside = Path(self.temp.name) / "keep"
        outside.mkdir()
        store.update(job_id, state={"published_to": str(outside)})
        store.trash(job_id)

        with self.assertRaisesRegex(ValueError, "outside the output directory"):
            store.purge(job_id)

        self.assertTrue(outside.exists())
        self.assertTrue(job_path.exists())
        self.assertIsNotNone(store.get(job_id))


if __name__ == "__main__":
    unittest.main()
