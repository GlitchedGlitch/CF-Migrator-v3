import asyncio
import bz2
import os
import shutil
import time
from collections import defaultdict
from datetime import datetime, date

import discord
from tortoise import Tortoise
from tortoise.fields.data import DatetimeField, DateField, FloatField, IntField
from tortoise.exceptions import ValidationError

from ballsdex.core.models import (
    Ball,
    BallInstance,
    BlacklistedGuild,
    BlacklistedID,
    Economy,
    Friendship,
    GuildConfig,
    Player,
    Regime,
    Special,
    Trade,
    TradeObject,
)
from ballsdex.core.models import DonationPolicy, PrivacyPolicy

__version__ = "1.0.4-detailed-logging"


# ----------- ChatGPT Starts Here -------------
def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_datetime(value):
    if value in (None, "", "None"):
        return None

    if isinstance(value, datetime):
        return value

    try:
        f = float(value)

        if 0 <= f <= 4_102_444_800:
            return datetime.fromtimestamp(f)

    except (TypeError, ValueError, OSError):
        pass

    try:
        return datetime.fromisoformat(str(value))

    except (ValueError, TypeError):
        return None


def safe_date(value):
    if value in (None, "", "None"):
        return None

    if isinstance(value, date):
        return value

    try:
        f = float(value)

        if f > 10_000_000_000:
            f = f / 1000

        if 0 <= f <= 4_102_444_800:
            return datetime.fromtimestamp(f).date()

    except (TypeError, ValueError, OSError):
        pass

    try:
        return date.fromisoformat(str(value))

    except (ValueError, TypeError):
        return None


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ensure_required_fields(model_dict, fields_map):
    """
    Prevent Tortoise ValidationError by ensuring required fields are never None.
    """
    for field_name, field_obj in fields_map.items():
        if field_name not in model_dict:
            continue

        if getattr(field_obj, "null", True):
            continue

        if model_dict[field_name] is None:
            if field_name in ("name", "short_name", "country"):
                model_dict[field_name] = "Unknown"
            elif isinstance(field_obj, IntField):
                model_dict[field_name] = 0
            elif isinstance(field_obj, FloatField):
                model_dict[field_name] = 0.0
            elif isinstance(field_obj, DatetimeField):
                model_dict[field_name] = datetime.now()
            elif isinstance(field_obj, DateField):
                model_dict[field_name] = date.today()
            else:
                model_dict[field_name] = "Unknown"


def detailed_skip_reason(item_name, model_id, reason, extra=None):
    msg = f"{item_name} - ID: {model_id} - SKIPPED: {reason}"
    if extra:
        msg += f" | DETAILS: {extra}"
    return msg


# ----------- ChatGPT Ends Here -------------

SECTIONS = {
    "R": [Regime, ["id", "background", "name"]],
    "E": [Economy, ["id", "icon", "name"]],
    "S-EX": [Special, ["id", "catch_phrase", "emoji", "background", "name", "rarity"]],
    "S-EV": [Special, ["id", "background", "catch_phrase", "emoji", "end_date", "hidden", "name", "rarity", "start_date", "tradeable"]],
    "B": [Ball, ["id", "regime_id", "economy_id", "country", "short_name", "catch_names", "health", "attack", "rarity", "emoji_id", "wild_card", "collection_card", "credits", "capacity_name", "capacity_description", "enabled", "tradeable"]],
    "BI": [BallInstance, ["id", "ball_id", "catch_date", "special_id", "favorite", "attack_bonus", "player_id", "server_id", "spawned_time", "trade_player_id", "tradeable", "health_bonus"]],
    "P": [Player, ["id", "discord_id", "donation_policy", "privacy_policy"]],
    "GC": [GuildConfig, ["id", "enabled", "guild_id", "spawn_channel"]],
    "F": [Friendship, ["id", "player1_id", "player2_id", "since"]],
    "BU": [BlacklistedID, ["id", "date", "discord_id", "reason"]],
    "BG": [BlacklistedGuild, ["id", "date", "discord_id", "reason"]],
    "T": [Trade, ["id", "date", "player1_id", "player2_id"]],
    "TO": [TradeObject, ["id", "ballinstance_id", "player_id", "trade_id"]],
}


def read_bz2(path: str):
    with bz2.open(path, "rb") as bz2f:
        return bz2f.read().splitlines()


output = []


def reload_embed(start_time=None, status="RUNNING"):
    embed = discord.Embed(title="BD-Migrator Process", description=f"Status: **{status}**")

    if status == "RUNNING":
        embed.color = discord.Color.yellow()
    elif status == "FINISHED":
        embed.color = discord.Color.green()
    elif status == "CANCELED":
        embed.color = discord.Color.red()

    if output:
        text = "\n".join(output[-20:])
        embed.add_field(name="Output", value=text[:1000])

    if start_time:
        embed.set_footer(text=f"Elapsed {round(time.time() - start_time, 2)}s")

    return embed


async def load(message):
    lines = read_bz2("migration.txt.bz2")

    section = ""
    data = {}

    exclusive_cf_to_bd = {}
    event_cf_to_bd = {}
    special_counter = [1]

    skipped_log = open("skipped_records.log", "w", encoding="utf-8")
    placeholder_log = open("placeholder_assignments.log", "w", encoding="utf-8")

    created_placeholders = {}

    for index, line in enumerate(lines, start=1):
        line = line.decode().rstrip()

        if line.startswith(":"):
            section = line[1:]
            continue

        if line.startswith("#fields:"):
            SECTIONS[section][1] = line[len("#fields:"):].split("╵")
            continue

        if not section or line.startswith("#") or not line:
            continue

        model_class, fields_list = SECTIONS[section]
        fields_map = model_class._meta.fields_map

        model_dict = {}

        for field, value in zip(fields_list, line.split("╵")):
            if value in ("", "None"):
                value = None

            if value == "🬀":
                value = True
            elif value == "🬁":
                value = False

            if field in fields_map:
                field_obj = fields_map[field]
                if isinstance(field_obj, IntField):
                    value = safe_int(value)
                elif isinstance(field_obj, FloatField):
                    value = safe_float(value)
                elif isinstance(field_obj, DatetimeField):
                    value = safe_datetime(value)
                elif isinstance(field_obj, DateField):
                    value = safe_date(value)

            model_dict[field] = value

        ensure_required_fields(model_dict, fields_map)

        # FIX CRITICAL ERROR HERE: prevent None in required CharField like "name"
        if "name" in model_dict and model_dict["name"] is None:
            model_dict["name"] = "Unknown"

        model_dict["_section"] = section

        key = (model_class, section)
        data.setdefault(key, []).append(model_dict)

    # --- INSERTION PHASE ---
    for model_class, section_key in [
        (Regime, "R"),
        (Economy, "E"),
        (Special, "S-EX"),
        (Special, "S-EV"),
        (Ball, "B"),
        (Player, "P"),
        (BallInstance, "BI"),
    ]:
        key = (model_class, section_key)
        if key not in data:
            continue

        items = []

        for row in data[key]:
            try:
                instance = model_class(**row)
                await instance.full_clean()
                items.append(instance)
            except Exception as e:
                skipped_log.write(f"{model_class.__name__} SKIP: {e}\n")

        if items:
            await model_class.bulk_create(items)

    skipped_log.close()
    placeholder_log.close()


async def main():
    message = type("Dummy", (), {"edit": lambda *a, **k: None})()
    await load(message)


await main()
