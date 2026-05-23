import asyncio
import bz2
import os
import shutil
import time
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

__version__ = "1.0.3-cleaned"

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
            return date.fromtimestamp(f)
        return None
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
# ----------- ChatGPT Ends Here -------------

SECTIONS = {
    "R": [Regime, None],
    "E": [Economy, None],
    "S-EX": [Special, None],
    "S-EV": [Special, None],
    "B": [Ball, None],
    "BI": [BallInstance, None],
    "P": [Player, None],
    "GC": [GuildConfig, None],
    "F": [Friendship, None],
    "BU": [BlacklistedID, ["id", "date", "discord_id", "reason"]],
    "BG": [BlacklistedGuild, ["id", "date", "discord_id", "reason"]],
    "T": [Trade, ["id", "date", "player1_id", "player2_id"]],
    "TO": [TradeObject, ["id", "ballinstance_id", "player_id", "trade_id"]],
}

def read_bz2(path: str):
    with bz2.open(path, "rb") as bz2f:
        return bz2f.read().splitlines()

output = []

def reload_embed(start_time: float | None = None, status="RUNNING"):
    embed = discord.Embed(title="BD-Migrator Process", description=f"Status: **{status}**")
    
    if status == "RUNNING":
        embed.color = discord.Color.yellow()
    elif status == "FINISHED":
        embed.color = discord.Color.green()
    elif status == "CANCELED":
        embed.color = discord.Color.red()

    if len(output) > 0:
        recent_output = output[-20:] if len(output) > 20 else output
        output_text = "\n".join(recent_output)
        if len(output_text) > 1000:
            output_text = "...\n" + output_text[-1000:]
        embed.add_field(name="Output", value=output_text)

    if start_time is not None:
        embed.set_footer(text=f"Ended migration in {round((time.time() - start_time), 3)}s")

    return embed


async def get_or_create_placeholder_player(missing_player_id, placeholder_log, created_placeholders):
    """Create a unique placeholder Player for a specific missing player ID."""
    placeholder_key = f"Player_{missing_player_id}"
    if placeholder_key in created_placeholders:
        return created_placeholders[placeholder_key]
    
    placeholder_discord_id = 900000000000000000 + (missing_player_id % 99999999999999999)
    
    placeholder_player = await Player.filter(discord_id=placeholder_discord_id).first()
    
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
        placeholder_log.write(f"Created placeholder Player (discord_id={placeholder_discord_id}, DB ID={placeholder_player.pk}) for missing Player ID {missing_player_id}\n")
    
    created_placeholders[placeholder_key] = placeholder_player.pk
    return placeholder_player.pk


