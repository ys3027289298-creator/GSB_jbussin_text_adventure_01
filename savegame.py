"""Save and load game state (player + world tiles) as JSON.

The world tile mapping (world._world) is the contract: a save file records
per-tile state keyed by map coordinates, and loading applies that state back
onto the tiles of the currently loaded world.
"""
__author__ = 'Phillip Johnson'

import json

import items
import tiles as tile_types
import world


def _item_to_data(item):
    data = {"type": type(item).__name__}
    if isinstance(item, items.Gold):
        data["amt"] = item.amt
    return data


def _item_from_data(data):
    cls = getattr(items, data["type"], None)
    if cls is None:
        raise ValueError("Unknown item type in save: {}".format(data["type"]))
    if cls is items.Gold:
        return cls(data["amt"])
    return cls()


def save_game(player, path):
    """Writes the player and world state to a JSON file at the given path."""
    tiles_data = {}
    for (x, y), tile in world._world.items():
        if tile is None:
            continue
        state = {}
        if isinstance(tile, tile_types.LootRoom):
            state["loot_taken"] = tile.item is None
        if isinstance(tile, tile_types.EnemyRoom):
            state["enemy_hp"] = tile.enemy.hp
        if state:
            tiles_data["{},{}".format(x, y)] = state
    data = {
        "player": {
            "hp": player.hp,
            "victory": player.victory,
            "location_x": player.location_x,
            "location_y": player.location_y,
            "inventory": [_item_to_data(item) for item in player.inventory],
        },
        "tiles": tiles_data,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_game(player, path):
    """Restores player and world state from a save file. Returns the player.

    The world must already be loaded (world.load_tiles()); the save is
    validated against the current tile mapping.
    """
    with open(path, "r") as f:
        data = json.load(f)
    player_data = data["player"]
    player.hp = player_data["hp"]
    player.victory = player_data["victory"]
    player.location_x = player_data["location_x"]
    player.location_y = player_data["location_y"]
    player.inventory = [_item_from_data(d) for d in player_data["inventory"]]
    for key, state in data["tiles"].items():
        x, y = (int(part) for part in key.split(","))
        tile = world.tile_exists(x, y)
        if tile is None:
            raise ValueError("Save references tile ({}, {}) which is not on the map".format(x, y))
        if "loot_taken" in state:
            if state["loot_taken"]:
                tile.item = None
            elif tile.item is None:
                raise ValueError("Save says tile ({}, {}) still holds loot, "
                                 "but the room has none".format(x, y))
        if "enemy_hp" in state:
            tile.enemy.hp = state["enemy_hp"]
    return player
