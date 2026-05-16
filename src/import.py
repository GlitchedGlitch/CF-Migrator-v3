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

        # milliseconds timestamp safeguard
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


def resolve_special_id(
    model_dict,
    exclusive_cf_to_bd,
    event_cf_to_bd,
):
    """
    Prefer exclusive special over event special.
    Exporter may provide:
      - exclusive_id
      - event_id
      - special_id
    """

    exclusive_id = model_dict.pop("exclusive_id", None)
    event_id = model_dict.pop("event_id", None)

    if exclusive_id is not None:
        mapped = exclusive_cf_to_bd.get(exclusive_id)

        if mapped is not None:
            model_dict["special_id"] = mapped
            return

    if event_id is not None:
        mapped = event_cf_to_bd.get(event_id)

        if mapped is not None:
            model_dict["special_id"] = mapped
            return

    # fallback if exporter already had special_id
    if model_dict.get("special_id") is None:
        model_dict["special_id"] = None


def detailed_skip_reason(
    item_name,
    model_id,
    reason,
    extra=None,
):
    msg = (
        f"{item_name} - "
        f"ID: {model_id} - "
        f"SKIPPED: {reason}"
    )

    if extra:
        msg += f" | DETAILS: {extra}"

    return msg


# ----------- ChatGPT Ends Here -------------


SECTIONS = {
    "R": [
        Regime,
        [
            "id",
            "background",
            "name",
        ],
    ],
    "E": [
        Economy,
        [
            "id",
            "icon",
            "name",
        ],
    ],
    "S-EX": [
        Special,
        [
            "id",
            "catch_phrase",
            "emoji",
            "background",
            "name",
            "rarity",
        ],
    ],
    "S-EV": [
        Special,
        [
            "id",
            "background",
            "catch_phrase",
            "emoji",
            "end_date",
            "hidden",
            "name",
            "rarity",
            "start_date",
            "tradeable",
        ],
    ],
    "B": [
        Ball,
        [
            "id",
            "regime_id",
            "economy_id",
            "country",
            "short_name",
            "catch_names",
            "health",
            "attack",
            "rarity",
            "emoji_id",
            "wild_card",
            "collection_card",
            "credits",
            "capacity_name",
            "capacity_description",
            "enabled",
            "tradeable",
        ],
    ],
    "BI": [
        BallInstance,
        [
            "id",
            "ball_id",
            "catch_date",
            "special_id",
            "favorite",
            "attack_bonus",
            "player_id",
            "server_id",
            "spawned_time",
            "trade_player_id",
            "tradeable",
            "health_bonus",
        ],
    ],
    "P": [
        Player,
        [
            "id",
            "discord_id",
            "donation_policy",
            "privacy_policy",
        ],
    ],
    "GC": [
        GuildConfig,
        [
            "id",
            "enabled",
            "guild_id",
            "spawn_channel",
        ],
    ],
    "F": [
        Friendship,
        [
            "id",
            "player1_id",
            "player2_id",
            "since",
        ],
    ],
    "BU": [
        BlacklistedID,
        [
            "id",
            "date",
            "discord_id",
            "reason",
        ],
    ],
    "BG": [
        BlacklistedGuild,
        [
            "id",
            "date",
            "discord_id",
            "reason",
        ],
    ],
    "T": [
        Trade,
        [
            "id",
            "date",
            "player1_id",
            "player2_id",
        ],
    ],
    "TO": [
        TradeObject,
        [
            "id",
            "ballinstance_id",
            "player_id",
            "trade_id",
        ],
    ],
}


def read_bz2(path: str):
    with bz2.open(path, "rb") as bz2f:
        return bz2f.read().splitlines()


output = []


def reload_embed(
    start_time: float | None = None,
    status="RUNNING",
):
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

    if len(output) > 0:
        recent_output = (
            output[-20:]
            if len(output) > 20
            else output
        )

        output_text = "\n".join(recent_output)

        if len(output_text) > 1000:
            output_text = "...\n" + output_text[-1000:]

        embed.add_field(
            name="Output",
            value=output_text,
        )

    if start_time is not None:
        embed.set_footer(
            text=(
                f"Ended migration in "
                f"{round((time.time() - start_time), 3)}s"
            )
        )

    return embed


