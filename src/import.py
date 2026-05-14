"""
CF-Migrator v2 - Import Script
Imports CarFigures database export into BallsDex
"""

import bz2
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import discord

log = logging.getLogger("ballsdex.migration")


def build_embed(counts: dict, status: str, skipped: dict = None) -> discord.Embed:
    embed = discord.Embed(
        title="CF-Migrator Process",
        color=0x00FF00,  # Green
    )
    embed.add_field(name="Status", value=f"**{status}**", inline=False)

    if counts:
        output_lines = []
        labels = {
            "Regime":               "Regime objects",
            "Economy":              "Economy objects",
            "Ball":                 "Ball objects",
            "Exclusive->Special":   "Exclusive → Special objects",
            "Event->Special":       "Event → Special objects",
            "GuildConfig":          "GuildConfig objects",
            "Player":               "Player objects",
            "BlacklistedUser":      "BlacklistedUser objects",
            "BlacklistedGuild":     "BlacklistedGuild objects",
            "BallInstance":         "BallInstance objects",
            "Trade":                "Trade objects",
            "TradeObject":          "TradeObject objects",
        }
        for key, label in labels.items():
            if key in counts:
                output_lines.append(f"- Migrated **{counts[key]:,}** {label}.")
        if output_lines:
            embed.add_field(name="Output", value="\n".join(output_lines), inline=False)

    if skipped:
        skip_lines = []
        if skipped.get("players", 0):
            skip_lines.append(f"- Skipped **{skipped['players']}** invalid/ghost players.")
        if skipped.get("instances", 0):
            skip_lines.append(f"- Skipped **{skipped['instances']}** ball instances (invalid player or ball).")
        if skip_lines:
            embed.add_field(name="Warnings", value="\n".join(skip_lines), inline=False)

    return embed


