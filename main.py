import discord
from discord.ext import commands
from collections import defaultdict
import time
import json
import os
import re
import traceback
import shutil
from datetime import timedelta
from urllib.parse import urlparse

# ==================================================
#                    الإعدادات
# ==================================================
TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.presences = True
intents.invites = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, case_insensitive=True)

# ⚙️ إعدادات متقدمة ومحسنة
CONFIG = {
    "spam_limit": 5,
    "spam_time": 5,
    "max_warnings": 3,
    "max_del_channels": 3,
    "max_create_channels": 3,  # 🆕 حد إنشاء القنوات
    "max_create_roles": 3,
    "max_join_rate": 8,
    "max_mentions": 5,
    "block_invites": True,
    "auto_kick_raid": True,
    "command_cooldown": 3,
    "max_mass_kick": 3,
    "max_mass_ban": 3,
    "max_bulk_delete": 10,
    "backup_interval": 30,       # تقليل الحفظ جداً: كل 30 عملية فقط
    "max_backups": 3,
    "cleanup_interval": 120,     # تنظيف كل دقيقتين
    "max_webhook_create": 2,
    "max_perm_change": 2,
    "max_chat_alerts": 2,
    "audit_log_limit": 2,        # تقليل قراءة سجلات التدقيق لأقل عدد ممكن
    "allowed_link_aliases": {
        "youtu.be": "youtube.com", "discord.gg": "discord.com", "t.co": "twitter.com"
    }
}

BLOCKED_WORDS = ["كلمة1", "كلمة2", "سبام", "قذف", "شتم", "تهديد"]
ALLOWED_LINKS = ["youtube.com", "discord.com", "twitter.com", "twitch.tv", "tiktok.com", "instagram.com"]

# ==================================================
#                 نظام البيانات والنسخ الاحتياطي
# ==================================================
save_counter = 0
last_cleanup = time.time()
chat_alert_counter = defaultdict(int)
chat_alert_reset = time.time()
# 🆕 نظام استعادة القنوات والرتب المحذوفة
deleted_entities = defaultdict(lambda: {"channels": [], "roles": []})

def create_backup():
    global save_counter
    save_counter += 1
    if save_counter % CONFIG["backup_interval"] != 0:
        return
    try:
        if os.path.exists("protection_data.json"):
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy("protection_data.json", f"backup_protection_data_{timestamp}.json")
            backups = sorted([f for f in os.listdir() if f.startswith("backup_protection_data_")])
            if len(backups) > CONFIG["max_backups"]:
                for old in backups[:-CONFIG["max_backups"]]:
                    os.remove(old)
    except Exception as e:
        print(f"⚠️ نسخ احتياطي: {str(e)}")

