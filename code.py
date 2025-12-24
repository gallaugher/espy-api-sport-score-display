# SPDX-FileCopyrightText: 2023 Liz Clark for Adafruit Industries
# Modified for all-games ticker on two 64x64 panels
#
# SPDX-License-Identifier: MIT

import os
import gc
import ssl
import time
import wifi
import socketpool
import adafruit_requests
import adafruit_display_text.label
import board
import terminalio
import displayio
import framebufferio
import rgbmatrix
import microcontroller
from adafruit_ticks import ticks_ms, ticks_add, ticks_diff
from adafruit_datetime import datetime, timedelta
import neopixel

displayio.release_displays()

# =============================================================================
# CONFIGURATION
# =============================================================================

# Font color for text on matrix
font_color = 0xFFFFFF

# Your timezone UTC offset and timezone name
# EST = -5, EDT = -4, CST = -6, CDT = -5, MST = -7, MDT = -6, PST = -8, PDT = -7
timezone_info = [-5, "EST"]

# Sports and leagues to follow (must match your logo folder order)
# team0_logos = NFL, team1_logos = MLB, team2_logos = NHL, team3_logos = NBA
sport_names = ["football", "baseball", "hockey", "basketball"]
sport_leagues = ["nfl", "mlb", "nhl", "nba"]
logo_folders = ["team0_logos", "team1_logos", "team2_logos", "team3_logos"]

# How often to refresh data from ESPN API (seconds)
fetch_interval = 300  # 5 minutes

# How long to display each game (seconds)
display_interval = 5  # 5 seconds per game

# =============================================================================
# HARDWARE SETUP - Two 64x64 panels side by side
# =============================================================================

pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.3, auto_write=True)

base_width = 64
base_height = 64  # 64-row panels
chain_across = 2  # Two panels side by side
tile_down = 1  # Not tiled vertically

DISPLAY_WIDTH = base_width * chain_across  # 128
DISPLAY_HEIGHT = base_height * tile_down  # 64

matrix = rgbmatrix.RGBMatrix(
    width=DISPLAY_WIDTH,
    height=DISPLAY_HEIGHT,
    bit_depth=4,
    rgb_pins=[
        board.MTX_R1,
        board.MTX_G1,
        board.MTX_B1,
        board.MTX_R2,
        board.MTX_G2,
        board.MTX_B2
    ],
    addr_pins=[
        board.MTX_ADDRA,
        board.MTX_ADDRB,
        board.MTX_ADDRC,
        board.MTX_ADDRD,
        board.MTX_ADDRE,  # Required for 64-row panels
    ],
    clock_pin=board.MTX_CLK,
    latch_pin=board.MTX_LAT,
    output_enable_pin=board.MTX_OE,
    tile=tile_down,
    serpentine=True,
    doublebuffer=True
)

display = framebufferio.FramebufferDisplay(matrix)

# =============================================================================
# WIFI CONNECTION
# =============================================================================

print("Connecting to WiFi...")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print(f"Connected to {os.getenv('CIRCUITPY_WIFI_SSID')}")

context = ssl.create_default_context()
pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, context)

# =============================================================================
# BUILD API URLs
# =============================================================================

SPORT_URLS = []
for i in range(len(sport_leagues)):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_names[i]}/{sport_leagues[i]}/scoreboard"
    SPORT_URLS.append(url)
    print(f"Added URL for {sport_leagues[i].upper()}")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def convert_date_format(date_str, tz_info):
    """Convert UTC time from ESPN API to local timezone display format."""
    try:
        year = int(date_str[0:4])
        month = int(date_str[5:7])
        day = int(date_str[8:10])
        hour = int(date_str[11:13])
        minute = int(date_str[14:16])

        dt = datetime(year, month, day, hour, minute)
        dt_adjusted = dt + timedelta(hours=tz_info[0])

        month = dt_adjusted.month
        day = dt_adjusted.day
        hour = dt_adjusted.hour
        minute = dt_adjusted.minute

        am_pm = "AM" if hour < 12 else "PM"
        hour_12 = hour if hour <= 12 else hour - 12
        if hour_12 == 0:
            hour_12 = 12

        return f"{month}/{day} {hour_12}:{minute:02d}{am_pm}"
    except Exception as e:
        print(f"Date conversion error: {e}")
        return "TBD"


