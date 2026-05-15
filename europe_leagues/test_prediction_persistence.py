import unittest
from unittest.mock import patch

from domain.persistence import PredictionPersistenceService


class PredictionPersistenceSideEffectTest(unittest.TestCase):
    def test_persist_prediction_batch_only_refreshes_accuracy(self):
        class DummyResultManager:
            def __init__(self):
                self.refresh_count = 0

            def update_accuracy_stats(self):
                self.refresh_count += 1
                return {"overall": {}}

        manager = DummyResultManager()
        service = PredictionPersistenceService(base_dir="/tmp", cache=None, result_manager=manager)

        with patch.object(manager, "update_accuracy_stats", wraps=manager.update_accuracy_stats) as mock_refresh:
            service.persist_prediction_batch([
                {"match_id": "a"},
                {"match_id": "b"},
            ], "premier_league")

        self.assertEqual(manager.refresh_count, 1)
        mock_refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