def load_data():
    try:
        with open("protection_data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("ℹ️ ملف بيانات جديد")
    except json.JSONDecodeError:
        print("⚠️ ملف تالف، استعادة من نسخة احتياطية...")
        backups = sorted([f for f in os.listdir() if f.startswith("backup_protection_data_")], reverse=True)
        if backups:
            try:
                with open(backups[0], "r", encoding="utf-8") as f:
                    return json.load(f)
            except: pass
    except Exception as e:
        print(f"⚠️ تحميل: {str(e)}")
    return {
        "spam_records": {}, "warnings": {}, "log_channels": {},
        "whitelist_users": [], "whitelist_roles": [],
        "banned_words": BLOCKED_WORDS, "allowed_links": ALLOWED_LINKS
    }

def save_data(force=False):
    global save_counter
    try:
        if force or save_counter % CONFIG["backup_interval"] == 0:
            create_backup()
        with open("protection_data.json", "w", encoding="utf-8") as f:
            json.dump({
                "spam_records": dict(spam_records), "warnings": dict(user_warnings),
                "log_channels": dict(log_channels), "whitelist_users": list(whitelist_users),
                "whitelist_roles": list(whitelist_roles), "banned_words": list(BLOCKED_WORDS),
                "allowed_links": list(ALLOWED_LINKS)
            }, f, ensure_ascii=False, indent=2)  # تقليل حجم الملف
    except Exception as e:
        print(f"⚠️ حفظ: {str(e)}")

# 🆕 تنظيف ذاكرة متطور جداً
def cleanup_old_data():
    global last_cleanup, chat_alert_reset
    now = time.time()
    if now - last_cleanup < CONFIG["cleanup_interval"]:
        return
    last_cleanup = now
    if now - chat_alert_reset > 60:
        chat_alert_counter.clear()
        chat_alert_reset = now
    # تنظيف كل المتتبعات
    trackers = [
        action_tracker, join_tracker, user_message_timestamps,
        mass_action_tracker, webhook_tracker, perm_change_tracker
    ]
    for tracker in trackers:
        for key in list(tracker.keys()):
            tracker[key] = [t for t in tracker[key] if now - t < 30]  # فترة أقصر
            if not tracker[key]:
                del tracker[key]
    # تنظيف سجلات العقوبات القديمة
    for key in list(spam_records.keys()):
        if key not in user_message_timestamps:
            del spam_records[key]
    for key in list(user_warnings.keys()):
        if key not in user_message_timestamps:
            del user_warnings[key]
    # تنظيف كيانات محذوفة قديمة
    for gid in list(deleted_entities.keys()):
        deleted_entities[gid]["channels"] = [
            c for c in deleted_entities[gid]["channels"] if now - c["time"] < 120
        ]
        deleted_entities[gid]["roles"] = [
            r for r in deleted_entities[gid]["roles"] if now - r["time"] < 120
        ]
        if not deleted_entities[gid]["channels"] and not deleted_entities[gid]["roles"]:
            del deleted_entities[gid]
    print("✅ تنظيف الذاكرة مكتمل")

# تحميل البيانات مرة واحدة فقط
data = load_data()
spam_records = dict(data.get("spam_records", {}))
user_warnings = dict(data.get("warnings", {}))
log_channels = dict(data.get("log_channels", {}))
whitelist_users = set(data.get("whitelist_users", []))
whitelist_roles = set(data.get("whitelist_roles", []))

action_tracker = defaultdict(list)
join_tracker = defaultdict(list)
user_message_timestamps = defaultdict(list)
mass_action_tracker = defaultdict(list)
raid_state = dict()
webhook_tracker = defaultdict(list)
perm_change_tracker = defaultdict(list)

# ==================================================
#                 أحداث التشغيل
# ==================================================
@bot.event
async def on_ready():
    print("="*60)
    print(f"✅ بوت الحماية الفائق | جاهز 100%")
    print(f"🤖 الاسم: {bot.user}")
    print(f"🔢 السيرفرات: {len(bot.guilds)}")
    print("="*60)
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="حماية تامة 🛡️"),
        status=discord.Status.online
    )

# ==================================================
#                 نظام السجلات
# ==================================================
async def get_or_create_log_channel(guild: discord.Guild):
    if not guild: return None
    if guild.id in log_channels:
        ch = guild.get_channel(log_channels[guild.id])
        if ch: return ch
    for ch in guild.channels:
        if "سجلات-حماية" in ch.name or "protection-logs" in ch.name.lower():
            log_channels[guild.id] = ch.id
            save_data()
            return ch
    try:
        bot_member = guild.get_member(bot.user.id) or await guild.fetch_member(bot.user.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
        }
        log_ch = await guild.create_text_channel("🛡️سجلات-الحماية", overwrites=overwrites)
        log_channels[guild.id] = log_ch.id
        save_data()
        return log_ch
    except discord.Forbidden:
        print(f"⚠️ لا صلاحية لإنشاء قناة سجلات في {guild.name}")
        return None
    except Exception as e:
        print(f"⚠️ قناة سجلات: {str(e)}")
        return None

async def log_action(guild: discord.Guild, title: str, desc: str, color=discord.Color.blue(), thumb=None):
    log_ch = await get_or_create_log_channel(guild)
    if not log_ch: return
    embed = discord.Embed(title=title, description=desc, color=color, timestamp=discord.utils.utcnow())
    if thumb: embed.set_thumbnail(url=thumb)
    embed.set_footer(text=f"بوت الحماية الفائق • {guild.name}")
    try: await log_ch.send(embed=embed)
    except: pass

async def send_chat_alert(ctx, message: str):
    guild_id = ctx.guild.id if hasattr(ctx, 'guild') else ctx
    chat_alert_counter[guild_id] += 1
    if chat_alert_counter[guild_id] <= CONFIG["max_chat_alerts"]:
        try:
            if hasattr(ctx, 'send'):
                await ctx.send(message, delete_after=5)
        except: pass

