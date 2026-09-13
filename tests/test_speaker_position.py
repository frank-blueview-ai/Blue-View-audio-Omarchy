# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import unittest
from test_audio_controls import CONTROLS

class SpeakerPositionTests(unittest.TestCase):
    def gains(self, balance, fade):
        return [CONTROLS.speaker_position_gain(s, {'speakerBalance': balance, 'speakerFade': fade})
                for s in CONTROLS.DEFAULT_MASTER_MAP]

    def test_center_and_endpoints_do_not_boost(self):
        for position, expected in [((0,0),[1,1,1,1]), ((-100,0),[1,0,1,0]),
                                   ((100,0),[0,1,0,1]), ((0,-100),[1,1,0,0]),
                                   ((0,100),[0,0,1,1])]:
            self.assertEqual(self.gains(*position),expected)

    def test_two_axes_combine_without_affecting_saved_eq_or_mutes(self):
        self.assertEqual(self.gains(50,-50),[.5,1,.25,.5])
        state = {**CONTROLS.DEFAULT_STATE, 'speakerBalance':50, 'speakerFade':-50}
        before = repr(state)
        graph = CONTROLS.eq_filter_graph(state)
        self.assertEqual(repr(state),before)
        self.assertIn('name = blue_view_position_3 label = linear control = { Mult = 0.250000',graph)
        self.assertIn('input = "blue_view_position_1:In"',graph)
        self.assertIn('outputs = [ "blue_view_position_1:Out"',graph)

    def test_balance_survives_eq_bypass(self):
        state = {**CONTROLS.DEFAULT_STATE, 'speakerBalance':100, 'eqEnabled':False}
        graph = CONTROLS.eq_filter_graph(state)
        self.assertIn('name = blue_view_position_1 label = linear control = { Mult = 0.000000',graph)
        self.assertIn('name = blue_view_position_2 label = linear control = { Mult = 1.000000',graph)

if __name__ == '__main__': unittest.main()