async def import_cf_data(ctx):
    from ballsdex.core.models import (
        Regime,
        Economy,
        Special,
        Ball,
        Player as BDPlayer,
        BallInstance,
        GuildConfig as BDGuildConfig,
        BlacklistedID,
        BlacklistedGuild as BDBlacklistedGuild,
        Trade as BDTrade,
        TradeObject as BDTradeObject,
    )

    migration_file = os.path.isfile("/migration_export.json")
    if not migration_file.exists():
        await ctx.send("❌ **Migration file not found!** Please run export first and upload the file.")
        return

    counts = {}
    skipped = {"players": 0, "instances": 0}

    status_msg = await ctx.send(embed=build_embed(counts, "🔄 RUNNING"))

    compressed = migration_file.read_bytes()
    data = json.loads(bz2.decompress(compressed).decode("utf-8"))["data"]

    # === 1. REGIMES (CarType -> Regime) ===
    regime_id_map = {}
    for ct in data["cartypes"]:
        regime = await Regime.create(name=ct["name"])
        regime_id_map[ct["pk"]] = regime.pk
    counts["Regime"] = len(regime_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 2. ECONOMIES (Country -> Economy) ===
    economy_id_map = {}
    for country in data["countries"]:
        economy = await Economy.create(name=country["name"])
        economy_id_map[country["pk"]] = economy.pk
    counts["Economy"] = len(economy_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 3. BALLS (Car -> Ball) ===
    ball_id_map = {}
    for car in data["cars"]:
        ball = await Ball.create(
            country=car["fullName"],
            short_name=car["shortName"] or car["fullName"][:20],
            catch_names=car["catchNames"] or "",
            regime_id=regime_id_map.get(car["cartype_id"]),
            economy_id=economy_id_map.get(car["country_id"]),
            health=car["weight"],
            attack=car["horsepower"],
            rarity=car["rarity"],
            enabled=car["enabled"],
            tradeable=car["tradeable"],
            emoji_id=str(car["emoji"]) if car["emoji"] else None,
            capacity_name=car["capacityName"] or "Unknown",
            capacity_description=car["capacityDescription"] or "No description",
            capacity_logic={},
        )
        ball_id_map[car["pk"]] = ball.pk
    counts["Ball"] = len(ball_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 4. EXCLUSIVES -> SPECIALS (FIRST, priority IDs) ===
    exclusive_id_map = {}
    for exclusive in data["exclusives"]:
        special = await Special.create(
            name=exclusive["name"],
            catch_phrase=exclusive["catchPhrase"] or f"You caught a special {exclusive['name']}!",
            rarity=exclusive["rarity"],
            start_date=datetime.utcnow() - timedelta(days=365),
            end_date=datetime.utcnow() + timedelta(days=3650),
            tradeable=True,
            emoji_id=str(exclusive["emoji"]) if exclusive["emoji"] else None,
        )
        exclusive_id_map[exclusive["pk"]] = special.pk
    counts["Exclusive->Special"] = len(exclusive_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 5. EVENTS -> SPECIALS (SECOND, after exclusives) ===
    event_id_map = {}
    for event in data["events"]:
        if event["hidden"]:
            continue
        special = await Special.create(
            name=event["name"],
            catch_phrase=event["catchPhrase"] or f"You caught a special {event['name']}!",
            rarity=event["rarity"],
            start_date=datetime.fromisoformat(event["startDate"]) if event["startDate"] else datetime.utcnow(),
            end_date=datetime.fromisoformat(event["endDate"]) if event["endDate"] else datetime.utcnow() + timedelta(days=365),
            tradeable=event["tradeable"],
            emoji_id=str(event["emoji"]) if event["emoji"] else None,
        )
        event_id_map[event["pk"]] = special.pk
    counts["Event->Special"] = len(event_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 6. GUILD CONFIGS ===
    for guild in data["guilds"]:
        await BDGuildConfig.create(
            guild_id=guild["guild_id"],
            spawn_channel=guild["spawnChannel"],
            enabled=guild["enabled"],
        )
    counts["GuildConfig"] = len(data["guilds"])
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 7. PLAYERS (with validation, no ghost players) ===
    player_id_map = {}
    for player in data["players"]:
        discord_id = player["discord_id"]
        # Skip invalid Discord IDs — valid IDs are 17-19 digits
        if not discord_id or not (17000000000000000 <= discord_id <= 9999999999999999999):
            log.warning(f"Skipping invalid player discord_id: {discord_id}")
            skipped["players"] += 1
            continue
        bd_player = await BDPlayer.create(
            discord_id=discord_id,
            donation_policy_flags=player["donationPolicy"],
            privacy_policy_flags=player["privacyPolicy"],
        )
        player_id_map[player["pk"]] = bd_player.pk
    counts["Player"] = len(player_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 8. BLACKLISTED USERS ===
    for bl_user in data["blacklisted_users"]:
        await BlacklistedID.create(
            discord_id=bl_user["discord_id"],
            reason=bl_user["reason"] or "Migrated from CF",
        )
    counts["BlacklistedUser"] = len(data["blacklisted_users"])
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 9. BLACKLISTED GUILDS ===
    for bl_guild in data["blacklisted_guilds"]:
        await BDBlacklistedGuild.create(
            discord_id=bl_guild["discord_id"],
            reason=bl_guild["reason"] or "Migrated from CF",
        )
    counts["BlacklistedGuild"] = len(data["blacklisted_guilds"])
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 10. BALL INSTANCES (correct player mapping, exclusive priority) ===
    ball_instance_id_map = {}
    total_instances = len(data["car_instances"])

    for i, ci in enumerate(data["car_instances"]):
        # Map to correct BD player — skip if player wasn't migrated (was a ghost)
        bd_player_id = player_id_map.get(ci["player_id"])
        if not bd_player_id:
            skipped["instances"] += 1
            continue

        bd_ball_id = ball_id_map.get(ci["car_id"])
        if not bd_ball_id:
            skipped["instances"] += 1
            continue

        # EXCLUSIVE takes priority over EVENT
        bd_special_id = None
        if ci["exclusive_id"]:
            bd_special_id = exclusive_id_map.get(ci["exclusive_id"])
        elif ci["event_id"]:
            bd_special_id = event_id_map.get(ci["event_id"])

        bd_trade_player_id = None
        if ci["trade_player_id"]:
            bd_trade_player_id = player_id_map.get(ci["trade_player_id"])

        ball_instance = await BallInstance.create(
            ball_id=bd_ball_id,
            player_id=bd_player_id,
            catch_date=datetime.fromisoformat(ci["catchDate"]) if ci["catchDate"] else datetime.utcnow(),
            spawned_time=datetime.fromisoformat(ci["spawnedTime"]) if ci["spawnedTime"] else None,
            server_id=ci["server"],
            special_id=bd_special_id,
            health_bonus=ci["weightBonus"],
            attack_bonus=ci["horsepowerBonus"],
            trade_player_id=bd_trade_player_id,
            favorite=ci["favorite"],
            shiny=False,
        )
        ball_instance_id_map[ci["pk"]] = ball_instance.pk

        # Progress update every 10k
        if (i + 1) % 10000 == 0:
            counts["BallInstance"] = len(ball_instance_id_map)
            await status_msg.edit(embed=build_embed(
                counts,
                f"🔄 RUNNING ({i+1:,}/{total_instances:,} instances)",
                skipped,
            ))

    counts["BallInstance"] = len(ball_instance_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 11. TRADES ===
    trade_id_map = {}
    for trade in data["trades"]:
        bd_p1 = player_id_map.get(trade["player1_id"])
        bd_p2 = player_id_map.get(trade["player2_id"])
        if not bd_p1 or not bd_p2:
            continue
        bd_trade = await BDTrade.create(
            player1_id=bd_p1,
            player2_id=bd_p2,
            date=datetime.fromisoformat(trade["date"]) if trade["date"] else datetime.utcnow(),
        )
        trade_id_map[trade["pk"]] = bd_trade.pk
    counts["Trade"] = len(trade_id_map)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING", skipped))

    # === 12. TRADE OBJECTS ===
    for to in data["trade_objects"]:
        bd_trade_id = trade_id_map.get(to["trade_id"])
        bd_ball_instance_id = ball_instance_id_map.get(to["carinstance_id"])
        bd_player_id = player_id_map.get(to["player_id"])
        if not bd_trade_id or not bd_ball_instance_id or not bd_player_id:
            continue
        await BDTradeObject.create(
            trade_id=bd_trade_id,
            ballinstance_id=bd_ball_instance_id,
            player_id=bd_player_id,
        )
    counts["TradeObject"] = len(data["trade_objects"])

    # Final embed
    await status_msg.edit(embed=build_embed(counts, "✅ FINISHED", skipped))
    log.info("Import completed successfully")


await import_cf_data(ctx)
