"""
CF-Migrator v2 - Export Script
Exports CarFigures database to a compressed file for migration to BallsDex
"""

import bz2
import json
import logging
from datetime import datetime
from pathlib import Path

import discord

log = logging.getLogger("carfigures.migration")


def build_embed(counts: dict, status: str, file_path: str = None, size_mb: float = None) -> discord.Embed:
    embed = discord.Embed(
        title="CF-Migrator Process",
        color=0x00FF00,  # Green
    )
    embed.add_field(name="Status", value=f"**{status}**", inline=False)

    if counts:
        output_lines = []
        labels = {
            "CarType":      "CarType objects",
            "Country":      "Country objects",
            "Event":        "Event objects",
            "Exclusive":    "Exclusive objects",
            "Car":          "Car objects",
            "Player":       "Player objects",
            "CarInstance":  "CarInstance objects",
            "GuildConfig":  "GuildConfig objects",
            "Friendship":   "Friendship objects",
            "BlacklistedUser":  "BlacklistedUser objects",
            "BlacklistedGuild": "BlacklistedGuild objects",
            "Trade":        "Trade objects",
            "TradeObject":  "TradeObject objects",
        }
        for key, label in labels.items():
            if key in counts:
                output_lines.append(f"- Migrated **{counts[key]:,}** {label}.")
        if output_lines:
            embed.add_field(name="Output", value="\n".join(output_lines), inline=False)

    if file_path and size_mb is not None:
        embed.add_field(
            name="File",
            value=f"Saved to `{file_path}` ({size_mb:.2f} MB)",
            inline=False,
        )

    return embed


