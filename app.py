import random
from collections import deque
from dataclasses import dataclass

import pyxel


SCREEN_W = 256
SCREEN_H = 240
TILE_SIZE = 16
MAP_VIEW_W = 176
MAP_VIEW_H = 240
VIEW_COLS = MAP_VIEW_W // TILE_SIZE
VIEW_ROWS = MAP_VIEW_H // TILE_SIZE
MAP_W = VIEW_COLS
MAP_H = VIEW_ROWS
PANEL_X = MAP_VIEW_W

# Terrain / town tiles
TILE_WATER = 0
TILE_GRASS = 1
TILE_FOREST = 2
TILE_TOWN_NEUTRAL = 3
TILE_TOWN_PLAYER = 4
TILE_TOWN_CPU = 5

# Indexed by tile enum value: WATER=0 GRASS=1 FOREST=2 TOWN_NEUTRAL=3 TOWN_PLAYER=4 TOWN_CPU=5
TILE_COLOR_LIST = [12, 11, 3, 13, 6, 8]

UNIT_STATS = {
    "infantry": {"label": "\u5175", "move": 2, "range": 1, "hp": 30, "atk": 10},
    "tank": {"label": "\u8eca", "move": 3, "range": 3, "hp": 50, "atk": 18},
    "fighter": {"label": "\u98db", "move": 5, "range": 3, "hp": 10, "atk": 12},
    "ship": {"label": "\u8266", "move": 2, "range": 5, "hp": 50, "atk": 16},
}

FORCE_COMPOSITION = {
    "infantry": 5,
    "tank": 3,
    "fighter": 2,
    "ship": 1,
}

SIDE_COLORS = {"player": 6, "cpu": 8}


@dataclass
class Unit:
    uid: int
    side: str
    kind: str
    x: int
    y: int
    hp: int
    alive: bool = True