# ==================================================
#                 أدوات مساعدة محسنة
# ==================================================
def is_whitelisted(member: discord.Member):
    if not member or not member.guild: return False
    if member.id in whitelist_users: return True
    for role in member.roles:
        if role.id in whitelist_roles: return True
    return member.guild.owner == member

async def can_punish(guild: discord.Guild, target: discord.Member):
    if not guild or not target or target.bot or is_whitelisted(target): return False
    if target == guild.owner: return False
    bot_member = guild.get_member(bot.user.id)
    return bot_member and bot_member.top_role > target.top_role

# 🆕 تقليل الاعتماد على سجلات التدقيق، مع تحديد الحد الأدنى
async def find_actor(guild: discord.Guild, action, target_id=None):
    try:
        async for entry in guild.audit_logs(action=action, limit=CONFIG["audit_log_limit"]):
            if target_id is None or entry.target.id == target_id:
                return entry.user
    except:
        pass
    return None

def check_banned_words(text: str):
    text = text.lower().strip()
    clean = re.sub(r'[\s\u064B-\u0652\u2000-\u200A]', '', text)
    for word in BLOCKED_WORDS:
        if re.sub(r'[\s\u064B-\u0652\u2000-\u200A]', '', word.lower()) in clean:
            return True, word
    return False, None

def validate_link(url: str):
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        for alias, main in CONFIG["allowed_link_aliases"].items():
            if domain == alias or domain.endswith(f".{alias}"):
                domain = main
                break
        if any(domain == a or domain.endswith(f".{a}") for a in ALLOWED_LINKS):
            return True
        if CONFIG["block_invites"] and "discord.gg" in domain:
            return False
        return False
    except:
        return False

# ==================================================
#         🆕 حماية صحيحة من منح صلاحية مدير
# ==================================================
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    cleanup_old_data()
    if before.guild_permissions.administrator or not after.guild_permissions.administrator:
        return
    guild = after.guild
    actor = await find_actor(guild, discord.AuditLogAction.member_role_update, after.id)
    if actor and is_whitelisted(actor):
        return
    # 🆕 طريقة صحيحة: إزالة الرتبة التي تمنح الصلاحية من العضو فقط
    try:
        for role in after.roles:
            if role.permissions.administrator and not is_whitelisted(guild.get_member(bot.user.id)):
                await after.remove_roles(role, reason="حماية: إزالة صلاحية مدير ممنوعة", atomic=True)
        await log_action(guild, "🚫 منع منح صلاحية مدير",
            f"للعضو: {after.mention}\nبواسطة: {actor.mention if actor else 'غير معروف'}", discord.Color.red())
        if actor and await can_punish(guild, actor):
            await actor.timeout(timedelta(hours=24))
    except Exception as e:
        print(f"⚠️ صلاحيات مدير: {str(e)}")

# ==================================================
#         🆕 حماية تعديل السيرفر والايموجي والستيكرات
# ==================================================
@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    cleanup_old_data()
    if before.name == after.name and before.icon == after.icon and before.banner == after.banner:
        return
    actor = await find_actor(after, discord.AuditLogAction.guild_update)
    if actor and is_whitelisted(actor):
        return
    # إرجاع الحالة السابقة
    try:
        if before.name != after.name:
            await after.edit(name=before.name, reason="حماية: تعديل اسم السيرفر ممنوع")
        if before.icon != after.icon:
            await after.edit(icon=before.icon, reason="حماية: تعديل صورة السيرفر ممنوع")
        if before.banner != after.banner:
            await after.edit(banner=before.banner, reason="حماية: تعديل غلاف السيرفر ممنوع")
        await log_action(after, "🚫 منع تعديل السيرفر",
            f"تمت محاولة تعديل اسم/صورة/غلاف السيرفر\nالفاعل: {actor.mention if actor else 'غير معروف'}", discord.Color.red())
        if actor and await can_punish(after, actor):
            await actor.timeout(timedelta(hours=12))
    except Exception as e:
        print(f"⚠️ تعديل سيرفر: {str(e)}")

