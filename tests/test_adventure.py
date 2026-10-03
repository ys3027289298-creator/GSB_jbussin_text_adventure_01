"""Regression and contract tests for the text adventure.

The world tile mapping (world._world / world.tile_exists) is treated as the
contract: every assertion about rooms, movement and saves goes through it.
"""
import contextlib
import io
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions
import items
import savegame
import tiles
import world
from player import Player


def quiet(callable_, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return callable_(*args, **kwargs)


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        world.load_tiles()
        self.player = Player()

    def tile(self, x, y):
        tile = world.tile_exists(x, y)
        self.assertIsNotNone(tile, "expected a tile at ({}, {})".format(x, y))
        return tile


class WorldContractTest(BaseTestCase):
    def test_starting_position_is_on_map(self):
        self.assertIsNotNone(world.tile_exists(*world.starting_position))

    def test_all_rooms_reachable_from_start(self):
        start = world.starting_position
        visited = {start}
        frontier = [start]
        while frontier:
            x, y = frontier.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (x + dx, y + dy)
                if nxt not in visited and world.tile_exists(*nxt) is not None:
                    visited.add(nxt)
                    frontier.append(nxt)
        on_map = {coord for coord, tile in world._world.items() if tile is not None}
        self.assertEqual(on_map, visited)

    def test_every_room_honours_tile_contract(self):
        for coord, tile in world._world.items():
            if tile is None:
                continue
            with self.subTest(coord=coord):
                self.assertIsInstance(quiet(tile.intro_text), str)
                self.assertTrue(len(tile.available_actions()) > 0)


class MovementTest(BaseTestCase):
    def test_out_of_bounds_move_keeps_position_and_state(self):
        self.player.location_x, self.player.location_y = 0, 4  # west edge
        hp, inventory = self.player.hp, list(self.player.inventory)
        quiet(self.player.move_west)  # (-1, 4) is off the map
        self.assertEqual((self.player.location_x, self.player.location_y), (0, 4))
        self.assertEqual(self.player.hp, hp)
        self.assertEqual(self.player.inventory, inventory)

    def test_move_into_existing_tile_updates_position(self):
        quiet(self.player.move_west)  # (2,4) -> (1,4)
        self.assertEqual((self.player.location_x, self.player.location_y), (1, 4))

    def test_flee_always_lands_on_a_real_tile(self):
        random.seed(1234)
        room = self.tile(3, 1)  # GiantSpiderRoom
        for _ in range(20):
            quiet(self.player.flee, room)
            self.assertIsNotNone(
                world.tile_exists(self.player.location_x, self.player.location_y))
            self.player.location_x, self.player.location_y = 3, 1


class LootTest(BaseTestCase):
    def test_loot_room_grants_item_only_once(self):
        room = self.tile(2, 5)  # Find5GoldRoom
        quiet(room.modify_player, self.player)
        quiet(room.modify_player, self.player)  # revisit
        fives = [g for g in self.player.inventory
                 if isinstance(g, items.Gold) and g.amt == 5]
        self.assertEqual(len(fives), 1)

    def test_picked_up_item_leaves_the_room(self):
        room = self.tile(4, 4)  # FindDaggerRoom
        quiet(room.modify_player, self.player)
        in_inventory = any(isinstance(i, items.Dagger) for i in self.player.inventory)
        self.assertTrue(in_inventory)
        self.assertIsNone(room.item, "item must not exist in room and inventory at once")


class CombatTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.room = self.tile(3, 1)  # GiantSpiderRoom

    def test_dead_enemy_deals_no_damage_on_entry(self):
        self.room.enemy.hp = 0
        hp = self.player.hp
        quiet(self.room.modify_player, self.player)
        self.assertEqual(self.player.hp, hp)

    def test_dead_enemy_offers_no_combat_actions(self):
        self.room.enemy.hp = 0
        offered = {type(a) for a in self.room.available_actions()}
        self.assertNotIn(actions.Attack, offered)
        self.assertNotIn(actions.Flee, offered)
        self.assertIn(actions.ViewInventory, offered)

    def test_attacking_a_corpse_is_a_noop(self):
        self.room.enemy.hp = 0
        quiet(self.player.attack, self.room.enemy)
        self.assertEqual(self.room.enemy.hp, 0)

    def test_live_enemy_fights_back_until_killed(self):
        self.player.inventory.append(items.Dagger())  # 10 dmg vs 10 hp spider
        quiet(self.room.modify_player, self.player)
        self.assertEqual(self.player.hp, 100 - self.room.enemy.damage)
        quiet(self.player.attack, self.room.enemy)
        self.assertFalse(self.room.enemy.is_alive())
        hp = self.player.hp
        quiet(self.room.modify_player, self.player)
        self.assertEqual(self.player.hp, hp, "corpse must not keep fighting")


class VictoryTest(BaseTestCase):
    def test_exit_without_quest_item_grants_no_victory(self):
        quiet(self.tile(3, 0).modify_player, self.player)  # LeaveCaveRoom
        self.assertFalse(self.player.victory)

    def test_exit_with_quest_item_grants_victory(self):
        self.player.inventory.append(items.Dagger())
        quiet(self.tile(3, 0).modify_player, self.player)
        self.assertTrue(self.player.victory)


class FailurePathTest(BaseTestCase):
    def test_snake_pit_kills_but_preserves_inventory_and_position(self):
        inventory = list(self.player.inventory)
        quiet(self.tile(2, 7).modify_player, self.player)  # SnakePitRoom
        self.assertFalse(self.player.is_alive())
        self.assertEqual(self.player.inventory, inventory)
        self.assertEqual((self.player.location_x, self.player.location_y),
                         world.starting_position)

    def test_failed_move_does_not_corrupt_world(self):
        before = {coord: tile for coord, tile in world._world.items()}
        self.player.location_x, self.player.location_y = 0, 4
        quiet(self.player.move_west)
        self.assertEqual(before, world._world)


class SaveLoadTest(BaseTestCase):
    def _play_a_bit(self):
        self.player.hp = 42
        quiet(self.tile(4, 4).modify_player, self.player)   # take dagger
        quiet(self.tile(2, 5).modify_player, self.player)   # take 5 gold
        self.tile(3, 1).enemy.hp = 0                        # spider dead
        self.player.location_x, self.player.location_y = 3, 4

    def test_save_load_roundtrip_restores_rooms_and_inventory(self):
        self._play_a_bit()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            savegame.save_game(self.player, path)

            world.load_tiles()  # simulate a fresh session
            restored = savegame.load_game(Player(), path)

        self.assertEqual(restored.hp, 42)
        self.assertEqual((restored.location_x, restored.location_y), (3, 4))
        self.assertTrue(any(isinstance(i, items.Dagger) for i in restored.inventory))
        self.assertEqual(sum(1 for g in restored.inventory
                             if isinstance(g, items.Gold) and g.amt == 5), 1)
        # rooms agree with the inventory: no item exists in both places
        self.assertIsNone(world.tile_exists(4, 4).item)
        self.assertIsNone(world.tile_exists(2, 5).item)
        self.assertEqual(world.tile_exists(3, 1).enemy.hp, 0)
        # untouched rooms stay untouched
        self.assertIsNotNone(world.tile_exists(1, 1).item)
        self.assertTrue(world.tile_exists(0, 4).enemy.is_alive())
        # re-entering a looted room after loading grants nothing
        before = len(restored.inventory)
        quiet(world.tile_exists(2, 5).modify_player, restored)
        self.assertEqual(len(restored.inventory), before)

    def test_load_rejects_tiles_that_are_not_on_the_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w") as f:
                json.dump({"player": {"hp": 1, "victory": False,
                                      "location_x": 0, "location_y": 0,
                                      "inventory": []},
                           "tiles": {"99,99": {"loot_taken": True}}}, f)
            with self.assertRaises(ValueError):
                savegame.load_game(Player(), path)


if __name__ == "__main__":
    unittest.main()
