import discord
from discord.ext import commands
from discord import app_commands  # ✅ تم إضافة الاستيراد المفقود
import aiosqlite
import time
import asyncio
from datetime import datetime
from typing import Optional, Dict
import traceback

# ✅ توكن البوت الصحيح الذي أرسلته
BOT_TOKEN = ""

# ------------------ إعدادات البوت ------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
# ❌ لا يوجد سطر tree = ... نهائياً

# ------------------ الإعدادات العامة ------------------
CONFIG: Dict = {
    "support_role_id": 0, "category_id": 0,
    "ticket_limit": 3, "cooldown_seconds": 60,
    "next_ticket_number": 1, "db_path": "tickets.db",
    "info_color": 0x3b82f6
}
TICKET_TYPES = ["دعم فني", "استفسار عام", "شكوى", "طلب شراء", "أخرى"]
PRIORITY = [("عادي", 0x95a5a6, "🔵"), ("مهم", 0xf39c12, "🟡"), ("عاجل", 0xe74c3c, "🔴")]

# ------------------ نظام التخزين المؤقت ------------------
class Cache:
    def __init__(self):
        self.tickets = {}
        self.cooldown = {}
        self.support = {}
    def clear(self, uid):
        self.tickets.pop(uid, None)
        self.cooldown.pop(uid, None)
        self.support.pop(uid, None)
cache = Cache()

