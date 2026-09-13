# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import math
import unittest

from e2e_acoustic_probe import BLOCK, FREQ, RATE, tone_db


class AcousticMeasurementTests(unittest.TestCase):
    def test_known_sine_has_calibrated_rms_and_rejects_adjacent_frequency(self):
        samples = [round(2147483648 * .01 * math.sin(2*math.pi*FREQ*n/RATE))
                   for n in range(BLOCK)]
        self.assertAlmostEqual(tone_db(samples), -43.0103, places=3)
        self.assertLess(tone_db(samples, FREQ+50), -120)

    def test_silence_cannot_be_reported_as_signal(self):
        self.assertEqual(tone_db([0] * BLOCK), -240)

    def test_doubling_amplitude_changes_measured_level_by_six_db(self):
        samples = [round(2147483648 * .001 * math.sin(2*math.pi*FREQ*n/RATE))
                   for n in range(BLOCK)]
        self.assertAlmostEqual(tone_db([2*s for s in samples])-tone_db(samples), 6.0206, places=3)
