"""Serializes and restores game state (player + dynamic tile state) as JSON."""
import json

import items, tiles, world
from player import Player


class SaveGameError(Exception):
    """Raised when a save file is missing, corrupt, or inconsistent."""


def _item_to_dict(item):
    data = {"type": type(item).__name__}
    if isinstance(item, items.Gold):
        data["amt"] = item.amt
    return data


def _item_from_dict(data):
    if not isinstance(data, dict) or "type" not in data:
        raise SaveGameError("invalid item entry: {!r}".format(data))
    cls = getattr(items, data["type"], None)
    if cls is items.Gold:
        try:
            return items.Gold(int(data["amt"]))
        except (KeyError, TypeError, ValueError):
            raise SaveGameError("invalid Gold entry: {!r}".format(data))
    if cls in (items.Rock, items.Dagger):
        return cls()
    raise SaveGameError("unsupported item type: {!r}".format(data.get("type")))


def save_game(player, path):
    """Writes the player and dynamic tile state to ``path`` as JSON."""
    tile_states = {}
    for (x, y), tile in world._world.items():
        if isinstance(tile, tiles.EnemyRoom):
            tile_states["{},{}".format(x, y)] = {"enemy_hp": tile.enemy.hp}
        elif isinstance(tile, tiles.LootRoom):
            tile_states["{},{}".format(x, y)] = {"loot_taken": tile.item is None}
    data = {
        "player": {
            "hp": player.hp,
            "location": [player.location_x, player.location_y],
            "victory": player.victory,
            "inventory": [_item_to_dict(item) for item in player.inventory],
        },
        "tiles": tile_states,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _parse(data):
    """Validates the save structure without touching any live game state."""
    if not isinstance(data, dict):
        raise SaveGameError("save data is not an object")
    try:
        pdata = data["player"]
        hp = int(pdata["hp"])
        loc_x, loc_y = (int(v) for v in pdata["location"])
        victory = bool(pdata["victory"])
        inventory = [_item_from_dict(entry) for entry in pdata["inventory"]]
    except SaveGameError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise SaveGameError("invalid player data: {}".format(e))
    tile_states = data.get("tiles", {})
    if not isinstance(tile_states, dict):
        raise SaveGameError("invalid tile state data")
    parsed_tiles = []
    for key, state in tile_states.items():
        try:
            x, y = (int(v) for v in key.split(","))
        except (AttributeError, TypeError, ValueError):
            raise SaveGameError("invalid tile key: {!r}".format(key))
        if not isinstance(state, dict):
            raise SaveGameError("invalid state for tile {}".format(key))
        parsed_tiles.append((x, y, state))
    return hp, loc_x, loc_y, victory, inventory, parsed_tiles


def load_game(path):
    """Restores a save file and returns the reconstructed player.

    The live world is only rebuilt after the whole file has been validated,
    so a corrupt save leaves the current game state untouched.
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SaveGameError("save file not found: {}".format(path))
    except json.JSONDecodeError as e:
        raise SaveGameError("save file is not valid JSON: {}".format(e))
    hp, loc_x, loc_y, victory, inventory, parsed_tiles = _parse(data)

    world.load_tiles()
    if world.tile_exists(loc_x, loc_y) is None:
        raise SaveGameError("saved location ({}, {}) is not a valid tile".format(loc_x, loc_y))
    for x, y, state in parsed_tiles:
        tile = world.tile_exists(x, y)
        if "enemy_hp" in state:
            if not isinstance(tile, tiles.EnemyRoom):
                raise SaveGameError("tile ({}, {}) is not an enemy room".format(x, y))
            tile.enemy.hp = int(state["enemy_hp"])
        if "loot_taken" in state:
            if not isinstance(tile, tiles.LootRoom):
                raise SaveGameError("tile ({}, {}) is not a loot room".format(x, y))
            if state["loot_taken"]:
                tile.item = None

    player = Player()
    player.hp = hp
    player.location_x = loc_x
    player.location_y = loc_y
    player.victory = victory
    player.inventory = inventory
    return player
