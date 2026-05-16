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

__version__ = "1.0.5-fixed-validation"


# =========================
# SAFE CONVERSION HELPERS
# =========================

def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value):
    try:
        return float(value)
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


# =========================
# SPECIAL ID RESOLUTION
# =========================

def resolve_special_id(model_dict, exclusive_cf_to_bd, event_cf_to_bd):
    exclusive_id = model_dict.pop("exclusive_id", None)
    event_id = model_dict.pop("event_id", None)

    if exclusive_id is not None and exclusive_id in exclusive_cf_to_bd:
        model_dict["special_id"] = exclusive_cf_to_bd[exclusive_id]
        return

    if event_id is not None and event_id in event_cf_to_bd:
        model_dict["special_id"] = event_cf_to_bd[event_id]
        return

    if "special_id" not in model_dict:
        model_dict["special_id"] = None


# =========================
# REQUIRED FIELD FIX (CRITICAL)
# =========================

def ensure_required_fields(model_dict, fields_map):
    """
    Prevent Tortoise ValidationError BEFORE bulk_create.
    """
    for name, field in fields_map.items():
        if getattr(field, "null", False):
            continue

        if name in model_dict and model_dict[name] is None:
            if isinstance(field, IntField):
                model_dict[name] = 0
            elif isinstance(field, FloatField):
                model_dict[name] = 0.0
            elif isinstance(field, DatetimeField):
                model_dict[name] = datetime.now()
            elif isinstance(field, DateField):
                model_dict[name] = date.today()
            else:
                # THIS FIXES YOUR ERROR: name=None → "Unknown"
                model_dict[name] = "Unknown"


# =========================
# EMBED OUTPUT
# =========================

output = []


def reload_embed(start_time=None, status="RUNNING"):
    embed = discord.Embed(
        title="BD-Migrator Process",
        description=f"Status: **{status}**",
    )

    if status == "RUNNING":
        embed.color = discord.Color.yellow()
    elif status == "FINISHED":
        embed.color = discord.Color.green()
    elif status == "CANCELED":
        embed.color = discord.Color.red()

    if output:
        text = "\n".join(output[-20:])
        if len(text) > 1000:
            text = "...\n" + text[-1000:]
        embed.add_field(name="Output", value=text)

    if start_time:
        embed.set_footer(
            text=f"Ended migration in {round(time.time() - start_time, 3)}s"
        )

    return embed


# =========================
# BZ2 READER
# =========================

def read_bz2(path: str):
    with bz2.open(path, "rb") as f:
        return f.read().splitlines()


# =========================
# MAIN LOADER
# =========================

async def load(message):
    lines = read_bz2("migration.txt.bz2")

    section = ""
    data = {}

    exclusive_cf_to_bd = {}
    event_cf_to_bd = {}
    special_counter = [1]

    skipped_log = open("skipped_records.log", "w", encoding="utf-8")
    placeholder_log = open("placeholder.log", "w", encoding="utf-8")

    fields_override = {}

    output.append(f"- Reading {len(lines):,} lines...")
    await message.edit(embed=reload_embed())

    for i, raw in enumerate(lines, start=1):
        line = raw.decode().rstrip()

        if not line or line.startswith(("#", "//")):
            continue

        if line.startswith(":"):
            section = line[1:]
            continue

        if line.startswith("#fields:"):
            fields_override[section] = line[9:].split("╵")
            continue

        if section not in SECTIONS:
            continue

        model, base_fields = SECTIONS[section]
        fields = fields_override.get(section, base_fields)

        row = {}
        field_map = model._meta.fields_map

        for key, val in zip(fields, line.split("╵")):

            if val in ("", "None"):
                val = None
            elif val == "🬀":
                val = True
            elif val == "🬁":
                val = False

            field_obj = field_map.get(key)

            if isinstance(field_obj, IntField):
                val = safe_int(val)
            elif isinstance(field_obj, FloatField):
                val = safe_float(val)
            elif isinstance(field_obj, DatetimeField):
                val = safe_datetime(val)
            elif isinstance(field_obj, DateField):
                val = safe_date(val)

            row[key] = val

        row["_section"] = section

        key = (model, section)
        data.setdefault(key, []).append(row)

    output.append("- Parsing complete")
    await message.edit(embed=reload_embed())

    inserted = {}

    for model, section_key in SECTIONS.values():

        key = (model, section_key)
        if key not in data:
            continue

        rows = data[key]
        valid = []

        field_map = model._meta.fields_map

        for r in rows:

            model_id = r.get("id")
            if model_id is None:
                continue

            ensure_required_fields(r, field_map)

            if model == Special:
                new_id = special_counter[0]
                special_counter[0] += 1
                r["id"] = new_id

            try:
                obj = model(**r)

                # FINAL SAFETY PASS
                ensure_required_fields(r, field_map)

                await obj.full_clean()
                valid.append(obj)

            except Exception as e:
                skipped_log.write(f"{model.__name__}: {e}\n")

        try:
            await model.bulk_create(valid)
        except Exception as e:
            skipped_log.write(f"BULK FAIL {model.__name__}: {e}\n")
            raise

        inserted[model] = len(valid)

        output.append(f"- Inserted {len(valid)} {model.__name__}")
        await message.edit(embed=reload_embed())

    skipped_log.close()
    placeholder_log.close()

    await message.edit(embed=reload_embed(time.time(), "FINISHED"))


# =========================
# ENTRY POINT
# =========================

async def main():
    message = await ctx.send(embed=reload_embed())  # noqa
    await load(message)


await main()