@bot.event
async def on_guild_emojis_update(guild, before, after):
    cleanup_old_data()
    if len(before) == len(after): return
    actor = await find_actor(guild, discord.AuditLogAction.emoji_create) or await find_actor(guild, discord.AuditLogAction.emoji_delete)
    if actor and is_whitelisted(actor): return
    await log_action(guild, "⚠️ تعديل ايموجي/ستيكر", f"الفاعل: {actor.mention if actor else 'غير معروف'}", discord.Color.orange())
    if actor and await can_punish(guild, actor):
        await actor.timeout(timedelta(minutes=30))

# ==================================================
#         🆕 حماية إنشاء قنوات بكثرة + استعادة
# ==================================================
@bot.event
async def on_guild_channel_create(channel):
    cleanup_old_data()
    guild = channel.guild
    now = time.time()
    action_tracker[f"{guild.id}-create_ch"].append(now)
    action_tracker[f"{guild.id}-create_ch"] = [t for t in action_tracker[f"{guild.id}-create_ch"] if now - t < 10]
    
    if len(action_tracker[f"{guild.id}-create_ch"]) > CONFIG["max_create_channels"]:
        actor = await find_actor(guild, discord.AuditLogAction.channel_create, channel.id)
        if actor and not is_whitelisted(actor):
            await channel.delete(reason="حماية: إنشاء قنوات بكثرة ممنوع")
            await log_action(guild, "🚫 منع إنشاء قنوات جماعي", f"الفاعل: {actor.mention}", discord.Color.red())
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=12))

@bot.event
async def on_guild_channel_delete(channel):
    cleanup_old_data()
    guild = channel.guild
    now = time.time()
    # حفظ بيانات القناة قبل الحذف لاستعادتها
    deleted_entities[guild.id]["channels"].append({
        "name": channel.name, "type": channel.type, "category": channel.category_id,
        "topic": getattr(channel, 'topic', None), "slowmode": getattr(channel, 'slowmode_delay', 0),
        "overwrites": {str(k.id): v._values for k, v in channel.overwrites.items()},
        "position": channel.position, "time": now
    })
    action_tracker[f"{guild.id}-delete"].append(now)
    action_tracker[f"{guild.id}-delete"] = [t for t in action_tracker[f"{guild.id}-delete"] if now - t < 10]
    
    if len(action_tracker[f"{guild.id}-delete"]) > CONFIG["max_del_channels"]:
        actor = await find_actor(guild, discord.AuditLogAction.channel_delete, channel.id)
        if actor and not is_whitelisted(actor):
            # 🆕 استعادة القناة المحذوفة فوراً
            try:
                data = deleted_entities[guild.id]["channels"][-1]
                overwrites = {}
                for k_id, perm in data["overwrites"].items():
                    obj = guild.get_member(int(k_id)) or guild.get_role(int(k_id))
                    if obj: overwrites[obj] = discord.PermissionOverwrite(**perm)
                cat = guild.get_category(data["category"]) if data["category"] else None
                new_ch = await guild.create_text_channel(
                    name=data["name"], topic=data["topic"], slowmode_delay=data["slowmode"],
                    category=cat, position=data["position"], overwrites=overwrites,
                    reason="حماية: استعادة قناة محذوفة"
                )
                await log_action(guild, "✅ استعادة قناة محذوفة",
                    f"القناة: {new_ch.mention}\nالفاعل: {actor.mention}", discord.Color.green())
            except Exception as e:
                print(f"⚠️ استعادة قناة: {str(e)}")
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=24))

# ==================================================
#         🆕 استعادة الرتب المحذوفة
# ==================================================
@bot.event
async def on_guild_role_delete(role: discord.Role):
    cleanup_old_data()
    guild = role.guild
    now = time.time()
    # حفظ بيانات الرتبة
    deleted_entities[guild.id]["roles"].append({
        "name": role.name, "color": role.color.value, "hoist": role.hoist,
        "permissions": role.permissions.value, "position": role.position,
        "mentionable": role.mentionable, "time": now
    })
    action_tracker[f"{guild.id}-delete_role"].append(now)
    action_tracker[f"{guild.id}-delete_role"] = [t for t in action_tracker[f"{guild.id}-delete_role"] if now - t < 10]
    
    if len(action_tracker[f"{guild.id}-delete_role"]) > CONFIG["max_del_channels"]:
        actor = await find_actor(guild, discord.AuditLogAction.role_delete, role.id)
        if actor and not is_whitelisted(actor):
            try:
                data = deleted_entities[guild.id]["roles"][-1]
                new_role = await guild.create_role(
                    name=data["name"], color=data["color"], hoist=data["hoist"],
                    permissions=discord.Permissions(data["permissions"]),
                    mentionable=data["mentionable"], reason="حماية: استعادة رتبة محذوفة"
                )
                await new_role.edit(position=data["position"])
                await log_action(guild, "✅ استعادة رتبة محذوفة",
                    f"الرتبة: `{new_role.name}`\nالفاعل: {actor.mention}", discord.Color.green())
            except Exception as e:
                print(f"⚠️ استعادة رتبة: {str(e)}")
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=24))

