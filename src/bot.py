import os
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Define a Cog for admin configuration commands
class AdminConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # /add_channel command
    @app_commands.command(name="add_channel", description="Add a channel for the bot to track")
    @app_commands.describe(channel="Select the channel to add")
    async def add_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        # Check if the user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        channel_id = channel.id

        async with self.bot.pg_pool.acquire() as conn:
            # Insert server info if not already present
            await conn.execute("""
                INSERT INTO servers (guild_id, guild_name)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO NOTHING;
            """, guild_id, interaction.guild.name)
            # Insert channel info into tracked_channels
            await conn.execute("""
                INSERT INTO tracked_channels (guild_id, channel_id)
                VALUES ($1, $2)
                ON CONFLICT (guild_id, channel_id) DO NOTHING;
            """, guild_id, channel_id)

        await interaction.response.send_message(f"Channel {channel.mention} has been added for tracking.", ephemeral=True)

    # /remove_channel command
    @app_commands.command(name="remove_channel", description="Remove a channel from tracking")
    @app_commands.describe(channel="Select the channel to remove")
    async def remove_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        channel_id = channel.id

        async with self.bot.pg_pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM tracked_channels
                WHERE guild_id = $1 AND channel_id = $2;
            """, guild_id, channel_id)
            # Optionally, delete associated messages from your messages table here.
            # await conn.execute("DELETE FROM discord_messages WHERE guild_id = $1 AND channel_id = $2;", guild_id, channel_id)

        await interaction.response.send_message(f"Channel {channel.mention} has been removed from tracking.", ephemeral=True)

    # /config command
    @app_commands.command(name="config", description="Show the tracked channels in this server")
    async def config(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild_id = interaction.guild.id

        async with self.bot.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT channel_id FROM tracked_channels
                WHERE guild_id = $1;
            """, guild_id)

        if rows:
            channels = []
            for row in rows:
                ch = interaction.guild.get_channel(row['channel_id'])
                if ch:
                    channels.append(ch.mention)
                else:
                    channels.append(f"ID:{row['channel_id']}")
            channels_str = ", ".join(channels)
            await interaction.response.send_message(f"Tracked channels in this server: {channels_str}", ephemeral=True)
        else:
            await interaction.response.send_message("No channels are currently being tracked in this server.", ephemeral=True)

# Custom Bot class with database connection setup
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
        self.pg_pool = None

    async def setup_hook(self):
        # Create the asyncpg pool using environment variables
        self.pg_pool = await asyncpg.create_pool(
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            database=os.environ.get('DB_NAME'),
            host=os.environ.get('DB_HOST'),
            port=int(os.environ.get('DB_PORT'))
        )
        # Add our AdminConfig cog
        await self.add_cog(AdminConfig(self))
        # Sync the slash command tree
        await self.tree.sync()

# Instantiate and run the bot
bot = MyBot()
bot.run(os.environ.get('YOUR_DISCORD_BOT_TOKEN'))