# ------------------ قاعدة البيانات ------------------
class DB:
    def __init__(self, path):
        self.path = path
        self.conn = None
    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, cid INTEGER UNIQUE, uid INTEGER, 
                type TEXT, priority TEXT, status TEXT DEFAULT 'مفتوحة', created TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                uid INTEGER PRIMARY KEY, count INTEGER DEFAULT 0, last REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT);
        ''')
        await self.conn.commit()
    async def close(self):
        if self.conn: await self.conn.close()
    async def exec(self, q, p=()):
        if not self.conn: return
        await self.conn.execute(q,p); await self.conn.commit()
    async def many(self, lst):
        if not self.conn: return
        async with self.conn.cursor() as cur:
            [await cur.execute(q,p) for q,p in lst]; await self.conn.commit()
    async def get_one(self, q,p=()):
        if not self.conn: return None
        async with self.conn.execute(q,p) as c: return await c.fetchone()
db = DB(CONFIG["db_path"])

# ------------------ دوال مساعدة ------------------
def is_support(m: discord.Member) -> bool:
    if m.id in cache.support: return cache.support[m.id]
    ok = m.guild_permissions.administrator or (CONFIG["support_role_id"] and CONFIG["support_role_id"] in [r.id for r in m.roles])
    cache.support[m.id] = ok
    return ok
async def get_bot(g): return g.get_member(bot.user.id)
async def check_tickets(uid):
    if uid in cache.tickets: return cache.tickets[uid]
    r = await db.get_one("SELECT count FROM users WHERE uid=?", (uid,))
    n = r[0] if r else 0
    cache.tickets[uid] = n
    return n
async def check_cd(uid):
    now = time.time()
    if uid in cache.cooldown: return max(0, CONFIG["cooldown_seconds"] - int(now - cache.cooldown[uid]))
    r = await db.get_one("SELECT last FROM users WHERE uid=?", (uid,))
    if not r or not r[0]: return 0
    cache.cooldown[uid] = r[0]
    return max(0, CONFIG["cooldown_seconds"] - int(now - r[0]))
def gen_id():
    CONFIG["next_ticket_number"] +=1
    return str(CONFIG["next_ticket_number"]-1)
async def save_cfg(k, v):
    CONFIG[k] = v
    await db.exec("REPLACE INTO config VALUES (?,?)", (k, str(v)))

# ------------------ أحداث البوت ------------------
@bot.event
async def on_ready():
    await db.connect()
    for k in CONFIG:
        r = await db.get_one("SELECT v FROM config WHERE k=?", (k,))
        if r:
            try: CONFIG[k] = type(CONFIG[k])(r[0])
            except: pass
    await bot.tree.sync()
    print(f"✅ البوت جاهز بنجاح! اسمه: {bot.user}")

@bot.event
async def on_close(): await db.close()

# ------------------ واجهات التذاكر ------------------
class OpenSelect(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.select(placeholder="اختر نوع التذكرة...", options=[discord.SelectOption(label=t, value=t) for t in TICKET_TYPES])
    async def sel(self, inter, sel):
        t = sel.values[0]
        cd = await check_cd(inter.user.id)
        if cd: return await inter.response.send_message(f"⏳ انتظر {cd} ثانية", ephemeral=True)
        if await check_tickets(inter.user.id) >= CONFIG["ticket_limit"]:
            return await inter.response.send_message("⚠️ وصلت لحد التذاكر المسموحة", ephemeral=True)
        await inter.response.send_message("اختر الأولوية:", view=PrioritySelect(t), ephemeral=True)

class PrioritySelect(discord.ui.View):
    def __init__(self, t): super().__init__(timeout=60); self.t = t
    @discord.ui.select(placeholder="اختر الأولوية...", options=[discord.SelectOption(label=l, value=l, emoji=e) for l,_,e in PRIORITY])
    async def sel(self, inter, sel):
        p = sel.values[0]
        botm = await get_bot(inter.guild)
        if not botm: return await inter.response.send_message("❌ خطأ في الحصول على بيانات البوت", ephemeral=True)
        cat = inter.guild.get_channel(CONFIG["category_id"]) if CONFIG["category_id"] else None
        over = {
            inter.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            inter.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            botm: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True)
        }
        if CONFIG["support_role_id"]:
            r = inter.guild.get_role(CONFIG["support_role_id"])
            if r: over[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        tid = gen_id()
        try: ch = await inter.guild.create_text_channel(f"تذكرة-{tid}", category=cat, overwrites=over)
        except: return await inter.response.send_message("❌ ليس لدي صلاحيات إنشاء قنوات", ephemeral=True)
        now = datetime.now().isoformat()
        await db.many([
            ("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)", (tid, ch.id, inter.user.id, self.t, p, "مفتوحة", now)),
            ("INSERT OR IGNORE INTO users (uid) VALUES (?)", (inter.user.id,)),
            ("UPDATE users SET count=count+1, last=? WHERE uid=?", (time.time(), inter.user.id))
        ])
        cache.clear(inter.user.id)
        clr = next(c for n,c,_ in PRIORITY if n==p)[1]
        emb = discord.Embed(title=f"🎫 تذكرة رقم {tid}", color=clr)
        emb.add_field(name="النوع", value=self.t, inline=True)
        emb.add_field(name="الأولوية", value=p, inline=True)
        emb.add_field(name="صاحب التذكرة", value=inter.user.mention, inline=False)
        await ch.send(embed=emb, view=TicketControl(tid))
        await inter.response.send_message(f"✅ تم إنشاء تذكرتك: {ch.mention}", ephemeral=True)

class TicketControl(discord.ui.View):
    def __init__(self, tid): super().__init__(timeout=None); self.tid = tid
    @discord.ui.button(label="إغلاق", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close(self, inter, btn):
        if not is_support(inter.user): return await inter.response.send_message("❌ لا صلاحية", ephemeral=True)
        d = await db.get_one("SELECT status,uid FROM tickets WHERE id=?", (self.tid,))
        if not d or d[0] != "مفتوحة": return await inter.response.send_message("❌ التذكرة غير موجودة أو مغلقة", ephemeral=True)
        await db.many([("UPDATE tickets SET status='مغلقة' WHERE id=?", (self.tid,)), ("UPDATE users SET count=count-1 WHERE uid=?", (d[1],))])
        cache.clear(d[1])
        await inter.response.send_message("🔒 تم إغلاق التذكرة")
        await inter.channel.edit(name=f"مغلق-{inter.channel.name}")
    @discord.ui.button(label="حذف", style=discord.ButtonStyle.gray, emoji="🗑️")
    async def delete(self, inter, btn):
        if not is_support(inter.user): return await inter.response.send_message("❌ لا صلاحية", ephemeral=True)
        await inter.response.send_message("⏳ جاري الحذف...", ephemeral=True)
        await inter.channel.delete()

# ------------------ الأوامر ------------------
@bot.tree.command(name="لوحة-التذاكر", description="إرسال لوحة فتح التذاكر")
@app_commands.checks.has_permissions(administrator=True)
async def send_panel(inter):
    emb = discord.Embed(title="🎫 نظام التذاكر", description="اختر نوع طلبك لإنشاء تذكرة جديدة", color=CONFIG["info_color"])
    await inter.response.send_message(embed=emb, view=OpenSelect())

@bot.tree.command(name="تعيين-تصنيف", description="تحديد تصنيف قنوات التذاكر")
@app_commands.checks.has_permissions(administrator=True)
async def set_cat(inter, التصنيف: discord.CategoryChannel):
    await save_cfg("category_id", التصنيف.id)
    await inter.response.send_message(f"✅ تم تعيين التصنيف: {التصنيف.name}", ephemeral=True)

@bot.tree.command(name="تعيين-دعم", description="تحديد دور فريق الدعم")
@app_commands.checks.has_permissions(administrator=True)
async def set_role(inter, دور_الدعم: discord.Role):
    await save_cfg("support_role_id", دور_الدعم.id)
    cache.support.clear()
    await inter.response.send_message(f"✅ تم تعيين دور الدعم: {دور_الدعم.name}", ephemeral=True)

# ------------------ تشغيل البوت ------------------
async def main():
    try:
        await bot.start(BOT_TOKEN)
    except Exception as e:
        print(f"❌ خطأ في التشغيل: {str(e)}")
        traceback.print_exc()
    finally:
        if not bot.is_closed(): await bot.close()
        await db.close()

if __name__ == "__main__":
    print("🔄 جاري تشغيل البوت...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        print(f"❌ خطأ حرج: {str(e)}")
        traceback.print_exc()
