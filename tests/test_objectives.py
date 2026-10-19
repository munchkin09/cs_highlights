import unittest

from cs_highlights.analysis import normalize
from cs_highlights.errors import HighlightsError
from cs_highlights.selection import select_kills
from helpers import ENEMY, PLAYER, kill, raw_match


def clutch(tick=500, round_number=1, opponents=2, won=True, player=PLAYER):
    return {'tick': tick, 'roundNumber': round_number, 'clutcherSteamId': player,
            'opponentCount': opponents, 'won': won}


def defuse(tick=1900, round_number=1, player=PLAYER):
    return {'tick': tick, 'roundNumber': round_number, 'defuserSteamId': player}


class ObjectiveSelectionTests(unittest.TestCase):
    def select(self, raw, before=1, after=1):
        return select_kills(normalize(raw), PLAYER, before, after)

    def test_won_one_vs_two_through_five_covers_whole_play_and_resolution(self):
        for count in range(2, 6):
            with self.subTest(count=count):
                raw = raw_match([kill(900), kill(1800)])
                raw['clutches'] = [clutch(opponents=count)]
                clips = self.select(raw)
                self.assertEqual(len(clips), 1)
                self.assertEqual((clips[0].start_tick, clips[0].end_tick), (436, 2064))
                self.assertEqual(clips[0].kill_ticks, (900, 1800))
                self.assertIn('won_clutch', clips[0].reason)

    def test_lost_one_vs_one_other_player_and_outside_round_do_not_expand(self):
        for event in (clutch(won=False), clutch(opponents=1), clutch(opponents=6),
                      clutch(player=ENEMY), clutch(tick=50), clutch(round_number=0)):
            with self.subTest(event=event):
                raw = raw_match([kill(900)])
                raw['clutches'] = [event]
                self.assertEqual(self.select(raw), self.select(raw_match([kill(900)])))

    def test_won_clutch_without_kills_is_still_a_highlight(self):
        raw = raw_match([])
        raw['clutches'] = [clutch()]
        clip, = self.select(raw)
        self.assertEqual(clip.reason, 'won_clutch')
        self.assertEqual(clip.kill_ticks, ())

    def test_defuse_extends_last_kill_over_long_gap_and_keeps_prior_clip(self):
        raw = raw_match([kill(300), kill(900)])
        raw['bombsDefused'] = [defuse()]
        clips = self.select(raw)
        self.assertEqual([(c.start_tick, c.end_tick) for c in clips], [(236, 364), (836, 1964)])
        self.assertIn('defuse', clips[-1].reason)

    def test_defuse_after_multiple_nearby_kills_merges_without_duplicate_events(self):
        raw = raw_match([kill(800), kill(900)])
        raw['bombsDefused'] = [defuse()]
        clip, = self.select(raw)
        self.assertEqual(clip.kill_ticks, (800, 900))
        self.assertEqual(clip.end_tick, 1964)

    def test_defuse_requires_own_preceding_kill_in_same_round(self):
        for event in (defuse(player=ENEMY), defuse(tick=800), defuse(tick=6000, round_number=2),
                      defuse(tick=2150), defuse(round_number=0)):
            with self.subTest(event=event):
                raw = raw_match([kill(900)])
                raw['bombsDefused'] = [event]
                self.assertEqual(self.select(raw), self.select(raw_match([kill(900)])))
        raw = raw_match([])
        raw['bombsDefused'] = [defuse()]
        self.assertEqual(self.select(raw), [])

    def test_clutch_and_defuse_coalesce_and_stay_within_round(self):
        raw = raw_match([kill(900)])
        raw['clutches'] = [clutch(), clutch()]
        raw['bombsDefused'] = [defuse(tick=2000)]
        clip, = self.select(raw, before=100, after=100)
        self.assertEqual((clip.start_tick, clip.end_tick), (100, 2100))
        self.assertEqual(clip.kill_ticks, (900,))
        self.assertEqual(set(clip.reason.split('+')), {'enemy_kills', 'won_clutch', 'defuse'})

    def test_zero_postroll_includes_defuse_and_round_resolution_ticks(self):
        raw = raw_match([kill(900)])
        raw['bombsDefused'] = [defuse()]
        self.assertEqual(self.select(raw, after=0)[0].end_tick, 1901)
        raw['clutches'] = [clutch()]
        self.assertEqual(self.select(raw, after=0)[0].end_tick, 2001)

    def test_optional_events_are_validated_and_exported(self):
        raw = raw_match()
        raw['clutches'] = [clutch()]
        raw['bombsDefused'] = [defuse()]
        normalized = normalize(raw)
        self.assertEqual(normalized.to_dict()['schema_version'], 2)
        self.assertEqual(normalized.defuses[0].player_steam_id, PLAYER)
        for field, value in [('won', 'false'), ('opponentCount', '2'), ('tick', None)]:
            with self.subTest(field=field), self.assertRaises(HighlightsError):
                normalize({**raw, 'clutches': [{**clutch(), field: value}]})

    def test_final_defuse_is_included_without_official_round_end(self):
        raw = raw_match([kill(6000, 2)])
        raw['rounds'][1]['endOfficiallyTick'] = 0
        raw['bombsDefused'] = [defuse(tick=6900, round_number=2)]
        clip, = self.select(raw)
        self.assertEqual(clip.end_tick, 6901)
        self.assertIn('defuse', clip.reason)