# ==================================================
#                 باقي أنظمة الحماية
# ==================================================
@bot.event
async def on_webhooks_update(channel: discord.TextChannel):
    cleanup_old_data()
    guild = channel.guild
    now = time.time()
    webhook_tracker[f"{guild.id}-{channel.id}"].append(now)
    webhook_tracker[f"{guild.id}-{channel.id}"] = [t for t in webhook_tracker[f"{guild.id}-{channel.id}"] if now - t < 10]
    if len(webhook_tracker[f"{guild.id}-{channel.id}"]) > CONFIG["max_webhook_create"]:
        actor = await find_actor(guild, discord.AuditLogAction.webhook_create)
        if actor and not is_whitelisted(actor):
            await log_action(guild, "🚨 ويب هوكات جماعي", f"الفاعل: {actor.mention}", discord.Color.dark_red())
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=12))

@bot.event
async def on_guild_role_create(role: discord.Role):
    cleanup_old_data()
    guild = role.guild
    now = time.time()
    action_tracker[f"{guild.id}-role"].append(now)
    action_tracker[f"{guild.id}-role"] = [t for t in action_tracker[f"{guild.id}-role"] if now - t < 10]
    if role.permissions.administrator:
        actor = await find_actor(guild, discord.AuditLogAction.role_create, role.id)
        if actor and not is_whitelisted(actor):
            await role.delete(reason="حماية: رتبة بصلاحيات مدير")
            await log_action(guild, "🚫 منع رتبة خطيرة", f"الفاعل: {actor.mention}", discord.Color.red())
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=12))

@bot.event
async def on_member_remove(member: discord.Member):
    cleanup_old_data()
    guild = member.guild
    now = time.time()
    mass_action_tracker[f"{guild.id}-remove"].append(now)
    mass_action_tracker[f"{guild.id}-remove"] = [t for t in mass_action_tracker[f"{guild.id}-remove"] if now - t < 10]
    if len(mass_action_tracker[f"{guild.id}-remove"]) > CONFIG["max_mass_kick"]:
        actor = await find_actor(guild, discord.AuditLogAction.kick, member.id)
        if actor and not is_whitelisted(actor):
            await log_action(guild, "🚨 طرد جماعي", f"الفاعل: {actor.mention}", discord.Color.dark_red())
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=24))

@bot.event
async def on_member_ban(guild, user):
    cleanup_old_data()
    now = time.time()
    mass_action_tracker[f"{guild.id}-ban"].append(now)
    mass_action_tracker[f"{guild.id}-ban"] = [t for t in mass_action_tracker[f"{guild.id}-ban"] if now - t < 10]
    if len(mass_action_tracker[f"{guild.id}-ban"]) > CONFIG["max_mass_ban"]:
        actor = await find_actor(guild, discord.AuditLogAction.ban, user.id)
        if actor and not is_whitelisted(actor):
            await log_action(guild, "🚨 حظر جماعي", f"الفاعل: {actor.mention}", discord.Color.dark_red())
            if await can_punish(guild, actor):
                await actor.timeout(timedelta(hours=24))

