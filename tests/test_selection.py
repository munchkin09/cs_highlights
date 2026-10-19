import unittest

from cs_highlights.analysis import normalize
from cs_highlights.errors import HighlightsError
from cs_highlights.selection import select_kills
from helpers import PLAYER, kill, raw_match


class SelectionTests(unittest.TestCase):
    def select(self, raw, before=5, after=3):
        return select_kills(normalize(raw), PLAYER, before, after)

    def test_overlaps_are_merged_and_side_switches_use_event_sides(self):
        clips = self.select(raw_match())
        self.assertEqual([(c.start_tick, c.end_tick) for c in clips], [(320, 992), (1480, 1992), (3680, 4192)])
        self.assertEqual(clips[0].kill_ticks, (640, 800))
        self.assertEqual(clips[-1].round_number, 2)

    def test_round_bounds_and_last_round_fallback(self):
        raw = raw_match([kill(110), kill(2090), kill(6899, 2)])
        raw["rounds"][-1]["endOfficiallyTick"] = 0
        clips = self.select(raw)
        self.assertEqual(clips[0].start_tick, 100)
        self.assertEqual(clips[1].end_tick, 2100)
        self.assertEqual(clips[-1].end_tick, 6901)

    def test_warmup_suicides_teamkills_and_spectators_excluded(self):
        raw = raw_match([kill(50), kill(500, 0), kill(600, victim=PLAYER), kill(700, victim_side=3),
                         kill(800, killer_side=1), {**kill(900), "isWarmup": True}])
        self.assertEqual(self.select(raw), [])

    def test_duplicate_events_removed_but_collateral_preserved(self):
        raw = raw_match([kill(640), kill(640), kill(640, victim="76561198000000003")])
        self.assertEqual(self.select(raw)[0].kill_ticks, (640, 640))

    def test_never_merge_across_rounds(self):
        clips = self.select(raw_match([kill(2000), kill(3001, 2)]), 100, 100)
        self.assertEqual(len(clips), 2)
        self.assertLessEqual(clips[0].end_tick, clips[1].start_tick)

    def test_zero_padding_still_contains_event(self):
        clip = self.select(raw_match([kill(640)]), 0, 0)[0]
        self.assertEqual((clip.start_tick, clip.end_tick), (640, 641))

    def test_no_kills_is_empty_but_missing_player_is_error(self):
        self.assertEqual(self.select(raw_match([])), [])
        raw = raw_match([])
        raw["players"] = []
        with self.assertRaisesRegex(HighlightsError, "no aparece"):
            self.select(raw)

    def test_tickrate_is_read_from_demo(self):
        raw = raw_match([kill(640)])
        raw["tickrate"] = 128
        self.assertEqual(self.select(raw)[0].end_tick, 1024)

    def test_published_csdm_cs2_game_identifier(self):
        raw = raw_match()
        raw["game"] = "CS2"
        self.assertEqual(self.select(raw), self.select(raw_match()))

    def test_strict_schema_does_not_guess_fields(self):
        for field, value in (("tickrate", float("nan")), ("tickCount", 0), ("game", "csgo")):
            with self.subTest(field=field), self.assertRaises(HighlightsError):
                self.select({**raw_match(), field: value})
        raw = raw_match()
        raw["players"][0]["steamId"] = int(PLAYER)
        with self.assertRaises(HighlightsError):
            normalize(raw)

    def test_invalid_rounds_fail_instead_of_dropping_highlights(self):
        raw = raw_match()
        raw["rounds"][1]["freezetimeEndTick"] = 1500
        raw["rounds"][1]["startTick"] = 1500
        with self.assertRaisesRegex(HighlightsError, "solapadas"):
            normalize(raw)