async def load(message):
    lines = read_bz2("migration.txt.bz2")
    section = ""
    data = {}
    # Maps CF exclusive pk -> BD Special pk (set during S-EX processing)
    exclusive_cf_to_bd: dict[int, int] = {}
    # Maps CF event pk -> BD Special pk (set during S-EV processing)
    event_cf_to_bd: dict[int, int] = {}
    # Sequential counter for Special IDs shared across S-EX and S-EV
    # so exclusives get 1,2,3... and events continue from there
    special_counter = [1]  # list so nested code can mutate it

    skipped_log = open("skipped_records.log", "w", encoding="utf-8")
    skipped_log.write("=== MIGRATION SKIPPED RECORDS LOG ===\n")
    skipped_log.write(f"Generated: {datetime.now()}\n\n")
    
    placeholder_log = open("placeholder_assignments.log", "w", encoding="utf-8")
    placeholder_log.write("=== PLACEHOLDER ASSIGNMENTS LOG ===\n")
    placeholder_log.write(f"Generated: {datetime.now()}\n")
    placeholder_log.write("Records assigned to placeholder entities:\n\n")
    
    created_placeholders = {}

    output.append(f"- Reading migration file with {len(lines):,} lines...")
    await message.edit(embed=reload_embed())

    for index, line in enumerate(lines, start=1):
        line = line.decode().rstrip()

        if index % 10000 == 0:
            output[-1] = f"- Reading migration file... (line {index:,}/{len(lines):,})"
            await message.edit(embed=reload_embed())

        if line.startswith("//") or line == "":
            continue

        if line.startswith(":"):
            section = line[1:]
            if section not in SECTIONS:
                raise Exception(f"Invalid section '{section}' detected on line {index}")
            continue

        if line.startswith("#fields:"):
            col_names = line[len("#fields:"):].split("â•µ")
            if section in SECTIONS:
                SECTIONS[section][1] = col_names
            continue

        if line.startswith("#"):
            continue

        if section == "":
            continue

        section_full = SECTIONS[section]

        # Columns must be known before we can parse rows
        if section_full[1] is None:
            raise Exception(f"No #fields header found before data in section '{section}'")

        bucket_key = (section_full[0], section)

        if bucket_key not in data:
            data[bucket_key] = []

        model_dict = {}
        fields = section_full[0]._meta.fields_map
        attribute_index = 0

        for value, line_data in zip(section_full[1], line.split("â•µ")):
            attribute_index += 1

            if value == "id" and line_data == "":
                skipped_log.write(f"Line {index} - {section_full[0].__name__}: SKIPPED - Empty ID field\n")
                model_dict = None
                break
            
            if line_data == "":
                model_dict[value] = None
                continue

            if value not in fields:
                # Skip unknown fields silently (e.g. exclusive_id/event_id not in BD model)
                model_dict[value] = line_data if line_data not in ("", "None") else None
                continue

            if line_data == "None":
                line_data = None
            elif line_data == "ðŸ¬€":
                line_data = True
            elif line_data == "ðŸ¬":
                line_data = False

            field_type = fields[value]

            if line_data is not None:
                if isinstance(field_type, IntField):
                    line_data = safe_int(line_data)
                elif isinstance(field_type, FloatField):
                    line_data = float(line_data)
                elif isinstance(field_type, DatetimeField):
                    line_data = safe_datetime(line_data)
                elif isinstance(field_type, DateField):
                    line_data = safe_date(line_data)

            if isinstance(line_data, str):
                line_data = line_data.replace("ðŸ®ˆ", "\n")

            model_dict[value] = line_data

        if model_dict is not None:
            model_dict['_section'] = section
            data[bucket_key].append(model_dict)

    output.append(f"- Finished reading migration file. Processing models...")
    await message.edit(embed=reload_embed())

    start_time = time.time()
    inserted_ids = {}
    
    # Process S-EX (exclusives) before S-EV (events) so they get lower IDs.
    # Then everything else in dependency order.
    processing_order = [
        (Regime, "R"),
        (Economy, "E"),
        (Special, "S-EX"),   # Exclusives first â€” they get their natural IDs
        (Special, "S-EV"),   # Events second â€” get next available IDs
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
        output.append(f"- Processing {item.__name__} [{section_key}]... ({len(value):,} records to validate)")
        await message.edit(embed=reload_embed())
        
        fields_map = item._meta.fields_map
        
        fk_fields = {}
        for field_name, field_obj in fields_map.items():
            if hasattr(field_obj, 'related_model') and field_obj.related_model is not None:
                fk_fields[field_name] = field_obj.related_model
                fk_fields[field_name + '_id'] = field_obj.related_model
        
        seen_ids = set()
        unique_values = []
        skipped_count = 0
        fk_violation_count = 0
        null_field_count = 0
        duplicate_count = 0
        
        for idx, model in enumerate(value):
            if idx > 0 and idx % 5000 == 0:
                output[-1] = f"- Processing {item.__name__} [{section_key}]... (validated {idx:,}/{len(value):,})"
                await message.edit(embed=reload_embed())
            
            model_id = model.get('id')
            section_type = model.pop('_section', None)
            
            if model_id is None:
                skipped_log.write(f"{item.__name__} [{section_key}] - ID: None - SKIPPED: Null ID\n")
                skipped_count += 1
                continue

            # --- Ghost player filter ---
            # Accept: 17-19 digit Discord IDs, reject 900000000000000000+ placeholders
            if item == Player:
                discord_id = model.get('discord_id')
                try:
                    did_str = str(int(discord_id))
                    valid = 17 <= len(did_str) <= 19
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    skipped_log.write(f"Player - ID: {model_id} - SKIPPED: Invalid discord_id={discord_id}\n")
                    skipped_count += 1
                    continue

            if model_id in seen_ids:
                skipped_log.write(f"{item.__name__} [{section_key}] - ID: {model_id} - SKIPPED: Duplicate ID\n")
                skipped_count += 1
                duplicate_count += 1
                continue
            
            # Validate FK references
            has_invalid_fk = False
            for fk_field_name, related_model in fk_fields.items():
                fk_value = model.get(fk_field_name)
                
                if fk_value is None:
                    if not fk_field_name.endswith('_id'):
                        continue  # relation accessor key, not a real column â€” skip safely
                    base_field_name = fk_field_name[:-3]
                    field_obj = fields_map.get(base_field_name)
                    is_nullable = field_obj is not None and getattr(field_obj, 'null', False)
                    if not is_nullable and base_field_name in fields_map:
                        skipped_log.write(
                            f"{item.__name__} - ID: {model_id} - SKIPPED: "
                            f"Required FK {fk_field_name} is null\n"
                        )
                        has_invalid_fk = True
                        fk_violation_count += 1
                        break
                    continue
                
                if fk_value == 0:
                    base_field_name = fk_field_name[:-3] if fk_field_name.endswith('_id') else fk_field_name
                    field_obj = fields_map.get(base_field_name)
                    is_nullable = field_obj is not None and getattr(field_obj, 'null', False)
                    
                    if is_nullable:
                        model[fk_field_name] = None
                    elif related_model == Player:
                        placeholder_id = await get_or_create_placeholder_player(0, placeholder_log, created_placeholders)
                        if Player not in inserted_ids:
                            inserted_ids[Player] = set()
                        inserted_ids[Player].add(placeholder_id)
                        model[fk_field_name] = placeholder_id
                    else:
                        skipped_log.write(f"{item.__name__} - ID: {model_id} - SKIPPED: {fk_field_name}=0 is invalid\n")
                        has_invalid_fk = True
                        fk_violation_count += 1
                    continue
                
                exists_in_current_batch = related_model == item and fk_value in seen_ids
                exists_in_tracking = related_model in inserted_ids and fk_value in inserted_ids[related_model]
                
                if not exists_in_current_batch and not exists_in_tracking:
                    exists_in_db = await related_model.filter(pk=fk_value).exists()
                    
                    if not exists_in_db:
                        if related_model == Player:
                            # Skip this ball instance â€” don't create ghost players
                            skipped_log.write(f"{item.__name__} - ID: {model_id} - SKIPPED: player_id={fk_value} not found (ghost player avoided)\n")
                            has_invalid_fk = True
                            fk_violation_count += 1
                            break
                        elif related_model == Special:
                            model[fk_field_name] = None
                            placeholder_log.write(f"{item.__name__} ID {model_id}: Set {fk_field_name}=None (Special ID {fk_value} not found)\n")
                        else:
                            skipped_log.write(f"{item.__name__} - ID: {model_id} - SKIPPED: Invalid FK {fk_field_name}={fk_value}\n")
                            has_invalid_fk = True
                            fk_violation_count += 1
                            break
            
            if has_invalid_fk:
                skipped_count += 1
                continue
            
            skip_record = False
            null_fields = []
            defaults_set = []
            
            for field_name, field_value in list(model.items()):
                if field_value is None and field_name in fields_map:
                    field_obj = fields_map[field_name]
                    if hasattr(field_obj, 'null') and not field_obj.null:
                        if field_name in ('country', 'short_name', 'capacity_name', 'capacity_description', 'credits', 'catch_phrase'):
                            model[field_name] = 'Unknown'
                        elif field_name in ('enabled', 'tradeable'):
                            model[field_name] = True
                        elif field_name == 'hidden':
                            model[field_name] = False
                        elif field_name == 'favorite':
                            model[field_name] = False
                        elif field_name in ('health', 'attack', 'rarity', 'health_bonus', 'attack_bonus'):
                            model[field_name] = 0
                        elif field_name == 'emoji_id':
                            model[field_name] = 1234567890123456789
                        elif field_name == 'regime_id':
                            first = await Regime.all().first()
                            model[field_name] = first.pk if first else 1
                        elif field_name == 'donation_policy':
                            model[field_name] = list(DonationPolicy)[0]
                        elif field_name == 'privacy_policy':
                            model[field_name] = list(PrivacyPolicy)[0]
                        elif field_name == 'guild_id':
                            null_fields.append(field_name)
                            skip_record = True
                        else:
                            null_fields.append(field_name)
                            skip_record = True
            
            if skip_record:
                skipped_log.write(f"{item.__name__} - ID: {model_id} - SKIPPED: Null required fields: {', '.join(null_fields)}\n")
                skipped_count += 1
                null_field_count += 1
                continue
                
            seen_ids.add(model_id)

            # Map CF policy enum ints to BD policy enum values
            if item == Player:
                dp = model.get("donation_policy")
                pp = model.get("privacy_policy")
                donation_map = {
                    1: "ALWAYS_ACCEPT",
                    2: "REQUEST_APPROVAL",
                    3: "ALWAYS_DENY",
                    4: "FRIENDS_ONLY",
                }
                privacy_map = {
                    1: "ALLOW_ALL",
                    2: "DENY",
                    3: "FRIENDS",
                    4: "SAME_SERVER",
                }
                try:
                    if dp is not None:
                        model["donation_policy"] = DonationPolicy[donation_map.get(int(dp), "ALWAYS_ACCEPT")]
                except (KeyError, AttributeError):
                    model["donation_policy"] = list(DonationPolicy)[0]
                try:
                    if pp is not None:
                        model["privacy_policy"] = PrivacyPolicy[privacy_map.get(int(pp), "ALLOW_ALL")]
                except (KeyError, AttributeError):
                    model["privacy_policy"] = list(PrivacyPolicy)[0]

            # BI: convert exclusive_id + event_id -> special_id after specials are loaded
            if section_key == "BI":
                excl = model.pop("exclusive_id", None)
                evnt = model.pop("event_id", None)
                excl = None if excl in (None, "None", "") else safe_int(excl)
                evnt = None if evnt in (None, "None", "") else safe_int(evnt)
                if excl and excl in exclusive_cf_to_bd:
                    model["special_id"] = exclusive_cf_to_bd[excl]
                elif evnt and evnt in event_cf_to_bd:
                    model["special_id"] = event_cf_to_bd[evnt]
                else:
                    model["special_id"] = None

            # For specials, replace the original CF ID with the next sequential counter
            # value so S-EX and S-EV never collide in the Special table
            if item == Special:
                new_id = special_counter[0]
                special_counter[0] += 1
                if section_key == "S-EX":
                    exclusive_cf_to_bd[model_id] = new_id
                elif section_key == "S-EV":
                    event_cf_to_bd[model_id] = new_id
                model['id'] = new_id

            unique_values.append(model)
        
        output[-1] = f"- Creating {item.__name__} [{section_key}] instances... ({len(unique_values):,} valid records)"
        await message.edit(embed=reload_embed())
        
        items = []
        validation_fail_count = 0
        
        for idx, model in enumerate(unique_values):
            if idx > 0 and idx % 5000 == 0:
                output[-1] = f"- Creating {item.__name__} [{section_key}] instances... ({idx:,}/{len(unique_values):,})"
                await message.edit(embed=reload_embed())
            
            if model.get('short_name') is None:
                model['short_name'] = 'Unknown'
            if model.get('country') is None:
                model['country'] = 'Unknown'
            if model.get('enabled') is None:
                model['enabled'] = True
            if model.get('tradeable') is None:
                model['tradeable'] = True
            
            emoji_id = model.get('emoji_id')
            if emoji_id is not None:
                try:
                    emoji_id_int = int(emoji_id)
                    emoji_id_str = str(emoji_id_int)
                    if len(emoji_id_str) < 17 or len(emoji_id_str) > 19:
                        model['emoji_id'] = 1234567890123456789
                except (ValueError, TypeError):
                    model['emoji_id'] = 1234567890123456789
            
            try:
                instance = item(**model)
                
                for fk_field_name in list(fk_fields.keys()):
                    if not fk_field_name.endswith('_id'):
                        continue
                    inst_val = getattr(instance, fk_field_name, None)
                    if inst_val == 0:
                        related_model = fk_fields[fk_field_name]
                        base_name = fk_field_name[:-3]
                        field_obj = fields_map.get(base_name)
                        is_nullable = field_obj is not None and getattr(field_obj, 'null', False)
                        if is_nullable:
                            setattr(instance, fk_field_name, None)
                        elif related_model == Player:
                            placeholder_id = await get_or_create_placeholder_player(0, placeholder_log, created_placeholders)
                            if Player not in inserted_ids:
                                inserted_ids[Player] = set()
                            inserted_ids[Player].add(placeholder_id)
                            setattr(instance, fk_field_name, placeholder_id)

                try:
                    await instance.full_clean()
                except AttributeError:
                    pass
                except ValidationError as ve:
                    skipped_log.write(f"{item.__name__} - ID: {model.get('id')} - SKIPPED: Validation error: {str(ve)[:200]}\n")
                    skipped_count += 1
                    validation_fail_count += 1
                    continue
                
                items.append(instance)
            except (ValueError, ValidationError) as e:
                skipped_log.write(f"{item.__name__} - ID: {model.get('id')} - SKIPPED: {str(e)[:200]}\n")
                skipped_count += 1
                validation_fail_count += 1
                continue
        
        output[-1] = f"- Saving {item.__name__} [{section_key}] to database... ({len(items):,} objects)"
        await message.edit(embed=reload_embed())

        if items:
            fixed_count = 0
            STRING_FIELD_TYPES = ('CharField', 'TextField')
            
            for instance in items:
                instance_fields = instance._meta.fields_map
                for field_name, field_obj in instance_fields.items():
                    if hasattr(field_obj, 'related_model'):
                        continue
                    if not (hasattr(field_obj, 'null') and not field_obj.null):
                        continue
                    val = getattr(instance, field_name, None)
                    if val is not None:
                        if field_name == 'emoji_id':
                            if len(str(val)) < 17 or len(str(val)) > 19:
                                setattr(instance, field_name, 1234567890123456789)
                                fixed_count += 1
                        continue
                    field_type = type(field_obj).__name__
                    if field_name == 'emoji_id':
                        setattr(instance, field_name, 1234567890123456789)
                    elif field_type in STRING_FIELD_TYPES:
                        setattr(instance, field_name, 'Unknown')
                    elif field_type == 'IntField':
                        setattr(instance, field_name, 0)
                    elif field_type == 'FloatField':
                        setattr(instance, field_name, 0.0)
                    elif field_type == 'BooleanField':
                        setattr(instance, field_name, False)
                    elif field_type in ('DatetimeField', 'DateField'):
                        setattr(instance, field_name, datetime.now())
                    else:
                        setattr(instance, field_name, 'Unknown')
                    fixed_count += 1
            
            zero_fk_fixed = 0
            for instance in items:
                for attr in list(vars(instance).keys()):
                    if attr.endswith('_id') and not attr.startswith('_'):
                        val = getattr(instance, attr, None)
                        if val == 0:
                            base = attr[:-3]
                            field_obj = instance._meta.fields_map.get(base) or instance._meta.fields_map.get(attr)
                            is_nullable = field_obj is not None and getattr(field_obj, 'null', False)
                            if is_nullable:
                                setattr(instance, attr, None)
                            else:
                                setattr(instance, attr, None)
                            zero_fk_fixed += 1
            
            try:
                await item.bulk_create(items)
                if item == Special:
                    if Special not in inserted_ids:
                        inserted_ids[Special] = set()
                    for inst in items:
                        inserted_ids[Special].add(inst.id)
                else:
                    inserted_ids[item] = seen_ids
                
                await sequence_model(item)
                
            except Exception as e:
                error_msg = f"ERROR: {type(e).__name__}: {str(e)[:500]}"
                skipped_log.write(f"\n{item.__name__} [{section_key}] BULK CREATE FAILED: {error_msg}\n")
                output.append(f"- CRITICAL ERROR: {error_msg}")
                await message.edit(embed=reload_embed())
                skipped_log.close()
                placeholder_log.close()
                raise

        msg = f"- Added **{len(items):,}** {item.__name__} [{section_key}] objects."
        skip_details = []
        if fk_violation_count > 0:
            skip_details.append(f"{fk_violation_count} FK violations")
        if null_field_count > 0:
            skip_details.append(f"{null_field_count} null fields")
        if duplicate_count > 0:
            skip_details.append(f"{duplicate_count} duplicates")
        if validation_fail_count > 0:
            skip_details.append(f"{validation_fail_count} validation errors")
        if skip_details:
            msg += f" (skipped: {', '.join(skip_details)})"
        
        output[-1] = msg
        await message.edit(embed=reload_embed())

    # Apply exclusive/event priority to BallInstances.
    # The export wrote exclusive_id and event_id as separate columns.
    # BD has only special_id, so we now update each instance:
    # exclusive takes priority over event.
    output.append("- Applying exclusive/event priority to ball instances...")
    await message.edit(embed=reload_embed())

    updated = 0
    skipped_special = 0
    async for bi in BallInstance.all().only("id", "special_id"):
        pass  # special_id was already set correctly during import via the
              # exclusive_id/event_id columns â€” see BI processing above.
              # The export puts exclusive_id before event_id in the field list,
              # and the importer picks up the first non-null one as special_id.

    # Send skipped balls summary as a separate message
    skipped_bi_count = sum(
        1 for line in open("skipped_records.log", encoding="utf-8")
        if "BallInstance" in line and "SKIPPED" in line
    )
    if skipped_bi_count > 0:
        await ctx.send(  # type: ignore # noqa: F821
            f"âš ï¸ **{skipped_bi_count} BallInstances were skipped during migration.**\n"
            "Common reasons:\n"
            "- Player no longer exists (ghost player avoided)\n"
            "- Referenced Ball ID not found in migrated data\n"
            "- Required fields were null/invalid\n"
            "Check `skipped_records.log` for the full list."
        )

    output.append("- Updating database sequences...")
    await message.edit(embed=reload_embed())
    
    await sequence_all_models()

    skipped_log.write("\n=== END OF LOG ===\n")
    skipped_log.close()
    placeholder_log.write("\n=== END OF LOG ===\n")
    placeholder_log.close()
    
    try:
        if os.path.exists("skipped_records.log"):
            shutil.copy("skipped_records.log", "/mnt/user-data/outputs/skipped_records.log")
        if os.path.exists("placeholder_assignments.log"):
            shutil.copy("placeholder_assignments.log", "/mnt/user-data/outputs/placeholder_assignments.log")
        output.append("- Migration complete! Logs saved to outputs directory.")
    except Exception:
        output.append("- Migration complete! Logs saved to working directory.")
    
    await message.edit(embed=reload_embed(start_time, "FINISHED"))

    # Send log file
    try:
        log_path = "/mnt/user-data/outputs/skipped_records.log" if os.path.exists("/mnt/user-data/outputs/skipped_records.log") else "skipped_records.log"
        if os.path.exists(log_path):
            await ctx.send(file=discord.File(log_path))  # type: ignore # noqa: F821
    except Exception as e:
        pass

    # Count and report skipped records
    skipped_balls = skipped_players = skipped_bis = 0
    try:
        with open("skipped_records.log", encoding="utf-8") as f:
            for line in f:
                if "Ball [B]" in line and "SKIPPED" in line:
                    skipped_balls += 1
                elif "Player [P]" in line and "SKIPPED" in line:
                    skipped_players += 1
                elif "BallInstance [BI]" in line and "SKIPPED" in line:
                    skipped_bis += 1
    except:
        pass

    if skipped_balls > 0 or skipped_players > 0 or skipped_bis > 0:
        msg = "âš ï¸ **Skipped Records:**\n"
        if skipped_balls > 0:
            msg += f"- **{skipped_balls} Balls**: Required fields null/invalid\n"
        if skipped_players > 0:
            msg += f"- **{skipped_players} Players**: Invalid Discord ID\n"
        if skipped_bis > 0:
            msg += f"- **{skipped_bis} BallInstances**: Missing player/ball or null fields\n"
        await ctx.send(msg)  # type: ignore # noqa: F821


async def sequence_model(model):
    if await model.all().count() == 0:
        return
    try:
        client = Tortoise.get_connection("default")
        last_id = await model.all().order_by("-id").first().values_list("id", flat=True)
        await client.execute_query(f"SELECT setval('{model._meta.db_table}_id_seq', {last_id});")
    except Exception:
        pass


async def sequence_all_models():
    models = Tortoise.apps.get("models")
    if models is None:
        return
    for model in models.values():
        await sequence_model(model)


async def clear_all_data():
    client = Tortoise.get_connection("default")
    all_models = [Regime, Economy, Special, Ball, Player, GuildConfig, Friendship, BlacklistedID, BlacklistedGuild, BallInstance, Trade, TradeObject]
    table_names = [model._meta.db_table for model in all_models]
    if table_names:
        tables_str = ", ".join(table_names)
        try:
            await client.execute_query(f"TRUNCATE TABLE {tables_str} RESTART IDENTITY CASCADE;")
        except Exception as e:
            output.append(f"- TRUNCATE failed, using fallback: {str(e)}")
            for model in reversed(all_models):
                await model.all().delete()
            for model in all_models:
                try:
                    table = model._meta.db_table
                    await client.execute_query(f"ALTER SEQUENCE {table}_id_seq RESTART WITH 1;")
                except Exception:
                    pass


async def main():
    if os.path.isdir("carfigures"):
        print("You cannot run this command from CarFigures.")
        return

    if not os.path.isfile("migration.txt.bz2"):
        print("Could not find `migration.txt.bz2` migration file.")
        return

    try:
        await ctx.send(  # type: ignore # noqa: F821
            "**WARNING**: All existing data on this bot will be **CLEARED**.\n"
            "Type `proceed` if you wish to proceed.\n"
            "Type `cancel` if you wish to cancel."
        )

        confirm_message = await bot.wait_for(  # type: ignore # noqa: F821
            "message",
            check=lambda m: m.author == ctx.author  # type: ignore # noqa: F821
            and m.channel == ctx.channel  # type: ignore # noqa: F821
            and m.content.lower() in ["proceed", "cancel"],
            timeout=20,
        )
    except asyncio.TimeoutError:
        await ctx.send("Canceled due to response timeout.")  # type: ignore # noqa: F821
        return

    if confirm_message.content.lower() != "proceed":
        await ctx.send("Canceled due to message response.")  # type: ignore # noqa: F821
        return

    message = await ctx.send(embed=reload_embed())  # type: ignore # noqa: F821

    output.append("- Clearing existing data...")
    await message.edit(embed=reload_embed())
    
    await clear_all_data()
    
    output.append("- Data cleared successfully. Starting migration...")
    await message.edit(embed=reload_embed())
    
    await load(message)


await main()  # type: ignore  # noqa: F704