def get_league_index(league):
    """Get the logo folder index for a league."""
    try:
        return sport_leagues.index(league)
    except ValueError:
        return 0


def fetch_all_games():
    """Fetch all games from all leagues and return a list of game data."""
    all_games = []

    for league_idx, url in enumerate(SPORT_URLS):
        league = sport_leagues[league_idx]
        print(f"Fetching {league.upper()} games...")
        pixel.fill((0, 0, 255))  # Blue while fetching

        try:
            resp = requests.get(url)
            data = resp.json()
            resp.close()

            events = data.get("events", [])
            print(f"  Found {len(events)} {league.upper()} games")

            for event in events:
                try:
                    game = parse_game(event, league_idx)
                    if game:
                        all_games.append(game)
                except Exception as e:
                    print(f"  Error parsing game: {e}")
                    continue

        except Exception as e:
            print(f"  Error fetching {league.upper()}: {e}")
            continue

        gc.collect()

    pixel.fill((0, 0, 0))  # Turn off LED
    print(f"Total games loaded: {len(all_games)}")
    return all_games


def parse_game(event, league_idx):
    """Parse a single game event into a display-friendly dictionary."""
    try:
        competition = event["competitions"][0]
        competitors = competition["competitors"]

        if len(competitors) != 2:
            return None

        # Get team info - ESPN lists home team first (index 0)
        home_team = competitors[0]["team"]["abbreviation"]
        away_team = competitors[1]["team"]["abbreviation"]
        home_score = competitors[0].get("score", "0")
        away_score = competitors[1].get("score", "0")

        # Get game status
        status_type = event["status"]["type"]
        status_name = status_type.get("name", "STATUS_SCHEDULED")
        status_detail = status_type.get("shortDetail", "")

        # Get game date
        game_date = event.get("date", "")

        # Determine display status
        if status_name == "STATUS_FINAL":
            display_status = "FINAL"
        elif status_name == "STATUS_IN_PROGRESS":
            display_status = status_detail  # e.g., "Q3 5:42" or "2nd 12:30"
        elif status_name == "STATUS_SCHEDULED":
            display_status = convert_date_format(game_date, timezone_info)
        elif status_name == "STATUS_POSTPONED":
            display_status = "POSTPONED"
        elif status_name == "STATUS_CANCELED":
            display_status = "CANCELED"
        else:
            display_status = status_detail if status_detail else "SCHEDULED"

        return {
            "league": sport_leagues[league_idx].upper(),
            "league_idx": league_idx,
            "home_team": home_team,
            "away_team": away_team,
            "home_score": str(home_score),
            "away_score": str(away_score),
            "status": display_status,
            "is_final": status_name == "STATUS_FINAL",
            "is_live": status_name == "STATUS_IN_PROGRESS",
            "is_scheduled": status_name == "STATUS_SCHEDULED",
        }
    except Exception as e:
        print(f"Parse error: {e}")
        return None