async def export_cf_data(ctx):
    from carfigures.core.models import (
        CarType, Country, Event, Exclusive, Car,
        Player, CarInstance, GuildConfig, Friendship,
        BlacklistedUser, BlacklistedGuild, Trade, TradeObject,
    )

    counts = {}
    embed = build_embed(counts, "🔄 RUNNING")
    status_msg = await ctx.send(embed=embed)

    data = {
        "export_date": datetime.utcnow().isoformat(),
        "version": "2.0",
        "data": {}
    }

    # 1. CarTypes
    cartypes = await CarType.all()
    data["data"]["cartypes"] = [
        {"pk": ct.pk, "name": ct.name, "image": ct.image,
         "rebirthRequired": ct.rebirthRequired, "fontsPack_id": ct.fontsPack_id}
        for ct in cartypes
    ]
    counts["CarType"] = len(cartypes)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 2. Countries
    countries = await Country.all()
    data["data"]["countries"] = [
        {"pk": c.pk, "name": c.name, "image": c.image}
        for c in countries
    ]
    counts["Country"] = len(countries)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 3. Events
    events = await Event.all()
    data["data"]["events"] = [
        {"pk": e.pk, "name": e.name, "description": e.description,
         "catchPhrase": e.catchPhrase,
         "startDate": e.startDate.isoformat() if e.startDate else None,
         "endDate": e.endDate.isoformat() if e.endDate else None,
         "rarity": e.rarity, "emoji": e.emoji,
         "tradeable": e.tradeable, "hidden": e.hidden}
        for e in events
    ]
    counts["Event"] = len(events)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 4. Exclusives
    exclusives = await Exclusive.all()
    data["data"]["exclusives"] = [
        {"pk": ex.pk, "name": ex.name, "image": ex.image,
         "rarity": ex.rarity, "emoji": ex.emoji,
         "catchPhrase": ex.catchPhrase, "rebirthRequired": ex.rebirthRequired}
        for ex in exclusives
    ]
    counts["Exclusive"] = len(exclusives)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 5. Cars
    cars = await Car.all()
    data["data"]["cars"] = [
        {"pk": c.pk, "fullName": c.fullName, "shortName": c.shortName,
         "catchNames": c.catchNames, "cartype_id": c.cartype_id,
         "country_id": c.country_id, "weight": c.weight,
         "horsepower": c.horsepower, "rarity": c.rarity,
         "enabled": c.enabled, "tradeable": c.tradeable,
         "emoji": c.emoji, "capacityName": c.capacityName,
         "capacityDescription": c.capacityDescription, "carCredits": c.carCredits}
        for c in cars
    ]
    counts["Car"] = len(cars)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 6. Players
    players = await Player.all()
    data["data"]["players"] = [
        {"pk": p.pk, "discord_id": p.discord_id,
         "donationPolicy": p.donationPolicy, "privacyPolicy": p.privacyPolicy,
         "bolts": p.bolts, "rebirths": p.rebirths}
        for p in players
    ]
    counts["Player"] = len(players)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 7. CarInstances
    car_instances = await CarInstance.all()
    data["data"]["car_instances"] = [
        {"pk": ci.pk, "car_id": ci.car_id, "player_id": ci.player_id,
         "catchDate": ci.catchDate.isoformat() if ci.catchDate else None,
         "spawnedTime": ci.spawnedTime.isoformat() if ci.spawnedTime else None,
         "server": ci.server, "exclusive_id": ci.exclusive_id,
         "event_id": ci.event_id, "weightBonus": ci.weightBonus,
         "horsepowerBonus": ci.horsepowerBonus,
         "trade_player_id": ci.trade_player_id,
         "favorite": ci.favorite, "tradeable": ci.tradeable}
        for ci in car_instances
    ]
    counts["CarInstance"] = len(car_instances)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 8. GuildConfigs
    guilds = await GuildConfig.all()
    data["data"]["guilds"] = [
        {"guild_id": g.guild_id, "spawnChannel": g.spawnChannel,
         "spawnRole": g.spawnRole, "enabled": g.enabled}
        for g in guilds
    ]
    counts["GuildConfig"] = len(guilds)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 9. Friendships
    friendships = await Friendship.all()
    data["data"]["friendships"] = [
        {"pk": f.pk, "friender_id": f.friender_id,
         "friended_id": f.friended_id, "bestie": f.bestie,
         "since": f.since.isoformat() if f.since else None}
        for f in friendships
    ]
    counts["Friendship"] = len(friendships)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 10. Blacklisted Users
    bl_users = await BlacklistedUser.all()
    data["data"]["blacklisted_users"] = [
        {"discord_id": bu.discord_id, "reason": bu.reason,
         "date": bu.date.isoformat() if bu.date else None}
        for bu in bl_users
    ]
    counts["BlacklistedUser"] = len(bl_users)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 11. Blacklisted Guilds
    bl_guilds = await BlacklistedGuild.all()
    data["data"]["blacklisted_guilds"] = [
        {"discord_id": bg.discord_id, "reason": bg.reason,
         "date": bg.date.isoformat() if bg.date else None}
        for bg in bl_guilds
    ]
    counts["BlacklistedGuild"] = len(bl_guilds)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 12. Trades
    trades = await Trade.all()
    data["data"]["trades"] = [
        {"pk": t.pk, "player1_id": t.player1_id,
         "player2_id": t.player2_id,
         "date": t.date.isoformat() if t.date else None}
        for t in trades
    ]
    counts["Trade"] = len(trades)
    await status_msg.edit(embed=build_embed(counts, "🔄 RUNNING"))

    # 13. TradeObjects
    trade_objects = await TradeObject.all()
    data["data"]["trade_objects"] = [
        {"trade_id": to.trade_id, "carinstance_id": to.carinstance_id,
         "player_id": to.player_id}
        for to in trade_objects
    ]
    counts["TradeObject"] = len(trade_objects)

    # Compress and save
    json_bytes = json.dumps(data, indent=2).encode("utf-8")
    compressed = bz2.compress(json_bytes)
    output_file = Path("/migration_export.json.bz2")
    output_file.write_bytes(compressed)
    size_mb = len(compressed) / (1024 * 1024)

    # Final embed
    await status_msg.edit(embed=build_embed(counts, "✅ FINISHED", str(output_file), size_mb))

    # Upload file to Discord
    try:
        if size_mb < 25:
            await ctx.send(file=discord.File(str(output_file)))
        else:
            await ctx.send(f"⚠️ File too large to upload ({size_mb:.2f} MB). Download from server at `{output_file}`")
    except Exception as e:
        await ctx.send(f"⚠️ Could not upload file: {e}\nDownload from server at `{output_file}`")

    log.info(f"Export completed: {output_file} ({size_mb:.2f} MB)")


await export_cf_data(ctx)
