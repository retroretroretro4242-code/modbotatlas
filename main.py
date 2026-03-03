import os
import discord
import sqlite3
import threading
import requests
from discord.ext import commands
from datetime import datetime, timedelta
from flask import Flask, redirect, request, session, render_template_string

# ================== ENV ==================
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
SECRET_KEY = os.getenv("SECRET_KEY")

if not all([TOKEN, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SECRET_KEY]):
    raise Exception("Missing required environment variables.")

# ================== DISCORD ==================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================== DATABASE ==================
conn = sqlite3.connect("enterprise.db", check_same_thread=False)
cursor = conn.cursor()
cursor.executescript("""
CREATE TABLE IF NOT EXISTS warnings(guild TEXT, user TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS whitelist(user TEXT);
CREATE TABLE IF NOT EXISTS global_blacklist(user TEXT);
CREATE TABLE IF NOT EXISTS logs(event TEXT, user TEXT, time TEXT);
CREATE TABLE IF NOT EXISTS security(guild TEXT, level TEXT);
""")
conn.commit()

# ================== UTIL ==================
raid_cache = {}
action_cache = {}
bot_join_cache = {}

def log(event, user):
    cursor.execute("INSERT INTO logs VALUES (?, ?, ?)",
                   (event, str(user), str(datetime.utcnow())))
    conn.commit()

def get_security_level(guild_id):
    cursor.execute("SELECT level FROM security WHERE guild=?", (guild_id,))
    r = cursor.fetchone()
    return r[0] if r else "medium"

def is_whitelisted(user_id):
    cursor.execute("SELECT * FROM whitelist WHERE user=?", (str(user_id),))
    return cursor.fetchone() is not None

def is_global_blacklisted(user_id):
    cursor.execute("SELECT * FROM global_blacklist WHERE user=?", (str(user_id),))
    return cursor.fetchone() is not None

# ================== EVENTS ==================
@bot.event
async def on_ready():
    # Guild scoped sync (hızlı slash komut)
    for guild in bot.guilds:
        await bot.tree.sync(guild=guild)
    print(f"Enterprise Bot Online: {bot.user}")

@bot.event
async def on_member_join(member):
    # Global Blacklist
    if is_global_blacklisted(member.id):
        await member.ban(reason="Global Blacklist")
        return

    # Anti Bot Flood
    if member.bot:
        gid = str(member.guild.id)
        bot_join_cache.setdefault(gid, [])
        bot_join_cache[gid].append(datetime.utcnow())
        if len(bot_join_cache[gid]) >= 5:
            diff = (bot_join_cache[gid][-1] - bot_join_cache[gid][0]).seconds
            if diff < 10:
                for m in member.guild.members:
                    if m.bot:
                        await m.kick(reason="Bot Flood")
                bot_join_cache[gid] = []

    # Anti-Raid
    gid = str(member.guild.id)
    raid_cache.setdefault(gid, [])
    raid_cache[gid].append(datetime.utcnow())
    if len(raid_cache[gid]) >= 5:
        diff = (raid_cache[gid][-1] - raid_cache[gid][0]).seconds
        if diff < 10:
            for channel in member.guild.text_channels:
                await channel.edit(slowmode_delay=15)
            await member.guild.system_channel.send("Raid detected.")
            raid_cache[gid] = []

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
        # Auto Restore
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

@bot.tree.command(name="kick")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str="No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message("Kicked.")
    log("Kick", interaction.user)

@bot.tree.command(name="lockdown")
async def lockdown(interaction: discord.Interaction):
    for c in interaction.guild.text_channels:
        await c.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message("Server locked.")
    log("Lockdown", interaction.user)

@bot.tree.command(name="unlock")
async def unlock(interaction: discord.Interaction):
    for c in interaction.guild.text_channels:
        await c.set_permissions(interaction.guild.default_role, send_messages=True)
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

# ================== DASHBOARD ==================
app = Flask(__name__)
app.secret_key = SECRET_KEY

@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    cursor.execute("SELECT * FROM logs ORDER BY time DESC")
    logs = cursor.fetchall()
    return render_template_string("""
    <h1>Atlas Enterprise Dashboard</h1>
    <a href='/logout'>Logout</a>
    <hr>
    {% for l in logs %}
        <p>{{l}}</p>
    {% endfor %}
    """, logs=logs)

@app.route("/login")
def login():
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    token = r.json().get("access_token")
    user = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token}"}).json()
    session["user"] = user
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ================== RUN ==================
def run_bot():
    bot.run(TOKEN)

threading.Thread(target=run_bot).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
