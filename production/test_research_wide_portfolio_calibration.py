import unittest

import numpy as np
import pandas as pd
from scipy.special import expit

from research_wide_portfolio_calibration import calibration_report, fit_calibrator, logits


class WideCalibrationTests(unittest.TestCase):
    def test_intercept_recovers_misspecified_average(self):
        p = np.full(100, 0.25)
        y = np.arange(100) < 40
        fitted = fit_calibrator(p, y, "intercept")
        self.assertAlmostEqual(fitted["slope"], 1)
        self.assertAlmostEqual(float(expit(fitted["offset"] + logits([0.25]))[0]), 0.40, places=5)

    def test_fit_is_independent_of_evaluation_labels(self):
        def sample(year):
            return pd.DataFrame({
                "model": "coherentV2", "policy": "maximumAnyHit",
                "raceID": [str(i) for i in range(30)], "date": pd.Timestamp(f"{year}-09-01"),
                "probabilityAnyHit": np.linspace(0.1, 0.6, 30), "hit": np.arange(30) % 3 == 0,
            })
        train, test = sample(2025), sample(2026)
        def bootstrap(*_):
            return None

        _, first = calibration_report(train, test, bootstrap)
        test["hit"] = ~test["hit"]
        _, second = calibration_report(train, test, bootstrap)
        np.testing.assert_array_equal(first["interceptSlope"], second["interceptSlope"])
        with self.assertRaisesRegex(ValueError, "periods"):
            calibration_report(test, train, bootstrap)


if __name__ == "__main__":
    unittest.main()