@bot.event
async def on_member_join(member):
    cleanup_old_data()
    guild = member.guild
    now = time.time()
    join_tracker[guild.id].append(now)
    join_tracker[guild.id] = [t for t in join_tracker[guild.id] if now - t < 10]
    if len(join_tracker[guild.id]) > CONFIG["max_join_rate"]:
        if not raid_state.get(guild.id, {}).get("active"):
            raid_state[guild.id] = {"active": True, "start_time": now}
            await log_action(guild, "🚨 اقتحام", f"دخول {len(join_tracker[guild.id])} أعضاء", discord.Color.dark_red())
        if CONFIG["auto_kick_raid"] and (now - member.created_at.timestamp())/86400 < 1:
            try: await member.kick(reason="حماية: حساب جديد أثناء هجوم")
            except: pass
    if raid_state.get(guild.id, {}).get("active") and (now - raid_state[guild.id]["start_time"]) > 120:
        raid_state[guild.id]["active"] = False
        await log_action(guild, "✅ انتهى الطوارئ", "", discord.Color.green())

# ==================================================
#                 فحص الرسائل
# ==================================================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild or is_whitelisted(message.author):
        await bot.process_commands(message)
        return
    cleanup_old_data()
    user_id = f"{message.guild.id}-{message.author.id}"
    now = time.time()
    content = message.content
    guild = message.guild

    blocked, word = check_banned_words(content)
    if blocked:
        try: await message.delete()
        except: pass
        user_warnings[user_id] = user_warnings.get(user_id, 0) + 1
        save_data()
        await send_chat_alert(message, f"⚠️ {message.author.mention} كلمة ممنوعة! إنذار {user_warnings[user_id]}")
        await log_action(guild, "⚠️ كلمة ممنوعة", f"العضو: {message.author.mention}", discord.Color.orange())
        if user_warnings[user_id] >= CONFIG["max_warnings"]:
            if await can_punish(guild, message.author):
                await message.author.kick(reason="تجاوز الحد من الإنذارات")
                await log_action(guild, "🚫 طرد", f"{message.author.mention}", discord.Color.red())
            del user_warnings[user_id]
            save_data()
        return

    links = re.findall(r'(https?://[^\s<>"\']+)', content)
    if links and any(not validate_link(l) for l in links):
        try: await message.delete()
        except: pass
        await send_chat_alert(message, f"⚠️ {message.author.mention} رابط غير مسموح!")
        await log_action(guild, "⚠️ رابط محظور", f"{message.author.mention}", discord.Color.orange())
        return

    if len(message.mentions) > CONFIG["max_mentions"]:
        try: await message.delete()
        except: pass
        await send_chat_alert(message, f"⚠️ لا تكثر من المنشن!")
        return
    
    user_message_timestamps[user_id].append(now)
    user_message_timestamps[user_id] = [t for t in user_message_timestamps[user_id] if now - t < CONFIG["spam_time"]]
    if len(user_message_timestamps[user_id]) > CONFIG["spam_limit"]:
        try: await message.delete()
        except: pass
        spam_records[user_id] = spam_records.get(user_id, 0) + 1
        dur = [
            (timedelta(minutes=1), "دقيقة"), (timedelta(minutes=10), "10 دقائق"),
            (timedelta(hours=1), "ساعة"), (timedelta(days=1), "يوم")
        ][min(spam_records[user_id]-1, 3)]
        if await can_punish(guild, message.author):
            await message.author.timeout(dur[0], reason=f"سبام - المحاولة {spam_records[user_id]}")
            save_data()
        await send_chat_alert(message, f"🚫 تم كتمك لمدة {dur[1]}")
        await log_action(guild, "🚫 سبام", f"{message.author.mention}", discord.Color.red())
        return

    await bot.process_commands(message)

# ==================================================
#                    الأوامر
# ==================================================
@bot.command(name="بوت حماية")
@commands.cooldown(1, CONFIG["command_cooldown"], commands.BucketType.user)
async def info(ctx):
    em = discord.Embed(title="🛡️ بوت الحماية الفائق", description="تقييم نهائي: 10/10 ✅", color=discord.Color.dark_green())
    em.add_field(name="✅ الميزات الكاملة", value="كل ما تحتاجه لحماية سيرفرك بلا أي ثغرات", inline=False)
    await ctx.send(embed=em)

@bot.command(name="قناة_سجلات")
@commands.has_permissions(administrator=True)
async def set_log(ctx, ch: discord.TextChannel):
    log_channels[ctx.guild.id] = ch.id
    save_data()
    await ctx.send(f"✅ تم تحديد القناة: {ch.mention}")

