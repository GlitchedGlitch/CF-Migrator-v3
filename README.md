# CF-Migrator-v3

![CF-Migrator Thumbnail](https://raw.githubusercontent.com/ItsMeFuture/CF-Migrator-v2/refs/heads/main/assets/thumbnail.png)

## What is CF-Migrator

CF-Migrator is a tool by DotZZ, with help from Susradist, Ceadz, and the amazing people in the [Ballsdex Developers server](https://discord.gg/QyHVf4bxqW) (kudos to you guys <3), that transfers CarFigures data into a Ballsdex instance.

If you're migrating from CarFigures, you should check out [CF-Commands](https://github.com/Caylies/CF-Commands), a package that ports a handful of CF commands to Ballsdex!

## Exporting data from CarFigures

You can export a new migration file by executing the following eval command on your CarFigures bot.

```py
import base64, requests

request = requests.get("https://api.github.com/repos/GlitchedGlitch/CF-Migrator-v3/contents/src/export.py")

await ctx.invoke(
    bot.get_command("eval"),
    body=base64.b64decode(request.json()["content"]).decode()
)
```

## Transferring to Ballsdex

Once your file is generated, you'll need to delete all the bot files and data but keep your images to transfer them later. Download the Ballsdex files and put them into the folder. After that, put the migration.txt file you got from exporting the data on the bot files. You should also migrate your configuration file over to the Ballsdex yaml format and move your images from `/static/uploads` to `/admin_panel/media`.

## Importing data to Ballsdex

You can import data from your migration file by running the following eval command on your Ballsdex bot.

```py
import base64, requests

request = requests.get("https://api.github.com/repos/GlitchedGlitch/CF-Migrator-v3/contents/src/import.py")

await ctx.invoke(
    bot.get_command("eval"),
    body=base64.b64decode(request.json()["content"]).decode()
)
```

Make sure to reload the bot's cache `[p]reloadcache` once you're done importing!

## Additional Information

- Events and Exclusives will now be converted into a single Special.
- Friendships will migrate over to the Ballsdex friendship system due to their systems being compatible. However, the "bestie" system will be removed.