async def get_or_create_placeholder_player(
    missing_player_id,
    placeholder_log,
    created_placeholders,
):
    """
    Create a unique placeholder Player for a specific missing player ID.
    """

    placeholder_key = f"Player_{missing_player_id}"

    if placeholder_key in created_placeholders:
        return created_placeholders[placeholder_key]

    placeholder_discord_id = (
        900000000000000000
        + (missing_player_id % 99999999999999999)
    )

    placeholder_player = await Player.filter(
        discord_id=placeholder_discord_id
    ).first()

    if not placeholder_player:
        try:
            donation = DonationPolicy.ALWAYS_ACCEPT

        except AttributeError:
            donation = list(DonationPolicy)[0]

        try:
            privacy = PrivacyPolicy.ALLOW_ALL

        except AttributeError:
            privacy = list(PrivacyPolicy)[0]

        placeholder_player = await Player.create(
            discord_id=placeholder_discord_id,
            donation_policy=donation,
            privacy_policy=privacy,
        )

        placeholder_log.write(
            f"Created placeholder Player "
            f"(discord_id={placeholder_discord_id}, "
            f"DB ID={placeholder_player.pk}) "
            f"for missing Player ID "
            f"{missing_player_id}\n"
        )

    created_placeholders[
        placeholder_key
    ] = placeholder_player.pk

    return placeholder_player.pk


async def send_long_message(
    ctx,
    content,
):
    chunks = [
        content[i : i + 1900]
        for i in range(0, len(content), 1900)
    ]

    for chunk in chunks:
        await ctx.send(f"```{chunk}```")