def build_game_display(game):
    """Build a displayio Group for a single game."""
    group = displayio.Group()

    league_idx = game["league_idx"]
    folder = logo_folders[league_idx]

    # Load team logos
    try:
        home_logo_path = f"/{folder}/{game['home_team']}.bmp"
        home_bitmap = displayio.OnDiskBitmap(home_logo_path)
        home_grid = displayio.TileGrid(home_bitmap, pixel_shader=home_bitmap.pixel_shader, x=4, y=4)
        group.append(home_grid)
    except Exception as e:
        print(f"Can't load home logo {game['home_team']}: {e}")

    try:
        away_logo_path = f"/{folder}/{game['away_team']}.bmp"
        away_bitmap = displayio.OnDiskBitmap(away_logo_path)
        away_grid = displayio.TileGrid(away_bitmap, pixel_shader=away_bitmap.pixel_shader, x=92, y=4)
        group.append(away_grid)
    except Exception as e:
        print(f"Can't load away logo {game['away_team']}: {e}")

    # League label at top center
    league_label = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=0xFFFF00,  # Yellow
        text=game["league"]
    )
    league_label.anchor_point = (0.5, 0.0)
    league_label.anchored_position = (DISPLAY_WIDTH // 2, 2)
    group.append(league_label)

    # Team abbreviations below logos
    home_abbr = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=font_color,
        text=game["home_team"]
    )
    home_abbr.anchor_point = (0.5, 0.0)
    home_abbr.anchored_position = (20, 38)
    group.append(home_abbr)

    away_abbr = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=font_color,
        text=game["away_team"]
    )
    away_abbr.anchor_point = (0.5, 0.0)
    away_abbr.anchored_position = (108, 38)
    group.append(away_abbr)

    # Score or VS in center
    if game["is_scheduled"]:
        score_text = "VS"
        score_color = font_color
    else:
        score_text = f"{game['home_score']} - {game['away_score']}"
        score_color = 0x00FF00 if game["is_live"] else font_color  # Green if live

    score_label = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=score_color,
        text=score_text
    )
    score_label.anchor_point = (0.5, 0.5)
    score_label.anchored_position = (DISPLAY_WIDTH // 2, 24)
    group.append(score_label)

    # Status at bottom
    status_label = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=0xFF0000 if game["is_live"] else font_color,  # Red if live
        text=game["status"]
    )
    status_label.anchor_point = (0.5, 1.0)
    status_label.anchored_position = (DISPLAY_WIDTH // 2, DISPLAY_HEIGHT - 2)
    group.append(status_label)

    return group


def show_startup():
    """Display a startup message."""
    group = displayio.Group()

    title = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=0xFFFF00,
        text="SPORTS TICKER"
    )
    title.anchor_point = (0.5, 0.5)
    title.anchored_position = (DISPLAY_WIDTH // 2, 20)
    group.append(title)

    subtitle = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=font_color,
        text="Loading..."
    )
    subtitle.anchor_point = (0.5, 0.5)
    subtitle.anchored_position = (DISPLAY_WIDTH // 2, 40)
    group.append(subtitle)

    display.root_group = group


def show_no_games():
    """Display a message when no games are found."""
    group = displayio.Group()

    msg = adafruit_display_text.label.Label(
        terminalio.FONT,
        color=font_color,
        text="NO GAMES TODAY"
    )
    msg.anchor_point = (0.5, 0.5)
    msg.anchored_position = (DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2)
    group.append(msg)

    display.root_group = group


# =============================================================================
# MAIN PROGRAM
# =============================================================================

print("=" * 40)
print("Sports Ticker Starting")
print(f"Display: {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}")
print("=" * 40)

# Show startup screen
show_startup()
time.sleep(2)

# Initial fetch
games = fetch_all_games()

if not games:
    print("No games found on initial fetch")
    show_no_games()
    time.sleep(10)
    games = fetch_all_games()

# Convert intervals to milliseconds
fetch_interval_ms = fetch_interval * 1000
display_interval_ms = display_interval * 1000

# Initialize timers
fetch_clock = ticks_ms()
display_clock = ticks_ms()
game_index = 0

print(f"Starting ticker with {len(games)} games")
print(f"Display interval: {display_interval}s, Fetch interval: {fetch_interval}s")

# Main loop
while True:
    try:
        current_time = ticks_ms()

        # Time to refresh data from ESPN?
        if ticks_diff(current_time, fetch_clock) >= fetch_interval_ms:
            print("Refreshing game data...")
            gc.collect()
            games = fetch_all_games()
            fetch_clock = ticks_add(fetch_clock, fetch_interval_ms)
            game_index = 0  # Reset to start

            if not games:
                show_no_games()
                time.sleep(5)
                continue

        # Time to show next game?
        if ticks_diff(current_time, display_clock) >= display_interval_ms:
            if games:
                # Build and display current game
                game = games[game_index]
                print(f"Showing: {game['league']} - {game['away_team']} @ {game['home_team']}")

                gc.collect()
                game_group = build_game_display(game)
                display.root_group = game_group

                # Advance to next game
                game_index = (game_index + 1) % len(games)

            display_clock = ticks_add(display_clock, display_interval_ms)

        # Small delay to prevent tight loop
        time.sleep(0.1)

    except MemoryError:
        print("Memory error - resetting...")
        gc.collect()
        time.sleep(5)
        microcontroller.reset()

    except Exception as e:
        print(f"Error in main loop: {e}")
        time.sleep(10)
        gc.collect()
        time.sleep(5)
        microcontroller.reset()