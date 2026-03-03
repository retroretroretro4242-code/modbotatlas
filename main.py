
import os
import discord
import sqlite3
import asyncio
from flask import Flask, render_template_string
from discord.ext import commands
from datetime import timedelta
import threading

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise Exception("DISCORD_TOKEN environment variable not set.")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Database setup
conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS warnings (
    guild_id TEXT,
    user_id TEXT,
    reason TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS logs (
    event TEXT,
    user TEXT
)
''')

conn.commit()

# Anti-Raid settings
join_cache = {}

# ========== EVENTS ==========

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Enterprise Bot Online: {bot.user}")

@bot.event
async def on_member_join(member):
    guild_id = str(member.guild.id)
    join_cache.setdefault(guild_id, [])
    join_cache[guild_id].append(member.joined_at)

    if len(join_cache[guild_id]) >= 5:
        diff = (join_cache[guild_id][-1] - join_cache[guild_id][0]).seconds
        if diff < 10:
            await member.guild.system_channel.send("⚠️ Raid detected! Slowmode enabled.")
            for channel in member.guild.text_channels:
                await channel.edit(slowmode_delay=10)
            join_cache[guild_id] = []

@bot.event
async def on_guild_channel_delete(channel):
    entry = await channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete).flatten()
    if entry:
        user = entry[0].user
        await channel.guild.ban(user, reason="Anti-Nuke Protection")
        cursor.execute("INSERT INTO logs VALUES (?, ?)", ("Channel Delete Ban", str(user)))
        conn.commit()

@bot.event
async def on_guild_role_delete(role):
    entry = await role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete).flatten()
    if entry:
        user = entry[0].user
        await role.guild.ban(user, reason="Anti-Nuke Protection")
        cursor.execute("INSERT INTO logs VALUES (?, ?)", ("Role Delete Ban", str(user)))
        conn.commit()

# ========== SLASH COMMANDS ==========

@bot.tree.command(name="warn", description="Warn a user.")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    cursor.execute("INSERT INTO warnings VALUES (?, ?, ?)", 
                   (str(interaction.guild.id), str(member.id), reason))
    conn.commit()
    await interaction.response.send_message(f"{member} warned.")

@bot.tree.command(name="warnings", description="Show user warnings.")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("SELECT reason FROM warnings WHERE guild_id=? AND user_id=?", 
                   (str(interaction.guild.id), str(member.id)))
    rows = cursor.fetchall()
    if rows:
        text = "\n".join([f"- {r[0]}" for r in rows])
    else:
        text = "No warnings."
    await interaction.response.send_message(text)

# ========== DASHBOARD ==========

app = Flask(__name__)

@app.route("/")
def dashboard():
    cursor.execute("SELECT * FROM logs")
    logs = cursor.fetchall()
    return render_template_string("""
    <h1>Atlas Enterprise Dashboard</h1>
    <h2>Security Logs</h2>
    <ul>
    {% for log in logs %}
        <li>{{log[0]}} - {{log[1]}}</li>
    {% endfor %}
    </ul>
    """, logs=logs)

def run_dashboard():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# Run Flask in thread
threading.Thread(target=run_dashboard).start()

bot.run(TOKEN)