async def load(message):
    lines = read_bz2("migration.txt.bz2")

    section = ""
    data = {}

    # Maps CF exclusive pk -> BD Special pk
    exclusive_cf_to_bd = {}

    # Maps CF event pk -> BD Special pk
    event_cf_to_bd = {}

    # Shared sequential counter for Special IDs
    special_counter = [1]

    skipped_log = open(
        "skipped_records.log",
        "w",
        encoding="utf-8",
    )

    skipped_log.write(
        "=== MIGRATION SKIPPED RECORDS LOG ===\n"
    )

    skipped_log.write(
        f"Generated: {datetime.now()}\n\n"
    )

    placeholder_log = open(
        "placeholder_assignments.log",
        "w",
        encoding="utf-8",
    )

    placeholder_log.write(
        "=== PLACEHOLDER ASSIGNMENTS LOG ===\n"
    )

    placeholder_log.write(
        f"Generated: {datetime.now()}\n"
    )

    placeholder_log.write(
        "Records assigned to placeholder entities:\n\n"
    )

    created_placeholders = {}

    skip_summary = defaultdict(int)

    output.append(
        f"- Reading migration file with "
        f"{len(lines):,} lines..."
    )

    await message.edit(embed=reload_embed())

    for index, line in enumerate(lines, start=1):
        line = line.decode().rstrip()

        if index % 10000 == 0:
            output[-1] = (
                f"- Reading migration file... "
                f"(line {index:,}/{len(lines):,})"
            )

            await message.edit(
                embed=reload_embed()
            )

        if (
            line.startswith("//")
            or line.startswith("#")
            or line == ""
        ):
            continue

        if line.startswith(":"):
            section = line[1:]

            if section not in SECTIONS:
                raise Exception(
                    f"Invalid section '{section}' "
                    f"detected on line {index}"
                )

            continue

        # Dynamic field names written by exporter
        if line.startswith("#fields:"):
            col_names = (
                line[len("#fields:") :]
                .split("╵")
            )

            if section in SECTIONS:
                SECTIONS[section][1] = col_names

            continue

        if section == "":
            continue

        section_full = SECTIONS[section]

        if section_full[1] is None:
            raise Exception(
                f"No #fields header found before "
                f"data in section '{section}'"
            )

        bucket_key = (
            section_full[0],
            section,
        )

        if bucket_key not in data:
            data[bucket_key] = []

        model_dict = {}

        fields = section_full[0]._meta.fields_map

        attribute_index = 0

        for value, line_data in zip(
            section_full[1],
            line.split("╵"),
        ):
            attribute_index += 1

            if (
                value == "id"
                and line_data == ""
            ):
                reason = detailed_skip_reason(
                    section_full[0].__name__,
                    "UNKNOWN",
                    "Empty ID field",
                    (
                        f"line={index}, "
                        f"attribute={attribute_index}"
                    ),
                )

                skipped_log.write(reason + "\n")

                skip_summary[
                    "Empty ID field"
                ] += 1

                model_dict = None
                break

            if line_data == "":
                continue

            if value not in fields:
                if value not in (
                    "exclusive_id",
                    "event_id",
                ):
                    raise Exception(
                        f"Unknown value '{value}' "
                        f"detected on line "
                        f"{index:,}"
                    )

            if line_data == "None":
                line_data = None

            elif line_data == "🬀":
                line_data = True

            elif line_data == "🬁":
                line_data = False

            field_type = fields.get(value)

            if (
                line_data is not None
                and field_type is not None
            ):
                if isinstance(field_type, IntField):
                    line_data = safe_int(line_data)

                elif isinstance(field_type, FloatField):
                    line_data = safe_float(line_data)

                elif isinstance(
                    field_type,
                    DatetimeField,
                ):
                    line_data = safe_datetime(
                        line_data
                    )

                elif isinstance(
                    field_type,
                    DateField,
                ):
                    line_data = safe_date(line_data)

            if isinstance(line_data, str):
                line_data = line_data.replace(
                    "🮈",
                    "\n",
                )

            model_dict[value] = line_data

        if model_dict is not None:
            model_dict["_section"] = section

            data[bucket_key].append(model_dict)

    output.append(
        "- Finished reading migration file. "
        "Processing models..."
    )

    await message.edit(embed=reload_embed())

    start_time = time.time()

    inserted_ids = {}

    processing_order = [
        (Regime, "R"),
        (Economy, "E"),
        (Special, "S-EX"),
        (Special, "S-EV"),
        (Ball, "B"),
        (Player, "P"),
        (BallInstance, "BI"),
        (GuildConfig, "GC"),
        (Friendship, "F"),
        (BlacklistedID, "BU"),
        (BlacklistedGuild, "BG"),
        (Trade, "T"),
        (TradeObject, "TO"),
    ]

    for (item, section_key) in processing_order:
        bucket_key = (item, section_key)

        if bucket_key not in data:
            continue

        value = data[bucket_key]

        output.append(
            f"- Processing "
            f"{item.__name__} "
            f"[{section_key}]... "
            f"({len(value):,} records "
            f"to validate)"
        )

        await message.edit(embed=reload_embed())

        fields_map = item._meta.fields_map

        fk_fields = {}

        for (
            field_name,
            field_obj,
        ) in fields_map.items():
            if (
                hasattr(
                    field_obj,
                    "related_model",
                )
                and field_obj.related_model
                is not None
            ):
                fk_fields[
                    field_name
                ] = field_obj.related_model

                fk_fields[
                    field_name + "_id"
                ] = field_obj.related_model

        seen_ids = set()
        unique_values = []

        skipped_count = 0
        fk_violation_count = 0
        null_field_count = 0
        duplicate_count = 0

        for idx, model in enumerate(value):
            if idx > 0 and idx % 5000 == 0:
                output[-1] = (
                    f"- Processing "
                    f"{item.__name__} "
                    f"[{section_key}]... "
                    f"(validated "
                    f"{idx:,}/{len(value):,})"
                )

                await message.edit(
                    embed=reload_embed()
                )

            model_id = model.get("id")

            model.pop("_section", None)

            if model_id is None:
                reason = detailed_skip_reason(
                    item.__name__,
                    "None",
                    "Null ID",
                )

                skipped_log.write(reason + "\n")

                skip_summary["Null ID"] += 1

                skipped_count += 1
                continue

            # ghost player filter
            if item == Player:
                discord_id = model.get(
                    "discord_id"
                )

                try:
                    did_str = str(
                        int(discord_id)
                    )

                    valid = (
                        17
                        <= len(did_str)
                        <= 19
                        and int(discord_id)
                        < 900000000000000000
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    valid = False

                if not valid:
                    reason = detailed_skip_reason(
                        "Player",
                        model_id,
                        (
                            "Invalid Discord ID"
                        ),
                        (
                            f"discord_id="
                            f"{discord_id}, "
                            f"must be 17-19 "
                            f"digits and below "
                            f"placeholder range"
                        ),
                    )

                    skipped_log.write(
                        reason + "\n"
                    )

                    skip_summary[
                        "Invalid Discord ID"
                    ] += 1

                    skipped_count += 1
                    continue

            if model_id in seen_ids:
                reason = detailed_skip_reason(
                    item.__name__,
                    model_id,
                    "Duplicate ID",
                    (
                        "Another record with "
                        "same ID already "
                        "exists in batch"
                    ),
                )

                skipped_log.write(reason + "\n")

                skip_summary[
                    "Duplicate ID"
                ] += 1

                skipped_count += 1
                duplicate_count += 1
                continue

            if item == BallInstance:
                resolve_special_id(
                    model,
                    exclusive_cf_to_bd,
                    event_cf_to_bd,
                )

            has_invalid_fk = False

            for (
                fk_field_name,
                related_model,
            ) in fk_fields.items():
                fk_value = model.get(
                    fk_field_name
                )

                if fk_value is None:
                    continue

                exists_in_current_batch = (
                    related_model == item
                    and fk_value in seen_ids
                )

                exists_in_tracking = (
                    related_model
                    in inserted_ids
                    and fk_value
                    in inserted_ids[
                        related_model
                    ]
                )

                if (
                    not exists_in_current_batch
                    and not exists_in_tracking
                ):
                    exists_in_db = (
                        await related_model
                        .filter(pk=fk_value)
                        .exists()
                    )

                    if not exists_in_db:
                        if related_model == Player:
                            reason = (
                                detailed_skip_reason(
                                    item.__name__,
                                    model_id,
                                    (
                                        "Missing "
                                        "Player FK"
                                    ),
                                    (
                                        f"{fk_field_name}="
                                        f"{fk_value} "
                                        f"does not "
                                        f"exist"
                                    ),
                                )
                            )

                            skipped_log.write(
                                reason + "\n"
                            )

                            skip_summary[
                                "Missing Player FK"
                            ] += 1

                            has_invalid_fk = True
                            fk_violation_count += 1
                            break

                        elif related_model == Special:
                            model[
                                fk_field_name
                            ] = None

                            placeholder_log.write(
                                f"{item.__name__} "
                                f"ID {model_id}: "
                                f"Set "
                                f"{fk_field_name}"
                                f"=None because "
                                f"Special "
                                f"{fk_value} "
                                f"not found\n"
                            )

                        else:
                            reason = (
                                detailed_skip_reason(
                                    item.__name__,
                                    model_id,
                                    (
                                        "Invalid FK"
                                    ),
                                    (
                                        f"{fk_field_name}="
                                        f"{fk_value} "
                                        f"references "
                                        f"missing "
                                        f"{related_model.__name__}"
                                    ),
                                )
                            )

                            skipped_log.write(
                                reason + "\n"
                            )

                            skip_summary[
                                "Invalid FK"
                            ] += 1

                            has_invalid_fk = True
                            fk_violation_count += 1
                            break

            if has_invalid_fk:
                skipped_count += 1
                continue

            skip_record = False

            null_fields = []

            for (
                field_name,
                field_value,
            ) in list(model.items()):
                if (
                    field_value is None
                    and field_name
                    in fields_map
                ):
                    field_obj = fields_map[
                        field_name
                    ]

                    if (
                        hasattr(
                            field_obj,
                            "null",
                        )
                        and not field_obj.null
                    ):
                        if field_name in (
                            "country",
                            "short_name",
                        ):
                            model[
                                field_name
                            ] = "Unknown"

                        elif field_name == "enabled":
                            model[
                                field_name
                            ] = True

                        elif (
                            field_name
                            == "tradeable"
                        ):
                            model[
                                field_name
                            ] = True

                        else:
                            null_fields.append(
                                field_name
                            )

                            skip_record = True

            if skip_record:
                reason = detailed_skip_reason(
                    item.__name__,
                    model_id,
                    (
                        "Null required "
                        "fields"
                    ),
                    (
                        ", ".join(
                            null_fields
                        )
                    ),
                )

                skipped_log.write(reason + "\n")

                skip_summary[
                    "Null required fields"
                ] += 1

                skipped_count += 1
                null_field_count += 1
                continue

            seen_ids.add(model_id)

            if item == Special:
                new_id = special_counter[0]

                special_counter[0] += 1

                if section_key == "S-EX":
                    exclusive_cf_to_bd[
                        model_id
                    ] = new_id

                elif section_key == "S-EV":
                    event_cf_to_bd[
                        model_id
                    ] = new_id

                model["id"] = new_id

            unique_values.append(model)

        output[-1] = (
            f"- Creating "
            f"{item.__name__} "
            f"[{section_key}] "
            f"instances... "
            f"({len(unique_values):,} "
            f"valid records)"
        )

        await message.edit(embed=reload_embed())

        items = []

        validation_fail_count = 0

        for idx, model in enumerate(unique_values):
            if idx > 0 and idx % 5000 == 0:
                output[-1] = (
                    f"- Creating "
                    f"{item.__name__} "
                    f"[{section_key}] "
                    f"instances... "
                    f"({idx:,}/"
                    f"{len(unique_values):,})"
                )

                await message.edit(
                    embed=reload_embed()
                )

            if model.get("short_name") is None:
                model["short_name"] = "Unknown"

            if model.get("country") is None:
                model["country"] = "Unknown"

            if model.get("enabled") is None:
                model["enabled"] = True

            if model.get("tradeable") is None:
                model["tradeable"] = True

            emoji_id = model.get("emoji_id")

            if emoji_id is not None:
                try:
                    emoji_id_int = int(emoji_id)

                    emoji_id_str = str(
                        emoji_id_int
                    )

                    if (
                        len(emoji_id_str)
                        < 17
                        or len(emoji_id_str)
                        > 19
                    ):
                        model[
                            "emoji_id"
                        ] = (
                            1234567890123456789
                        )

                except (
                    ValueError,
                    TypeError,
                ):
                    model[
                        "emoji_id"
                    ] = 1234567890123456789

            try:
                instance = item(**model)

                try:
                    await instance.full_clean()

                except AttributeError:
                    pass

                except ValidationError as ve:
                    reason = (
                        detailed_skip_reason(
                            item.__name__,
                            model.get("id"),
                            (
                                "Validation "
                                "error"
                            ),
                            str(ve)[:500],
                        )
                    )

                    skipped_log.write(
                        reason + "\n"
                    )

                    skip_summary[
                        "Validation error"
                    ] += 1

                    skipped_count += 1
                    validation_fail_count += 1

                    continue

                items.append(instance)

            except (
                ValueError,
                ValidationError,
            ) as e:
                reason = detailed_skip_reason(
                    item.__name__,
                    model.get("id"),
                    "Instantiation error",
                    str(e)[:500],
                )

                skipped_log.write(reason + "\n")

                skip_summary[
                    "Instantiation error"
                ] += 1

                skipped_count += 1
                validation_fail_count += 1

                continue

        output[-1] = (
            f"- Saving "
            f"{item.__name__} "
            f"[{section_key}] "
            f"to database... "
            f"({len(items):,} objects)"
        )

        await message.edit(embed=reload_embed())

        if items:
            try:
                await item.bulk_create(items)

                if item == Special:
                    if (
                        Special
                        not in inserted_ids
                    ):
                        inserted_ids[
                            Special
                        ] = set()

                    for inst in items:
                        inserted_ids[
                            Special
                        ].add(inst.id)

                else:
                    inserted_ids[item] = seen_ids

                await sequence_model(item)

            except Exception as e:
                error_msg = (
                    f"ERROR: "
                    f"{type(e).__name__}: "
                    f"{str(e)[:500]}"
                )

                skipped_log.write(
                    f"\n{item.__name__} "
                    f"[{section_key}] "
                    f"BULK CREATE FAILED: "
                    f"{error_msg}\n"
                )

                output.append(
                    f"- CRITICAL ERROR: "
                    f"{error_msg}"
                )

                await message.edit(
                    embed=reload_embed()
                )

                skipped_log.close()
                placeholder_log.close()

                raise

        msg = (
            f"- Added "
            f"**{len(items):,}** "
            f"{item.__name__} "
            f"[{section_key}] "
            f"objects."
        )

        output[-1] = msg

        await message.edit(embed=reload_embed())

    output.append(
        "- Updating database sequences..."
    )

    await message.edit(embed=reload_embed())

    await sequence_all_models()

    skipped_log.write(
        "\n=== END OF LOG ===\n"
    )

    skipped_log.close()

    placeholder_log.write(
        "\n=== END OF LOG ===\n"
    )

    placeholder_log.close()

    try:
        if os.path.exists(
            "skipped_records.log"
        ):
            shutil.copy(
                "skipped_records.log",
                (
                    "/mnt/user-data/outputs/"
                    "skipped_records.log"
                ),
            )

        if os.path.exists(
            "placeholder_assignments.log"
        ):
            shutil.copy(
                "placeholder_assignments.log",
                (
                    "/mnt/user-data/outputs/"
                    "placeholder_assignments.log"
                ),
            )

        output.append(
            "- Migration complete! "
            "Logs saved to outputs "
            "directory."
        )

    except Exception:
        output.append(
            "- Migration complete! "
            "Logs saved to working "
            "directory."
        )

    await message.edit(
        embed=reload_embed(
            start_time,
            "FINISHED",
        )
    )

    summary_lines = [
        "=== SKIP SUMMARY ===",
    ]

    total_skipped = 0

    for (
        reason,
        count,
    ) in sorted(
        skip_summary.items(),
        key=lambda x: x[1],
        reverse=True,
    ):
        total_skipped += count

        summary_lines.append(
            f"{reason}: {count}"
        )

    summary_lines.append("")
    summary_lines.append(
        f"TOTAL SKIPPED: "
        f"{total_skipped}"
    )

    await send_long_message(
        ctx,
        "\n".join(summary_lines),
    )  # type: ignore # noqa: F821

    # Send detailed log in Discord
    try:
        log_path = (
            "/mnt/user-data/outputs/"
            "skipped_records.log"
        )

        if not os.path.exists(log_path):
            log_path = "skipped_records.log"

        with open(
            log_path,
            "r",
            encoding="utf-8",
        ) as f:
            content = f.read()

        if len(content) <= 1900:
            await ctx.send(
                f"```{content}```"
            )  # type: ignore # noqa: F821

        else:
            await ctx.send(
                file=discord.File(log_path)
            )  # type: ignore # noqa: F821

    except Exception as e:
        await ctx.send(
            f"Failed to send skipped log: "
            f"{str(e)[:200]}"
        )  # type: ignore # noqa: F821


async def sequence_model(model):
    if await model.all().count() == 0:
        return

    try:
        client = Tortoise.get_connection(
            "default"
        )

        last_id = (
            await model.all()
            .order_by("-id")
            .first()
            .values_list(
                "id",
                flat=True,
            )
        )

        await client.execute_query(
            f"SELECT setval("
            f"'{model._meta.db_table}_id_seq', "
            f"{last_id}"
            f");"
        )

    except Exception:
        pass


async def sequence_all_models():
    models = Tortoise.apps.get("models")

    if models is None:
        return

    for model in models.values():
        await sequence_model(model)


async def clear_all_data():
    client = Tortoise.get_connection(
        "default"
    )

    all_models = [
        Regime,
        Economy,
        Special,
        Ball,
        Player,
        GuildConfig,
        Friendship,
        BlacklistedID,
        BlacklistedGuild,
        BallInstance,
        Trade,
        TradeObject,
    ]

    table_names = [
        model._meta.db_table
        for model in all_models
    ]

    if table_names:
        tables_str = ", ".join(
            table_names
        )

        try:
            await client.execute_query(
                f"TRUNCATE TABLE "
                f"{tables_str} "
                f"RESTART IDENTITY "
                f"CASCADE;"
            )

        except Exception as e:
            output.append(
                f"- TRUNCATE failed, "
                f"using fallback: "
                f"{str(e)}"
            )

            for model in reversed(
                all_models
            ):
                await model.all().delete()

            for model in all_models:
                try:
                    table = (
                        model._meta.db_table
                    )

                    await client.execute_query(
                        f"ALTER SEQUENCE "
                        f"{table}_id_seq "
                        f"RESTART WITH 1;"
                    )

                except Exception:
                    pass


async def main():
    if os.path.isdir("carfigures"):
        print(
            "You cannot run this command "
            "from CarFigures."
        )

        return

    if not os.path.isfile(
        "migration.txt.bz2"
    ):
        print(
            "Could not find "
            "`migration.txt.bz2` "
            "migration file."
        )

        return

    try:
        await ctx.send(  # type: ignore # noqa: F821
            "**WARNING**: "
            "All existing data on this bot "
            "will be **CLEARED**.\n"
            "Type `proceed` if you wish "
            "to proceed.\n"
            "Type `cancel` if you wish "
            "to cancel."
        )

        confirm_message = await bot.wait_for(  # type: ignore # noqa: F821
            "message",
            check=lambda m: (
                m.author == ctx.author
                and m.channel
                == ctx.channel
                and m.content.lower()
                in [
                    "proceed",
                    "cancel",
                ]
            ),
            timeout=20,
        )

    except asyncio.TimeoutError:
        await ctx.send(
            "Canceled due to response "
            "timeout."
        )  # type: ignore # noqa: F821

        return

    if (
        confirm_message.content.lower()
        != "proceed"
    ):
        await ctx.send(
            "Canceled due to message "
            "response."
        )  # type: ignore # noqa: F821

        return

    message = await ctx.send(
        embed=reload_embed()
    )  # type: ignore # noqa: F821

    output.append(
        "- Clearing existing data..."
    )

    await message.edit(embed=reload_embed())

    await clear_all_data()

    output.append(
        "- Data cleared successfully. "
        "Starting migration..."
    )

    await message.edit(embed=reload_embed())

    await load(message)


await main()  # type: ignore  # noqa: F704