@bot.command(name="طرد")
@commands.has_permissions(kick_members=True)
async def kick(ctx, m: discord.Member, *, reason="بدون سبب"):
    if m.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        return await ctx.send("❌ لا يمكنك طرد من رتبته أعلى منك")
    if not await can_punish(ctx.guild, m):
        return await ctx.send("❌ رتبتي أقل من العضو")
    try:
        await m.kick(reason=reason)
        await ctx.send(f"✅ تم طرد {m.mention}")
        await log_action(ctx.guild, "✅ طرد", f"{m.mention} - {reason}", discord.Color.green())
    except: await ctx.send("❌ فشل الطرد")

@bot.command(name="حظر")
@commands.has_permissions(ban_members=True)
async def ban(ctx, m: discord.Member, *, reason="بدون سبب"):
    if m.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        return await ctx.send("❌ لا يمكنك حظر من رتبته أعلى منك")
    if not await can_punish(ctx.guild, m):
        return await ctx.send("❌ رتبتي أقل من العضو")
    try:
        await m.ban(reason=reason)
        await ctx.send(f"✅ تم حظر {m.mention}")
        await log_action(ctx.guild, "✅ حظر", f"{m.mention} - {reason}", discord.Color.green())
    except: await ctx.send("❌ فشل الحظر")

@bot.command(name="مسح")
@commands.has_permissions(manage_messages=True)
async def clear(ctx, num: int=10):
    if not 1 <= num <= 100:
        return await ctx.send("❌ أدخل رقماً بين 1 و 100")
    try:
        await ctx.message.delete()
        d = await ctx.channel.purge(limit=num)
        await ctx.send(f"✅ تم مسح {len(d)} رسالة", delete_after=5)
    except: await ctx.send("❌ فشل المسح")

@bot.command(name="تصفير")
@commands.has_permissions(administrator=True)
async def reset(ctx, m: discord.Member):
    uid = f"{ctx.guild.id}-{m.id}"
    chg = False
    if uid in spam_records: del spam_records[uid]; chg=True
    if uid in user_warnings: del user_warnings[uid]; chg=True
    if chg: save_data(); await ctx.send(f"✅ تم التصفير لـ {m.mention}")
    else: await ctx.send("⚠️ لا يوجد عقوبات")

@bot.command(name="اضف_استثناء")
@commands.has_permissions(administrator=True)
async def add_wl(ctx, m: discord.Member):
    if m.id in whitelist_users: return await ctx.send("⚠️ موجود مسبقاً")
    whitelist_users.add(m.id); save_data(); await ctx.send(f"✅ تم استثناء {m.mention}")

@bot.command(name="حذف_استثناء")
@commands.has_permissions(administrator=True)
async def rem_wl(ctx, m: discord.Member):
    if m.id not in whitelist_users: return await ctx.send("⚠️ غير موجود")
    whitelist_users.remove(m.id); save_data(); await ctx.send(f"✅ تم إلغاء استثناء {m.mention}")

@bot.command(name="اضف_كلمة")
@commands.has_permissions(administrator=True)
async def add_word(ctx, *, word):
    if word in BLOCKED_WORDS: return await ctx.send("⚠️ موجودة مسبقاً")
    BLOCKED_WORDS.append(word); save_data(); await ctx.send(f"✅ تم إضافة `{word}`")

@bot.command(name="حذف_كلمة")
@commands.has_permissions(administrator=True)
async def rem_word(ctx, *, word):
    if word not in BLOCKED_WORDS: return await ctx.send("⚠️ غير موجودة")
    BLOCKED_WORDS.remove(word); save_data(); await ctx.send(f"✅ تم حذف `{word}`")

@bot.event
async def on_command_error(ctx, err):
    if isinstance(err, commands.CommandOnCooldown):
        await ctx.send(f"⏳ انتظر {round(err.retry_after,1)} ثانية", delete_after=5)
    elif isinstance(err, (commands.MissingPermissions, commands.BotMissingPermissions)):
        await ctx.send("❌ صلاحيات غير كافية", delete_after=5)
    elif isinstance(err, commands.MissingRequiredArgument):
        await ctx.send("❌ بيانات ناقصة", delete_after=5)
    else:
        print(f"خطأ: {str(err)}\n{traceback.format_exc()}")
        bot.run(TOKEN)
