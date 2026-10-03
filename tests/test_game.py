"""Regression tests for the text adventure.

The world tile mapping (resources/map.txt -> world._world) is treated as the
contract: every room must be reachable, failure paths must not corrupt state,
and a save/load round trip must keep rooms and inventory consistent.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import actions, enemies, items, tiles, world
import savegame
from player import Player


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def inventory_signature(player):
    sig = []
    for item in player.inventory:
        entry = [type(item).__name__]
        if isinstance(item, items.Gold):
            entry.append(item.amt)
        if isinstance(item, items.Weapon):
            entry.append(item.damage)
        sig.append(tuple(entry))
    return sorted(sig)


def world_signature():
    sig = {}
    for coord, tile in world._world.items():
        if isinstance(tile, tiles.EnemyRoom):
            sig[coord] = ("enemy", tile.enemy.hp)
        elif isinstance(tile, tiles.LootRoom):
            sig[coord] = ("loot", tile.item is None)
        elif tile is not None:
            sig[coord] = ("plain",)
    return sig


class WorldContractTest(unittest.TestCase):
    def setUp(self):
        world.load_tiles()

    def test_tile_mapping_matches_map_file(self):
        with open(world._map_path) as f:
            rows = [line.replace("\n", "").split("\t") for line in f.readlines()]
        expected = {}
        x_max = len(rows[0])  # world.load_tiles derives column count from row 0
        for y, cols in enumerate(rows):
            for x in range(x_max):
                name = cols[x] if x < len(cols) else ""
                expected[(x, y)] = name or None
        self.assertEqual(set(expected), set(world._world))
        for coord, name in expected.items():
            tile = world._world[coord]
            if name is None:
                self.assertIsNone(tile, coord)
            else:
                self.assertEqual(type(tile).__name__, name, coord)
                self.assertEqual((tile.x, tile.y), coord)

    def test_starting_position_matches_map(self):
        tile = world._world[world.starting_position]
        self.assertIsInstance(tile, tiles.StartingRoom)

    def test_all_rooms_reachable(self):
        start = world.starting_position
        visited = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (x + dx, y + dy)
                if nxt not in visited and world.tile_exists(*nxt) is not None:
                    visited.add(nxt)
                    stack.append(nxt)
        all_rooms = {c for c, t in world._world.items() if t is not None}
        self.assertEqual(visited, all_rooms)

    def test_adjacent_moves_stay_on_map(self):
        for coord, tile in world._world.items():
            if tile is None:
                continue
            for action in tile.adjacent_moves():
                dx, dy = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0)}[action.hotkey]
                self.assertIsNotNone(world.tile_exists(coord[0] + dx, coord[1] + dy))


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        world.load_tiles()
        self.player = Player()

    def test_move_out_of_bounds_is_safe_noop(self):
        self.player.location_x, self.player.location_y = 3, 0  # LeaveCaveRoom, map edge
        before_inv = inventory_signature(self.player)
        for move in (self.player.move_north, self.player.move_east, self.player.move_west):
            quiet(move)
            self.assertEqual((self.player.location_x, self.player.location_y), (3, 0))
            self.assertEqual(self.player.hp, 100)
            self.assertEqual(inventory_signature(self.player), before_inv)

    def test_move_into_void_inside_map_is_safe_noop(self):
        self.player.location_x, self.player.location_y = 2, 4  # StartingRoom
        self.player.location_x, self.player.location_y = 1, 1  # Find5GoldRoom
        quiet(self.player.move_west)  # (0, 1) is empty
        self.assertEqual((self.player.location_x, self.player.location_y), (1, 1))


class LootRoomTest(unittest.TestCase):
    def setUp(self):
        world.load_tiles()
        self.player = Player()

    def test_loot_given_only_once(self):
        room = world.tile_exists(4, 4)  # FindDaggerRoom
        before = len(self.player.inventory)
        quiet(room.modify_player, self.player)
        quiet(room.modify_player, self.player)  # game loop calls this every turn
        self.assertEqual(len(self.player.inventory), before + 1)

    def test_item_not_in_room_and_inventory_at_once(self):
        room = world.tile_exists(4, 4)
        quiet(room.modify_player, self.player)
        self.assertIsNone(room.item)
        self.assertEqual(sum(isinstance(i, items.Dagger) for i in self.player.inventory), 1)


class EnemyRoomTest(unittest.TestCase):
    def setUp(self):
        world.load_tiles()
        self.player = Player()
        self.room = world.tile_exists(0, 4)  # GiantSpiderRoom
        quiet(self.player.attack, self.room.enemy)
        quiet(self.player.attack, self.room.enemy)  # Rock: 5 dmg x2 vs 10 HP
        self.assertFalse(self.room.enemy.is_alive())

    def test_dead_enemy_deals_no_damage(self):
        hp_before = self.player.hp
        quiet(self.room.modify_player, self.player)
        self.assertEqual(self.player.hp, hp_before)

    def test_dead_enemy_offers_no_combat_actions(self):
        hotkeys = [a.hotkey for a in self.room.available_actions()]
        self.assertNotIn("a", hotkeys)
        self.assertNotIn("f", hotkeys)
        self.assertIn("i", hotkeys)  # normal room actions, incl. ViewInventory

    def test_live_enemy_forces_combat(self):
        room = world.tile_exists(3, 1)  # the other GiantSpiderRoom, still alive
        hotkeys = [a.hotkey for a in room.available_actions()]
        self.assertEqual(sorted(hotkeys), ["a", "f"])


class VictoryTest(unittest.TestCase):
    def setUp(self):
        world.load_tiles()
        self.player = Player()
        self.room = world.tile_exists(3, 0)  # LeaveCaveRoom

    def test_no_victory_without_quest_item(self):
        quiet(self.room.modify_player, self.player)
        self.assertFalse(self.player.victory)

    def test_victory_with_quest_item(self):
        self.player.inventory.append(items.Dagger())
        quiet(self.room.modify_player, self.player)
        self.assertTrue(self.player.victory)


class SaveLoadTest(unittest.TestCase):
    def setUp(self):
        world.load_tiles()
        self.player = Player()
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _play_a_bit(self):
        spider = world.tile_exists(0, 4)
        quiet(self.player.attack, spider.enemy)
        quiet(self.player.attack, spider.enemy)  # dead
        quiet(world.tile_exists(4, 4).modify_player, self.player)  # take dagger
        self.player.location_x, self.player.location_y = 2, 6
        self.player.hp = 42

    def test_round_trip_keeps_rooms_and_inventory_consistent(self):
        self._play_a_bit()
        savegame.save_game(self.player, self.path)
        expected_inv = inventory_signature(self.player)

        world.load_tiles()  # simulate a fresh process / mutated world
        loaded = savegame.load_game(self.path)

        self.assertEqual((loaded.location_x, loaded.location_y), (2, 6))
        self.assertEqual(loaded.hp, 42)
        self.assertEqual(inventory_signature(loaded), expected_inv)
        self.assertFalse(world.tile_exists(0, 4).enemy.is_alive())
        self.assertIsNone(world.tile_exists(4, 4).item)
        self.assertEqual(sum(isinstance(i, items.Dagger) for i in loaded.inventory), 1)

    def test_missing_save_fails_cleanly(self):
        os.unlink(self.path)
        before = world_signature()
        with self.assertRaises(savegame.SaveGameError):
            savegame.load_game(self.path)
        self.assertEqual(world_signature(), before)

    def test_corrupt_save_fails_cleanly(self):
        with open(self.path, "w") as f:
            f.write("{ not json")
        before = world_signature()
        with self.assertRaises(savegame.SaveGameError):
            savegame.load_game(self.path)
        self.assertEqual(world_signature(), before)

    def test_invalid_save_contents_fail_cleanly(self):
        bad_saves = [
            {"player": {"hp": "x", "location": [0, 0], "victory": False, "inventory": []}},
            {"player": {"hp": 1, "location": [0, 1], "victory": False, "inventory": []}},  # void tile
            {"player": {"hp": 1, "location": [0, 0], "victory": False,
                        "inventory": [{"type": "LaserGun"}]}},
            {"player": {"hp": 1, "location": [0, 0], "victory": False, "inventory": []},
             "tiles": {"1,1": {"enemy_hp": 5}}},  # 1,1 is a loot room, not enemy room
        ]
        for bad in bad_saves:
            with open(self.path, "w") as f:
                json.dump(bad, f)
            before = world_signature()
            with self.assertRaises(savegame.SaveGameError):
                savegame.load_game(self.path)
            self.assertEqual(world_signature(), before)


class NarrativeFreezeTest(unittest.TestCase):
    """Room intro texts are narrative content and must not change."""

    def setUp(self):
        world.load_tiles()

    def test_intro_texts_unchanged(self):
        start = world._world[world.starting_position]
        self.assertEqual(start.intro_text(), """
        You find yourself in a cave with a flickering torch on the wall.
        You can make out four paths, each equally as dark and foreboding.
        """)
        self.assertEqual(world.tile_exists(1, 1).intro_text(), """
        Someone dropped a 5 gold piece. You pick it up.
        """)
        self.assertEqual(world.tile_exists(3, 0).intro_text(), """
        You see a bright light in the distance...
        ... it grows as you get closer! It's sunlight!


        Victory is yours!
        """)
        spider = world.tile_exists(0, 4)
        self.assertEqual(spider.intro_text(), """
            A giant spider jumps down from its web in front of you!
            """)
        spider.enemy.hp = 0
        self.assertEqual(spider.intro_text(), """
            The corpse of a dead spider rots on the ground.
            """)


class EndToEndTest(unittest.TestCase):
    def test_full_playthrough_reaches_victory(self):
        import game
        # start(2,4) -> dagger(4,4) -> back -> north corridor -> spider(3,1)
        # -> kill with dagger -> LeaveCaveRoom(3,0)
        inputs = iter(["e", "e", "w", "w", "n", "n", "n", "e", "a", "n"])
        out = io.StringIO()
        with mock.patch("builtins.input", lambda _prompt="": next(inputs)):
            with contextlib.redirect_stdout(out):
                game.play()
        self.assertIn("Victory is yours!", out.getvalue())
        self.assertIn("You killed Giant Spider!", out.getvalue())


if __name__ == "__main__":
    unittest.main()
