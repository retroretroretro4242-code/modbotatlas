import os
import discord
from discord.ext import commands
from datetime import datetime
import sqlite3

# ================== ENV ==================
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise Exception("Missing TOKEN environment variable.")

# ================== DISCORD BOT ==================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================== DATABASE ==================
conn = sqlite3.connect("enterprise.db")
cursor = conn.cursor()
cursor.executescript("""
CREATE TABLE IF NOT EXISTS whitelist(user TEXT);
CREATE TABLE IF NOT EXISTS global_blacklist(user TEXT);
CREATE TABLE IF NOT EXISTS logs(event TEXT, user TEXT, time TEXT);
CREATE TABLE IF NOT EXISTS security(guild TEXT, level TEXT);
CREATE TABLE IF NOT EXISTS muted(user TEXT, guild TEXT);
""")
conn.commit()

# ================== UTİL ==================
raid_cache = {}
action_cache = {}
bot_join_cache = {}

def log(event, user):
    cursor.execute("INSERT INTO logs VALUES (?, ?, ?)",
                   (event, str(user), str(datetime.utcnow())))
    conn.commit()

def is_whitelisted(user_id):
    cursor.execute("SELECT * FROM whitelist WHERE user=?", (str(user_id),))
    return cursor.fetchone() is not None

def is_global_blacklisted(user_id):
    cursor.execute("SELECT * FROM global_blacklist WHERE user=?", (str(user_id),))
    return cursor.fetchone() is not None

def get_security_level(guild_id):
    cursor.execute("SELECT level FROM security WHERE guild=?", (guild_id,))
    r = cursor.fetchone()
    return r[0] if r else "medium"

# ================== EVENTS ==================
@bot.event
async def on_ready():
    print(f"Enterprise Bot Online: {bot.user}")
    for guild in bot.guilds:
        await bot.tree.sync(guild=guild)

@bot.event
async def on_member_join(member):
    # Global blacklist
    if is_global_blacklisted(member.id):
        await member.ban(reason="Global Blacklist")
        return

    # Anti-raid
    gid = str(member.guild.id)
    raid_cache.setdefault(gid, [])
    raid_cache[gid].append(datetime.utcnow())
    if len(raid_cache[gid]) >= 5:
        diff = (raid_cache[gid][-1] - raid_cache[gid][0]).seconds
        if diff < 10:
            for channel in member.guild.text_channels:
                await channel.edit(slowmode_delay=15)
            if member.guild.system_channel:
                await member.guild.system_channel.send("Raid detected.")
            raid_cache[gid] = []

    # Anti-bot flood
    bot_join_cache.setdefault(gid, [])
    if member.bot:
        bot_join_cache[gid].append(datetime.utcnow())
        if len(bot_join_cache[gid]) >= 5:
            diff = (bot_join_cache[gid][-1] - bot_join_cache[gid][0]).seconds
            if diff < 10:
                for m in member.guild.members:
                    if m.bot:
                        await m.kick(reason="Bot Flood")
                bot_join_cache[gid] = []

# ================== ANTI-NUKE ==================
async def handle_nuke(guild, user, action_type):
    if is_whitelisted(user.id) or user == guild.owner:
        return
    gid = str(guild.id)
    action_cache.setdefault(gid, {})
    action_cache[gid].setdefault(user.id, [])
    action_cache[gid][user.id].append(datetime.utcnow())
    if len(action_cache[gid][user.id]) >= 3:
        diff = (action_cache[gid][user.id][-1] - action_cache[gid][user.id][0]).seconds
        if diff < 10:
            await guild.ban(user, reason="Anti-Nuke Triggered")
            log(f"AntiNuke-{action_type}", user)
            action_cache[gid][user.id] = []

@bot.event
async def on_guild_channel_delete(channel):
    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        user = entry.user
        await handle_nuke(channel.guild, user, "ChannelDelete")
        # Kanalı geri oluştur
        await channel.guild.create_text_channel(name=channel.name, category=channel.category)
        break

@bot.event
async def on_guild_role_delete(role):
    async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        user = entry.user
        await handle_nuke(role.guild, user, "RoleDelete")
        break

# ================== SLASH COMMANDS ==================
@bot.tree.command(name="ban")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str="No reason"):
    if member.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("Hierarchy violation.")
    await member.ban(reason=reason)
    await interaction.response.send_message("Banned.")
    log("Ban", interaction.user)

@bot.tree.command(name="unban")
async def unban(interaction: discord.Interaction, user_id: int):
    user = await bot.fetch_user(user_id)
    await interaction.guild.unban(user)
    await interaction.response.send_message(f"{user} unbanned.")
    log("Unban", interaction.user)

@bot.tree.command(name="kick")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str="No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message("Kicked.")
    log("Kick", interaction.user)

@bot.tree.command(name="lockdown")
async def lockdown(interaction: discord.Interaction):
    for c in interaction.guild.text_channels:
        await c.set_permissions(interaction.guild.default_role, send_messages=False, view_channel=True)
    await interaction.response.send_message("Server locked. Users can see channels but cannot write.")
    log("Lockdown", interaction.user)

@bot.tree.command(name="unlock")
async def unlock(interaction: discord.Interaction):
    for c in interaction.guild.text_channels:
        await c.set_permissions(interaction.guild.default_role, send_messages=True, view_channel=True)
    await interaction.response.send_message("Server unlocked.")
    log("Unlock", interaction.user)

@bot.tree.command(name="security")
async def security(interaction: discord.Interaction, level: str):
    cursor.execute("DELETE FROM security WHERE guild=?", (str(interaction.guild.id),))
    cursor.execute("INSERT INTO security VALUES (?,?)", (str(interaction.guild.id), level))
    conn.commit()
    await interaction.response.send_message(f"Security level set to {level}")
    log("SecurityLevel", interaction.user)

@bot.tree.command(name="whitelist_add")
async def whitelist_add(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("INSERT INTO whitelist VALUES (?)", (str(member.id),))
    conn.commit()
    await interaction.response.send_message("Added to whitelist.")
    log("WhitelistAdd", interaction.user)

@bot.tree.command(name="whitelist_remove")
async def whitelist_remove(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM whitelist WHERE user=?", (str(member.id),))
    conn.commit()
    await interaction.response.send_message("Removed from whitelist.")
    log("WhitelistRemove", interaction.user)

@bot.tree.command(name="antiraid")
async def antiraid(interaction: discord.Interaction, state: str):
    gid = str(interaction.guild.id)
    if state.lower() == "off":
        raid_cache[gid] = []
    await interaction.response.send_message(f"AntiRaid {state}")
    log("AntiRaid", interaction.user)

@bot.tree.command(name="mute")
async def mute(interaction: discord.Interaction, member: discord.Member):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await interaction.guild.create_role(name="Muted")
        for c in interaction.guild.channels:
            await c.set_permissions(muted_role, send_messages=False)
    await member.add_roles(muted_role)
    cursor.execute("INSERT INTO muted VALUES (?,?)", (str(member.id), str(interaction.guild.id)))
    conn.commit()
    await interaction.response.send_message(f"{member} muted.")

@bot.tree.command(name="unmute")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if muted_role:
        await member.remove_roles(muted_role)
    cursor.execute("DELETE FROM muted WHERE user=? AND guild=?", (str(member.id), str(interaction.guild.id)))
    conn.commit()
    await interaction.response.send_message(f"{member} unmuted.")

# ================== RUN ==================
bot.run(TOKEN)