class App:
    def __init__(self):
        pyxel.init(SCREEN_W, SCREEN_H, title="Retro Tactical Sim")
        pyxel.mouse(True)

        self.font = None
        try:
            self.font = pyxel.Font("assets/PixelMplus12-Regular.ttf", 12)
        except Exception:
            self.font = None

        self.scene = "title"
        self.units = []
        self.next_uid = 1
        self.tiles = [[TILE_GRASS for _ in range(MAP_W)] for _ in range(MAP_H)]

        self.turn_side = "player"
        self.turn_count = 1
        self.active_unit = None

        self.selected_unit = None
        self.pending_action = None
        self.highlight_tiles = set()
        self.highlight_units = set()
        self.menu_pos = None

        self.ai_state = "idle"
        self.ai_timer = 0
        self.ai_plan = None

        # Pre-compute label pixel sizes for tile-centered rendering
        self._label_sizes = {}
        for _kind, _stats in UNIT_STATS.items():
            _label = _stats["label"]
            if self.font:
                _lw = self.font.text_width(_label)
                _lh = 12
            else:
                _lw = len(_label) * 4
                _lh = 6
            self._label_sizes[_kind] = (_lw, _lh)

        self.result = None

        pyxel.run(self.update, self.draw)

    def text(self, x, y, s, col):
        if self.font:
            pyxel.text(x, y, s, col, self.font)
        else:
            pyxel.text(x, y, s, col)

    def start_game(self):
        self.scene = "game"
        self.result = None
        self.pending_action = None
        self.highlight_tiles = set()
        self.highlight_units = set()
        self.menu_pos = None
        self.selected_unit = None
        self.ai_state = "idle"
        self.ai_plan = None

        self.generate_map()
        self.spawn_units()

        self.turn_side = "player"
        self.turn_count = 1
        self.active_unit = None
        self.selected_unit = None

    def generate_map(self):
        self.tiles = [[TILE_GRASS for _ in range(MAP_W)] for _ in range(MAP_H)]
        area = MAP_W * MAP_H

        # Forest patches
        for _ in range(max(8, area // 8)):
            cx = random.randint(0, MAP_W - 1)
            cy = random.randint(0, MAP_H - 1)
            radius = random.randint(1, 3)
            for y in range(max(0, cy - radius), min(MAP_H, cy + radius + 1)):
                for x in range(max(0, cx - radius), min(MAP_W, cx + radius + 1)):
                    if random.random() < 0.6:
                        self.tiles[y][x] = TILE_FOREST

        # Clustered water by random walk blobs
        for _ in range(max(3, area // 55)):
            x = random.randint(0, MAP_W - 1)
            y = random.randint(0, MAP_H - 1)
            for _ in range(max(20, area // 4)):
                self.tiles[y][x] = TILE_WATER
                dx, dy = random.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
                x = max(0, min(MAP_W - 1, x + dx))
                y = max(0, min(MAP_H - 1, y + dy))

        # Smooth water shapes
        for _ in range(1):
            nxt = [row[:] for row in self.tiles]
            for y in range(MAP_H):
                for x in range(MAP_W):
                    water_neighbors = 0
                    for ny in range(max(0, y - 1), min(MAP_H, y + 2)):
                        for nx in range(max(0, x - 1), min(MAP_W, x + 2)):
                            if nx == x and ny == y:
                                continue
                            if self.tiles[ny][nx] == TILE_WATER:
                                water_neighbors += 1
                    if self.tiles[y][x] == TILE_WATER and water_neighbors <= 2:
                        nxt[y][x] = TILE_GRASS
                    elif self.tiles[y][x] != TILE_WATER and water_neighbors >= 5:
                        nxt[y][x] = TILE_WATER
            self.tiles = nxt

        # Place odd number of towns (neutral)
        town_count = 7
        placed = 0
        tries = 0
        while placed < town_count and tries < 5000:
            tries += 1
            x = random.randint(1, MAP_W - 2)
            y = random.randint(1, MAP_H - 2)
            if self.tiles[y][x] == TILE_WATER:
                continue
            if self.tiles[y][x] in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER, TILE_TOWN_CPU):
                continue
            ok = True
            for ty in range(max(0, y - 2), min(MAP_H, y + 3)):
                for tx in range(max(0, x - 2), min(MAP_W, x + 3)):
                    if self.tiles[ty][tx] in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER, TILE_TOWN_CPU):
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            self.tiles[y][x] = TILE_TOWN_NEUTRAL
            placed += 1

    def spawn_units(self):
        self.units = []
        self.next_uid = 1

        for side in ("player", "cpu"):
            for kind, count in FORCE_COMPOSITION.items():
                for _ in range(count):
                    x, y = self.find_spawn_position(side, kind)
                    hp = UNIT_STATS[kind]["hp"]
                    self.units.append(Unit(self.next_uid, side, kind, x, y, hp))
                    self.next_uid += 1

    def find_spawn_position(self, side, kind):
        left = side == "player"
        band = max(2, MAP_W // 3)
        for _ in range(5000):
            if left:
                x = random.randint(0, band - 1)
            else:
                x = random.randint(MAP_W - band, MAP_W - 1)
            y = random.randint(0, MAP_H - 1)
            if not self.can_unit_enter(kind, x, y):
                continue
            if self.unit_at(x, y) is not None:
                continue
            return x, y

        # Fallback: scan entire map
        for y in range(MAP_H):
            for x in range(MAP_W):
                if left and x >= band:
                    continue
                if not left and x < MAP_W - band:
                    continue
                if self.can_unit_enter(kind, x, y) and self.unit_at(x, y) is None:
                    return x, y

        # Last resort for ship: force a water tile in the correct band
        if kind == "ship":
            for y in range(MAP_H):
                for x in range(MAP_W):
                    if left and x >= band:
                        continue
                    if not left and x < MAP_W - band:
                        continue
                    if self.unit_at(x, y) is None:
                        self.tiles[y][x] = TILE_WATER
                        return x, y
        return 0, 0

    def can_unit_enter(self, kind, x, y):
        tile = self.tiles[y][x]
        if kind == "fighter":
            return True
        if kind == "ship":
            return tile == TILE_WATER
        if kind == "tank":
            return tile in (TILE_GRASS, TILE_FOREST)
        # infantry: can enter any tile (water costs double movement)
        return True

    def tile_move_cost(self, kind, x, y):
        """Movement cost to enter tile (x, y). Returns 2 for tank on forest or infantry on water, 1 otherwise."""
        tile = self.tiles[y][x]
        if kind == "tank" and tile == TILE_FOREST:
            return 2
        if kind == "infantry" and tile == TILE_WATER:
            return 2
        return 1

    def can_attack_target(self, attacker, defender):
        if attacker.kind == "fighter":
            return defender.kind in ("fighter", "ship")
        return True

    def unit_at(self, x, y):
        for unit in self.units:
            if unit.alive and unit.x == x and unit.y == y:
                return unit
        return None

    def living_units(self, side):
        return [u for u in self.units if u.alive and u.side == side]

    def center_camera_on_active(self):
        return

    def get_reachable_tiles(self, unit):
        move = UNIT_STATS[unit.kind]["move"]
        # cost_map stores the minimum movement cost to reach each tile
        cost_map = {(unit.x, unit.y): 0}
        queue = deque([(unit.x, unit.y, 0)])
        reachable = set()

        while queue:
            x, y, spent = queue.popleft()
            if (x, y) != (unit.x, unit.y) and self.unit_at(x, y) is None:
                reachable.add((x, y))

            for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + ddx, y + ddy
                if nx < 0 or ny < 0 or nx >= MAP_W or ny >= MAP_H:
                    continue
                if not self.can_unit_enter(unit.kind, nx, ny):
                    continue
                occupant = self.unit_at(nx, ny)
                if occupant is not None and occupant != unit:
                    continue
                new_cost = spent + self.tile_move_cost(unit.kind, nx, ny)
                if new_cost > move:
                    continue
                if (nx, ny) in cost_map and cost_map[(nx, ny)] <= new_cost:
                    continue
                cost_map[(nx, ny)] = new_cost
                queue.append((nx, ny, new_cost))

        return reachable

    def get_attackable_units(self, unit):
        rng = UNIT_STATS[unit.kind]["range"]
        targets = set()
        for other in self.units:
            if not other.alive or other.side == unit.side:
                continue
            dist = abs(unit.x - other.x) + abs(unit.y - other.y)
            if dist <= rng and self.can_attack_target(unit, other):
                targets.add(other.uid)
        return targets

    def get_attack_tiles(self, unit):
        rng = UNIT_STATS[unit.kind]["range"]
        ux, uy = unit.x, unit.y
        tiles = set()
        for dy in range(-rng, rng + 1):
            rem = rng - abs(dy)
            for dx in range(-rem, rem + 1):
                if dx == 0 and dy == 0:
                    continue
                x, y = ux + dx, uy + dy
                if 0 <= x < MAP_W and 0 <= y < MAP_H:
                    tiles.add((x, y))
        return tiles

    def do_attack(self, attacker, target):
        """Attack with 80% hit chance."""
        if random.random() >= 0.8:
            return  # miss
        dmg = UNIT_STATS[attacker.kind]["atk"]
        target.hp -= dmg
        if target.hp <= 0:
            target.alive = False

    def on_infantry_capture(self, unit):
        if unit.kind != "infantry":
            return
        tile = self.tiles[unit.y][unit.x]
        if unit.side == "player" and tile in (TILE_TOWN_NEUTRAL, TILE_TOWN_CPU):
            self.tiles[unit.y][unit.x] = TILE_TOWN_PLAYER
        elif unit.side == "cpu" and tile in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER):
            self.tiles[unit.y][unit.x] = TILE_TOWN_CPU

    def check_result(self):
        player_alive = len(self.living_units("player"))
        cpu_alive = len(self.living_units("cpu"))
        player_town, cpu_town, neutral = self.count_towns()

        # Game over trigger: all towns occupied, one side annihilated, or one side's infantry
        # wiped out while other unit types still survive.
        player_has_infantry = any(u.alive and u.side == "player" and u.kind == "infantry" for u in self.units)
        cpu_has_infantry = any(u.alive and u.side == "cpu" and u.kind == "infantry" for u in self.units)
        game_over = (
            neutral == 0
            or player_alive == 0
            or cpu_alive == 0
            or not player_has_infantry
            or not cpu_has_infantry
        )
        if game_over:
            if player_town > cpu_town:
                self.result = "player"
            elif cpu_town > player_town:
                self.result = "cpu"
            else:
                self.result = "draw"
            return True

        return False

    def finish_turn(self):
        if self.check_result():
            return

        self.pending_action = None
        self.highlight_tiles.clear()
        self.highlight_units.clear()
        self.menu_pos = None

        self.turn_side = "cpu" if self.turn_side == "player" else "player"
        if self.turn_side == "player":
            self.turn_count += 1

        self.active_unit = None
        self.selected_unit = None

        if self.turn_side == "cpu":
            self.ai_state = "decide"
            self.ai_timer = 25
            self.ai_plan = None

    def map_click_tile(self):
        mx, my = pyxel.mouse_x, pyxel.mouse_y
        if not (0 <= mx < MAP_VIEW_W and 0 <= my < MAP_VIEW_H):
            return None
        tx = mx // TILE_SIZE
        ty = my // TILE_SIZE
        if tx < 0 or ty < 0 or tx >= MAP_W or ty >= MAP_H:
            return None
        return tx, ty

    def panel_button_clicked(self, x, y, w, h):
        mx, my = pyxel.mouse_x, pyxel.mouse_y
        return x <= mx < x + w and y <= my < y + h

    def set_menu_position_near_mouse(self):
        menu_w = 70
        menu_h = 62
        margin = 2
        mx, my = pyxel.mouse_x, pyxel.mouse_y
        x = mx + 6
        y = my - 6

        if x + menu_w > MAP_VIEW_W - margin:
            x = mx - menu_w - 6
        if y + menu_h > MAP_VIEW_H - margin:
            y = MAP_VIEW_H - menu_h - margin
        if y < margin:
            y = margin
        if x < margin:
            x = margin

        self.menu_pos = (x, y)

    def update_title(self):
        if pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT) or pyxel.btnp(pyxel.KEY_RETURN):
            self.start_game()

    def update_player_turn(self):
        if self.active_unit and not self.active_unit.alive:
            self.active_unit = None
            self.pending_action = None
            self.highlight_tiles.clear()
            self.highlight_units.clear()
            self.menu_pos = None

        if pyxel.btnp(pyxel.KEY_Q):
            pyxel.quit()

        if pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT):
            # Context menu buttons (shown near cursor after selecting own unit)
            if self.pending_action is None:
                menu_x, menu_y = self.menu_pos if self.menu_pos else (-999, -999)
                if self.panel_button_clicked(menu_x, menu_y, 70, 18):
                    if self.active_unit is None:
                        return
                    self.pending_action = "move"
                    self.highlight_tiles = self.get_reachable_tiles(self.active_unit)
                    self.highlight_units.clear()
                    return
                if self.panel_button_clicked(menu_x, menu_y + 22, 70, 18):
                    if self.active_unit is None:
                        return
                    self.pending_action = "attack"
                    self.highlight_tiles = self.get_attack_tiles(self.active_unit)
                    self.highlight_units = self.get_attackable_units(self.active_unit)
                    return
                if self.panel_button_clicked(menu_x, menu_y + 44, 70, 18):
                    self.finish_turn()
                    return

            clicked = self.map_click_tile()
            if clicked is None:
                # Clicking the panel only clears state when not in range-select mode
                if self.pending_action is None:
                    self.menu_pos = None
                return

            tx, ty = clicked
            clicked_unit = self.unit_at(tx, ty)

            if self.pending_action == "move":
                if (tx, ty) in self.highlight_tiles:
                    self.active_unit.x = tx
                    self.active_unit.y = ty
                    self.on_infantry_capture(self.active_unit)
                    self.finish_turn()
                else:
                    # Cancel move only when clicking outside movement range
                    self.pending_action = None
                    self.highlight_tiles.clear()
            elif self.pending_action == "attack":
                if clicked_unit and clicked_unit.uid in self.highlight_units:
                    # Enemy within attack range clicked -> execute attack
                    self.do_attack(self.active_unit, clicked_unit)
                    self.finish_turn()
                elif (tx, ty) not in self.highlight_tiles:
                    # Clicked completely outside attack range -> cancel
                    self.pending_action = None
                    self.highlight_tiles.clear()
                    self.highlight_units.clear()
                # else: clicked within range but no valid target -> keep attack mode active
            else:
                if clicked_unit:
                    self.selected_unit = clicked_unit
                    if clicked_unit.side == "player":
                        self.active_unit = clicked_unit
                        self.set_menu_position_near_mouse()
                    else:
                        self.menu_pos = None

    def score_ai_move(self, unit, x, y, towns_cache=None, enemies_cache=None):
        score = 0
        if unit.kind == "infantry":
            tile = self.tiles[y][x]
            if tile in (TILE_TOWN_NEUTRAL, TILE_TOWN_CPU):
                score += 100

        enemies = enemies_cache if enemies_cache is not None else self.living_units("player")
        if enemies:
            nearest = min(abs(x - e.x) + abs(y - e.y) for e in enemies)
            score += max(0, 25 - nearest)

        if unit.kind == "infantry":
            if towns_cache is None:
                towns_cache = [
                    (xx, yy) for yy in range(MAP_H) for xx in range(MAP_W)
                    if self.tiles[yy][xx] in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER)
                ]
            if towns_cache:
                nearest_town = min(abs(x - tx) + abs(y - ty) for tx, ty in towns_cache)
                score += max(0, 30 - nearest_town)

        return score

    def choose_ai_action(self, unit):
        if not unit or not unit.alive:
            return {"type": "skip"}

        # Attack priority: target that can be destroyed first, then lowest HP.
        targets = [u for u in self.units if u.alive and u.side == "player"]
        in_range = []
        rng = UNIT_STATS[unit.kind]["range"]
        for t in targets:
            if not self.can_attack_target(unit, t):
                continue
            if abs(unit.x - t.x) + abs(unit.y - t.y) <= rng:
                kill_bonus = 100 if t.hp <= UNIT_STATS[unit.kind]["atk"] else 0
                in_range.append((kill_bonus - t.hp, t))
        if in_range:
            in_range.sort(key=lambda v: v[0], reverse=True)
            return {"type": "attack", "target": in_range[0][1]}

        # Move priority - pre-compute caches once to avoid repeated scans
        towns_cache = [
            (xx, yy) for yy in range(MAP_H) for xx in range(MAP_W)
            if self.tiles[yy][xx] in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER)
        ]
        enemies_cache = self.living_units("player")
        reachable = list(self.get_reachable_tiles(unit))
        if reachable:
            best = max(reachable, key=lambda p: self.score_ai_move(unit, p[0], p[1], towns_cache, enemies_cache))
            return {"type": "move", "to": best}

        return {"type": "skip"}

    def choose_ai_unit(self):
        best_unit = None
        best_score = -10**9
        # Pre-compute caches once for all units
        towns_cache = [
            (xx, yy) for yy in range(MAP_H) for xx in range(MAP_W)
            if self.tiles[yy][xx] in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER)
        ]
        enemies_cache = self.living_units("player")

        for unit in self.living_units("cpu"):
            score = 0
            attackable = self.get_attackable_units(unit)
            if attackable:
                score += 200

            reachable = self.get_reachable_tiles(unit)
            if reachable:
                move_score = max(self.score_ai_move(unit, x, y, towns_cache, enemies_cache) for x, y in reachable)
                score += move_score

            if unit.kind == "infantry":
                score += 20

            if score > best_score:
                best_score = score
                best_unit = unit

        return best_unit

    def update_cpu_turn(self):
        if self.ai_state == "decide":
            self.ai_timer -= 1
            if self.ai_timer <= 0:
                self.active_unit = self.choose_ai_unit()
                self.selected_unit = self.active_unit
                if self.active_unit is None:
                    self.finish_turn()
                    return

                self.ai_plan = self.choose_ai_action(self.active_unit)
                self.ai_state = "preview"
                self.ai_timer = 20

                if self.ai_plan["type"] == "move":
                    self.highlight_tiles = {self.ai_plan["to"]}
                    self.highlight_units.clear()
                elif self.ai_plan["type"] == "attack":
                    self.highlight_tiles = self.get_attack_tiles(self.active_unit)
                    self.highlight_units = {self.ai_plan["target"].uid}
                else:
                    self.highlight_tiles.clear()
                    self.highlight_units.clear()

        elif self.ai_state == "preview":
            self.ai_timer -= 1
            if self.ai_timer <= 0:
                if self.ai_plan["type"] == "move":
                    tx, ty = self.ai_plan["to"]
                    self.active_unit.x = tx
                    self.active_unit.y = ty
                    self.on_infantry_capture(self.active_unit)
                elif self.ai_plan["type"] == "attack":
                    target = self.ai_plan["target"]
                    if target.alive:
                        self.do_attack(self.active_unit, target)

                self.ai_state = "idle"
                self.highlight_tiles.clear()
                self.highlight_units.clear()
                self.finish_turn()

    def update_result_overlay(self):
        if pyxel.btnp(pyxel.KEY_RETURN) or pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT):
            self.scene = "title"

    def update(self):
        if self.scene == "title":
            self.update_title()
            return

        if self.scene == "game":
            if self.result is not None:
                self.update_result_overlay()
                return

            if self.turn_side == "player":
                self.update_player_turn()
            else:
                self.update_cpu_turn()

    def draw_map(self):
        for sy in range(VIEW_ROWS):
            my = sy
            if my >= MAP_H:
                continue
            for sx in range(VIEW_COLS):
                mx = sx
                if mx >= MAP_W:
                    continue
                tile = self.tiles[my][mx]
                col = TILE_COLOR_LIST[tile]
                px = sx * TILE_SIZE
                py = sy * TILE_SIZE
                pyxel.rect(px, py, TILE_SIZE, TILE_SIZE, col)
                if tile in (TILE_TOWN_NEUTRAL, TILE_TOWN_PLAYER, TILE_TOWN_CPU):
                    pyxel.rectb(px + 2, py + 2, TILE_SIZE - 4, TILE_SIZE - 4, 7)

        for u in self.units:
            if not u.alive:
                continue
            sx = u.x
            sy = u.y
            if not (0 <= sx < VIEW_COLS and 0 <= sy < VIEW_ROWS):
                continue
            px = sx * TILE_SIZE
            py = sy * TILE_SIZE
            bg = SIDE_COLORS[u.side]
            pyxel.rect(px + 2, py + 2, TILE_SIZE - 4, TILE_SIZE - 4, bg)
            lw, lh = self._label_sizes[u.kind]
            self.text(px + (TILE_SIZE - lw) // 2, py + (TILE_SIZE - lh) // 2, UNIT_STATS[u.kind]["label"], 7)

        # Highlight movement tiles
        for tx, ty in self.highlight_tiles:
            sx = tx
            sy = ty
            if 0 <= sx < VIEW_COLS and 0 <= sy < VIEW_ROWS:
                pyxel.rectb(sx * TILE_SIZE, sy * TILE_SIZE, TILE_SIZE, TILE_SIZE, 8)

        # Highlight attack targets
        for uid in self.highlight_units:
            target = next((u for u in self.units if u.uid == uid and u.alive), None)
            if target is None:
                continue
            sx = target.x
            sy = target.y
            if 0 <= sx < VIEW_COLS and 0 <= sy < VIEW_ROWS:
                pyxel.rectb(sx * TILE_SIZE, sy * TILE_SIZE, TILE_SIZE, TILE_SIZE, 8)
                pyxel.rectb(sx * TILE_SIZE + 1, sy * TILE_SIZE + 1, TILE_SIZE - 2, TILE_SIZE - 2, 7)

        if self.active_unit and self.active_unit.alive:
            sx = self.active_unit.x
            sy = self.active_unit.y
            if 0 <= sx < VIEW_COLS and 0 <= sy < VIEW_ROWS:
                pyxel.rectb(sx * TILE_SIZE, sy * TILE_SIZE, TILE_SIZE, TILE_SIZE, 10)

        # Context menu near cursor (player turn)
        if self.turn_side == "player" and self.pending_action is None and self.menu_pos and self.result is None:
            mx, my = self.menu_pos
            self.draw_button(mx, my, 70, 18, "Move", False)
            self.draw_button(mx, my + 22, 70, 18, "Attack", False)
            self.draw_button(mx, my + 44, 70, 18, "Skip", False)

    def draw_panel(self):
        pyxel.rect(PANEL_X, 0, SCREEN_W - PANEL_X, SCREEN_H, 1)
        pyxel.rectb(PANEL_X, 0, SCREEN_W - PANEL_X, SCREEN_H, 5)

        turn_text = "PLAYER" if self.turn_side == "player" else "CPU"
        self.text(PANEL_X + 8, 8, f"TURN {self.turn_count}", 7)
        self.text(PANEL_X + 8, 22, f"SIDE: {turn_text}", 7)

        player_town, cpu_town, neutral = self.count_towns()
        self.text(PANEL_X + 8, 42, f"Town P:{player_town}", 6)
        self.text(PANEL_X + 8, 54, f"Town C:{cpu_town}", 8)
        self.text(PANEL_X + 8, 66, f"Town N:{neutral}", 13)

        unit = self.selected_unit if self.selected_unit and self.selected_unit.alive else self.active_unit
        if unit and unit.alive:
            self.text(PANEL_X + 8, 86, f"Unit:{UNIT_STATS[unit.kind]['label']}", 7)
            self.text(PANEL_X + 8, 98, f"HP:{unit.hp}", 7)
            self.text(PANEL_X + 8, 110, f"MV:{UNIT_STATS[unit.kind]['move']} RG:{UNIT_STATS[unit.kind]['range']}", 7)

        if self.turn_side == "player" and self.active_unit is None and self.result is None:
            self.text(PANEL_X + 8, 204, "Select own unit", 10)
        elif self.turn_side == "player" and self.pending_action is None:
            self.text(PANEL_X + 8, 204, "Menu near cursor", 13)
        else:
            self.text(PANEL_X + 8, 204, "Click unit/menu", 13)
        self.text(PANEL_X + 8, 216, "Mouse/Touch", 13)

    def draw_button(self, x, y, w, h, label, active):
        fill = 5 if active else 2
        pyxel.rect(x, y, w, h, fill)
        pyxel.rectb(x, y, w, h, 7)
        self.text(x + 10, y + 6, label, 7)

    def count_towns(self):
        player_town = 0
        cpu_town = 0
        neutral = 0
        for row in self.tiles:
            for t in row:
                if t == TILE_TOWN_PLAYER:
                    player_town += 1
                elif t == TILE_TOWN_CPU:
                    cpu_town += 1
                elif t == TILE_TOWN_NEUTRAL:
                    neutral += 1
        return player_town, cpu_town, neutral

    def draw_result_overlay(self):
        pyxel.rect(20, 52, 216, 136, 0)
        pyxel.rectb(20, 52, 216, 136, 7)

        player_town, cpu_town, _ = self.count_towns()
        self.text(40, 74, "GAME RESULT", 10)
        self.text(40, 98, f"Player Town: {player_town}", 6)
        self.text(40, 114, f"CPU Town: {cpu_town}", 8)

        if self.result == "player":
            self.text(40, 138, "WINNER: PLAYER", 11)
        elif self.result == "cpu":
            self.text(40, 138, "WINNER: CPU", 8)
        else:
            self.text(40, 138, "DRAW", 7)

        self.text(40, 162, "Click or Enter to Title", 13)

    def draw_title(self):
        pyxel.cls(0)

        # Simple animated background tiles
        for i in range(0, SCREEN_W, 16):
            y = (i // 2 + pyxel.frame_count) % SCREEN_H
            pyxel.rect(i, y, 8, 8, 1)
            pyxel.rect((i + 7) % SCREEN_W, (y + 8) % SCREEN_H, 8, 8, 5)

        self.text(36, 58, "TACTICAL SIMULATION", 10)
        self.text(40, 106, "Mouse / Touch Operation", 13)

        if pyxel.frame_count % 40 < 28:
            self.text(52, 154, "CLICK TO START", 11)

        self.text(36, 210, "Infantry captures towns", 6)

    def draw(self):
        if self.scene == "title":
            self.draw_title()
            return

        pyxel.cls(0)
        self.draw_map()
        self.draw_panel()

        if self.result is not None:
            self.draw_result_overlay()


App()
