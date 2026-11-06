import importlib.util
from pathlib import Path
import unittest


class WarmupFilesTest(unittest.TestCase):
    def test_four_files_join_at_endpoints(self):
        path = Path(__file__).resolve().parents[1] / 'trainer.py'
        spec = importlib.util.spec_from_file_location('trainer_warmup_test', path)
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        intervals = [module.warmup_file_interval(0.00001, 0.0007, i, 4) for i in range(1, 5)]
        for actual, expected in zip(intervals, [(0.00001, 0.0001825), (0.0001825, 0.000355),
                                                (0.000355, 0.0005275), (0.0005275, 0.0007)]):
            self.assertAlmostEqual(actual[0], expected[0])
            self.assertAlmostEqual(actual[1], expected[1])


if __name__ == '__main__':
    unittest.main()
