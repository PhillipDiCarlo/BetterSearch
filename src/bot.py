import os
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

class AdminConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def index_channel(self, channel: discord.TextChannel):
        """Index all messages (i.e. load and store) from the given channel."""
        print(f"Starting indexing for channel '{channel.name}' in guild '{channel.guild.name}'")
        async with self.bot.pg_pool.acquire() as conn:
            async for msg in channel.history(limit=None, oldest_first=True):
                created_at = msg.created_at.replace(tzinfo=None)
                try:
                    await conn.execute("""
                        INSERT INTO public.discord_messages 
                            (id, guild_id, channel_id, author_id, author_name, content, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
                        ON CONFLICT (id) DO NOTHING;
                    """, msg.id, msg.guild.id, msg.channel.id,
                         msg.author.id, msg.author.name, msg.content, created_at)
                except Exception as e:
                    print(f"Error indexing message {msg.id}: {e}")
        print(f"Finished indexing for channel '{channel.name}'")

    @app_commands.command(name="add_channel", description="Add a channel for the bot to track and index its messages")
    @app_commands.describe(channel="Select the channel to add")
    async def add_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        channel_id = channel.id

        async with self.bot.pg_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO public.servers (guild_id, guild_name)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO NOTHING;
            """, guild_id, interaction.guild.name)
            await conn.execute("""
                INSERT INTO public.tracked_channels (guild_id, channel_id)
                VALUES ($1, $2)
                ON CONFLICT (guild_id, channel_id) DO NOTHING;
            """, guild_id, channel_id)

        await interaction.response.send_message(
            f"Channel {channel.mention} has been added for tracking. Initiating indexing of message history.",
            ephemeral=True
        )
        # Start indexing in background
        self.bot.loop.create_task(self.index_channel(channel))

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
                DELETE FROM public.tracked_channels
                WHERE guild_id = $1 AND channel_id = $2;
            """, guild_id, channel_id)
        await interaction.response.send_message(
            f"Channel {channel.mention} has been removed from tracking.",
            ephemeral=True
        )

    @app_commands.command(name="config", description="Show the tracked channels in this server")
    async def config(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        async with self.bot.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT channel_id FROM public.tracked_channels
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
            await interaction.response.send_message(
                f"Tracked channels in this server: {channels_str}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "No channels are currently being tracked in this server.",
                ephemeral=True
            )

class MessageIngestion(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        async with self.bot.pg_pool.acquire() as conn:
            tracked = await conn.fetchrow("""
                SELECT 1 FROM public.tracked_channels
                WHERE guild_id = $1 AND channel_id = $2
            """, message.guild.id, message.channel.id)
        if not tracked:
            return

        created_at = message.created_at.replace(tzinfo=None)
        try:
            async with self.bot.pg_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO public.discord_messages 
                        (id, guild_id, channel_id, author_id, author_name, content, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
                    ON CONFLICT (id) DO NOTHING;
                """, message.id, message.guild.id, message.channel.id,
                     message.author.id, message.author.name, message.content, created_at)
            print(f"Indexed new message {message.id} in channel {message.channel.name}")
        except Exception as e:
            print(f"Error indexing message {message.id}: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.guild is None:
            return

        async with self.bot.pg_pool.acquire() as conn:
            tracked = await conn.fetchrow("""
                SELECT 1 FROM public.tracked_channels
                WHERE guild_id = $1 AND channel_id = $2
            """, after.guild.id, after.channel.id)
        if not tracked:
            return

        try:
            async with self.bot.pg_pool.acquire() as conn:
                await conn.execute("""
                    UPDATE public.discord_messages
                    SET content = $1, updated_at = NOW()
                    WHERE id = $2;
                """, after.content, after.id)
            print(f"Updated indexed message {after.id} in channel {after.channel.name}")
        except Exception as e:
            print(f"Error updating message {after.id}: {e}")

class MessageSearch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="search", description="Search stored messages by keyword")
    @app_commands.describe(query="The search query")
    async def search(self, interaction: discord.Interaction, query: str):
        guild_id = interaction.guild.id
        async with self.bot.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, channel_id, content, author_name, created_at 
                FROM public.discord_messages
                WHERE content_tsv @@ plainto_tsquery('english', $1)
                ORDER BY created_at DESC
                LIMIT 5;
            """, query)
        if rows:
            embed = discord.Embed(
                title="Search Results",
                description=f"Results for query: **{query}**",
                color=discord.Color.blue()
            )
            for row in rows:
                msg_link = f"https://discord.com/channels/{guild_id}/{row['channel_id']}/{row['id']}"
                timestamp = row['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                field_name = f"{row['author_name']} at {timestamp}"
                field_value = f"{row['content']}\n[Jump to Message]({msg_link})"
                embed.add_field(name=field_name, value=field_value, inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("No matching messages found.", ephemeral=True)

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.pg_pool = None

    async def setup_hook(self):
        self.pg_pool = await asyncpg.create_pool(
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            database=os.environ.get('DB_NAME'),
            host=os.environ.get('DB_HOST'),
            port=int(os.environ.get('DB_PORT'))
        )
        await self.add_cog(AdminConfig(self))
        await self.add_cog(MessageIngestion(self))
        await self.add_cog(MessageSearch(self))
        await self.tree.sync()

bot = MyBot()
bot.run(os.environ.get('YOUR_DISCORD_BOT_TOKEN'))
