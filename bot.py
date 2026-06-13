"""
savioursmile Discord bot — Components V2 edition
Replit. Talks to Flask API on Render for /project, /donate, /reply, /status.
Ticket + review systems run locally via SQLite with native discord.py Components V2.

Secrets (Replit → Padlock):
  DISCORD_TOKEN
  API_URL               e.g. https://jordan-api.onrender.com
  API_SECRET
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / FROM_EMAIL
  STAFF_ROLE_ID         comma-separated role IDs
  ADMIN_ROLE_ID         comma-separated role IDs
  LOG_CHANNEL           channel ID for ticket logs
  REVIEW_LOG_CHANNEL    channel ID for review logs (falls back to LOG_CHANNEL)
"""

import os, asyncio, smtplib, sqlite3, random, string, io, time
from datetime import datetime, timezone
from email.mime.text import MIMEText

import aiohttp
import discord
from discord import app_commands, ui, SeparatorSpacing

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN      = os.environ["DISCORD_TOKEN"]
API_URL    = os.environ.get("API_URL", "").rstrip("/")
API_SECRET = os.environ.get("API_SECRET", "")

SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)

LOG_CHANNEL_ID        = os.environ.get("LOG_CHANNEL")
REVIEW_LOG_CHANNEL_ID = os.environ.get("REVIEW_LOG_CHANNEL") or LOG_CHANNEL_ID

BRAND = "savioursmile"

def _role_ids(key: str) -> list[str]:
    return [r.strip() for r in os.environ.get(key, "").split(",") if r.strip()]

STAFF_ROLE_IDS = _role_ids("STAFF_ROLE_ID")
ADMIN_ROLE_IDS = _role_ids("ADMIN_ROLE_ID")

# ── Colors ────────────────────────────────────────────────────────────────────

class C:
    info     = 0x5865F2
    success  = 0x57F287
    error    = 0xED4245
    warning  = 0xFEE75C
    review   = 0xF5A623
    neutral  = 0x2C2F33

# ── Database ──────────────────────────────────────────────────────────────────

os.makedirs("data", exist_ok=True)

def get_db() -> sqlite3.Connection:
    con = sqlite3.connect("data/bot.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con

def init_db():
    with get_db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   TEXT NOT NULL,
                channel_id TEXT NOT NULL UNIQUE,
                user_id    TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'open',
                claimed_by TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                closed_at  TEXT,
                closed_by  TEXT
            );
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id          TEXT PRIMARY KEY,
                guild_id    TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                reviewer    TEXT NOT NULL DEFAULT '',
                rating      INTEGER NOT NULL,
                title       TEXT NOT NULL DEFAULT 'New Review!',
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

init_db()

def cfg_get(guild_id: str, key: str) -> str | None:
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM ticket_config WHERE guild_id=? AND key=?", (guild_id, key)
        ).fetchone()
    return row["value"] if row else None

def cfg_set(guild_id: str, key: str, value: str):
    with get_db() as con:
        con.execute(
            "INSERT OR REPLACE INTO ticket_config (guild_id, key, value) VALUES (?,?,?)",
            (guild_id, key, value)
        )

# ── Components V2 helpers ─────────────────────────────────────────────────────

def _sep(divider: bool = True) -> ui.Separator:
    return ui.Separator(visible=divider, spacing=SeparatorSpacing.small)

def _ts() -> str:
    return f"<t:{int(time.time())}:f>"

def _footer(extra: str | None = None) -> str:
    parts = [BRAND]
    if extra:
        parts.append(extra)
    parts.append(_ts())
    return f"-# {' • '.join(parts)}"

def make_container(
    *,
    color: int = C.neutral,
    author: str | None = None,
    title: str | None = None,
    description: str | None = None,
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
    show_footer: bool = True,
) -> ui.Container:
    """
    Build a Components V2 Container.
    fields = list of (name, value, inline)
    """
    parts: list[ui.Item] = []

    # Header text block
    header_lines = []
    if author:
        header_lines.append(f"-# {author}")
    if title:
        header_lines.append(f"### {title}")
    if description:
        header_lines.append(description)
    if header_lines:
        parts.append(ui.TextDisplay("\n".join(header_lines)))

    # Fields block
    if fields:
        if parts:
            parts.append(_sep(divider=False))
        field_lines = []
        row_buf = []
        for name, value, inline in fields:
            text = f"**{name}**\n{value}"
            if inline:
                row_buf.append(text)
                if len(row_buf) == 3:
                    field_lines.append("\u2003\u2003".join(row_buf))
                    row_buf = []
            else:
                if row_buf:
                    field_lines.append("\u2003\u2003".join(row_buf))
                    row_buf = []
                field_lines.append(text)
        if row_buf:
            field_lines.append("\u2003\u2003".join(row_buf))
        parts.append(ui.TextDisplay("\n\n".join(field_lines)))

    # Footer
    if show_footer or footer:
        if parts:
            parts.append(_sep(divider=True))
        parts.append(ui.TextDisplay(_footer(footer)))

    if not parts:
        parts.append(ui.TextDisplay("\u200b"))

    return ui.Container(*parts, accent_color=color)


def v2_view(*items: ui.Item, timeout: float | None = None) -> ui.LayoutView:
    """Wrap containers + action rows into a LayoutView with components_v2 flag."""
    view = ui.LayoutView(timeout=timeout)
    for item in items:
        view.add_item(item)
    return view


async def send_v2(
    target,          # interaction or channel
    *items: ui.Item,
    ephemeral: bool = False,
    followup: bool = False,
):
    """Send a Components V2 message. target = Interaction or TextChannel."""
    view = v2_view(*items)
    flags = discord.MessageFlags(components_v2=True)
    if ephemeral:
        flags = discord.MessageFlags(components_v2=True, ephemeral=True)

    kwargs = dict(view=view, flags=flags)

    if isinstance(target, discord.Interaction):
        if followup:
            return await target.followup.send(**kwargs, ephemeral=ephemeral)
        if target.response.is_done():
            return await target.followup.send(**kwargs, ephemeral=ephemeral)
        return await target.response.send_message(**kwargs, ephemeral=ephemeral)
    else:
        # TextChannel / similar
        return await target.send(**kwargs)


async def send_log(channel_id: str | None, container: ui.Container):
    if not channel_id:
        return
    ch = bot.get_channel(int(channel_id))
    if ch:
        try:
            await send_v2(ch, container)
        except Exception:
            pass

# ── Permission helpers ────────────────────────────────────────────────────────

def is_staff(member: discord.Member) -> bool:
    ids = STAFF_ROLE_IDS + ADMIN_ROLE_IDS
    if ids:
        return any(str(r.id) in ids for r in member.roles)
    return member.guild_permissions.administrator

def is_admin(member: discord.Member) -> bool:
    if ADMIN_ROLE_IDS:
        return any(str(r.id) in ADMIN_ROLE_IDS for r in member.roles)
    return member.guild_permissions.administrator

# ── Misc ──────────────────────────────────────────────────────────────────────

def review_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=7))

def stars(r: int) -> str:
    return "⭐" * r + "☆" * (5 - r)

def bar(n: int, total: int) -> str:
    filled = round((n / total) * 10) if total else 0
    return "█" * filled + "░" * (10 - filled)

# ── Bot ───────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

bot = Bot()

# ═══════════════════════════════════════════════════════════════════════════════
# TICKET SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

# ── Ticket modal ──────────────────────────────────────────────────────────────

class TicketModal(ui.Modal, title="Create a Ticket"):
    question = ui.TextInput(
        label="What do you need help with?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user  = interaction.user

        with get_db() as con:
            existing = con.execute(
                "SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open'",
                (str(guild.id), str(user.id))
            ).fetchone()

        if existing:
            c = make_container(
                color=C.error,
                title="Already Open",
                description=f"You already have an open ticket: <#{existing['channel_id']}>",
            )
            return await send_v2(interaction, c, ephemeral=True, followup=True)

        # Permission overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True
            ),
        }
        for rid in STAFF_ROLE_IDS + ADMIN_ROLE_IDS:
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_messages=True, attach_files=True
                )

        cat_id = cfg_get(str(guild.id), "ticket_category")
        category = guild.get_channel(int(cat_id)) if cat_id else None
        safe = "".join(c for c in user.name.lower() if c.isalnum())[:20]

        ticket_ch = await guild.create_text_channel(
            name=f"ticket-{safe}",
            overwrites=overwrites,
            category=category,
            topic=f"Ticket by {user.name} ({user.id})",
        )

        with get_db() as con:
            con.execute(
                "INSERT INTO tickets (guild_id, channel_id, user_id, username) VALUES (?,?,?,?)",
                (str(guild.id), str(ticket_ch.id), str(user.id), user.name)
            )

        # Staff ping → delete
        all_ids = STAFF_ROLE_IDS + ADMIN_ROLE_IDS
        if all_ids:
            ping = await ticket_ch.send(" ".join(f"<@&{r}>" for r in all_ids))
            await ping.delete()

        # Welcome container
        welcome = make_container(
            color=C.info,
            author=f"Ticket — {user.name}",
            fields=[
                ("Opened by",  f"<@{user.id}>",  True),
                ("Opened at",  _ts(),             True),
                ("Claimed by", "Unclaimed",        True),
                ("What do you need help with?", self.question.value, False),
            ],
            footer="Use the buttons below to manage this ticket",
        )
        manage_ar = ui.ActionRow(
            ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger,  custom_id="ticket_close_btn"),
            ui.Button(label="Claim",        style=discord.ButtonStyle.success, custom_id="ticket_claim_btn"),
        )
        await send_v2(ticket_ch, welcome, manage_ar)

        # Log
        log_c = make_container(
            color=C.info,
            title="Ticket Opened",
            fields=[
                ("User",    f"<@{user.id}> ({user.name})", True),
                ("Channel", f"<#{ticket_ch.id}>",          True),
                ("Question", self.question.value,           False),
            ],
            footer=f"User ID: {user.id}",
        )
        await send_log(LOG_CHANNEL_ID, log_c)

        confirm = make_container(
            color=C.success,
            description=f"Your ticket has been opened: {ticket_ch.mention}",
        )
        await send_v2(interaction, confirm, ephemeral=True, followup=True)


# ── Ticket panel button ───────────────────────────────────────────────────────

class TicketPanelView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ui.ActionRow(
            ui.Button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_create")
        ))

    @ui.button(custom_id="ticket_create", label="Create Ticket", style=discord.ButtonStyle.primary)
    async def create_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TicketModal())


# ── Ticket manage buttons (close / claim) ────────────────────────────────────

class TicketManageView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Close Ticket", style=discord.ButtonStyle.danger,  custom_id="ticket_close_btn")
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        await do_close_ticket(interaction, via_button=True)

    @ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim_btn")
    async def claim_btn(self, interaction: discord.Interaction, button: ui.Button):
        with get_db() as con:
            ticket = con.execute(
                "SELECT * FROM tickets WHERE guild_id=? AND channel_id=? AND status='open'",
                (str(interaction.guild_id), str(interaction.channel_id))
            ).fetchone()

        if not ticket:
            c = make_container(color=C.error, description="No open ticket in this channel.")
            return await send_v2(interaction, c, ephemeral=True)

        if ticket["claimed_by"]:
            who = ticket["claimed_by"]
            msg = "You already claimed this ticket." if who == str(interaction.user.id) \
                  else f"Already claimed by <@{who}>."
            c = make_container(color=C.warning, description=msg)
            return await send_v2(interaction, c, ephemeral=True)

        with get_db() as con:
            con.execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?",
                        (str(interaction.user.id), str(interaction.channel_id)))

        c = make_container(
            color=C.success,
            description=f"<@{interaction.user.id}> has claimed this ticket.",
        )
        await send_v2(interaction, c)


# ── Ticket closed buttons (reopen / delete) ───────────────────────────────────

class TicketClosedView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Reopen",        style=discord.ButtonStyle.success, custom_id="ticket_reopen_btn")
    async def reopen_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction.user):
            c = make_container(color=C.error, description="Only staff can reopen tickets.")
            return await send_v2(interaction, c, ephemeral=True)

        with get_db() as con:
            ticket = con.execute(
                "SELECT * FROM tickets WHERE guild_id=? AND channel_id=? AND status='closed'",
                (str(interaction.guild_id), str(interaction.channel_id))
            ).fetchone()

        if not ticket:
            c = make_container(color=C.error, description="This ticket is not closed.")
            return await send_v2(interaction, c, ephemeral=True)

        member = interaction.guild.get_member(int(ticket["user_id"]))
        if member:
            await interaction.channel.set_permissions(
                member, view_channel=True, send_messages=True, read_message_history=True
            )

        with get_db() as con:
            con.execute("UPDATE tickets SET status='open' WHERE channel_id=?",
                        (str(interaction.channel_id),))

        await interaction.response.defer()
        c = make_container(
            color=C.success,
            title="Ticket Reopened",
            fields=[
                ("Reopened by", f"<@{interaction.user.id}>", True),
                ("User",        f"<@{ticket['user_id']}>",   True),
            ],
        )
        manage_ar = ui.ActionRow(
            ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger,  custom_id="ticket_close_btn"),
            ui.Button(label="Claim",        style=discord.ButtonStyle.success, custom_id="ticket_claim_btn"),
        )
        await send_v2(interaction.channel, c, manage_ar)

    @ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger,  custom_id="ticket_delete_btn")
    async def delete_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction.user):
            c = make_container(color=C.error, description="Only staff can delete tickets.")
            return await send_v2(interaction, c, ephemeral=True)

        with get_db() as con:
            ticket = con.execute(
                "SELECT * FROM tickets WHERE channel_id=?",
                (str(interaction.channel_id),)
            ).fetchone()
            con.execute("DELETE FROM tickets WHERE channel_id=?", (str(interaction.channel_id),))

        if ticket:
            log_c = make_container(
                color=C.error,
                title="Ticket Deleted",
                fields=[
                    ("User",       f"<@{ticket['user_id']}>",    True),
                    ("Channel",    interaction.channel.name,       True),
                    ("Deleted by", f"<@{interaction.user.id}>",  True),
                ],
            )
            await send_log(LOG_CHANNEL_ID, log_c)

        c = make_container(color=C.warning, description="Deleting ticket in 5 seconds…")
        await send_v2(interaction, c)
        await asyncio.sleep(5)
        await interaction.channel.delete()


# ── Close logic ───────────────────────────────────────────────────────────────

async def do_close_ticket(interaction: discord.Interaction, via_button: bool = False):
    with get_db() as con:
        ticket = con.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=? AND status='open'",
            (str(interaction.guild_id), str(interaction.channel_id))
        ).fetchone()

    if not ticket:
        c = make_container(color=C.error, description="This is not an open ticket channel.")
        return await send_v2(interaction, c, ephemeral=True)

    if not is_staff(interaction.user) and str(interaction.user.id) != ticket["user_id"]:
        c = make_container(color=C.error, description="You don't have permission to close this ticket.")
        return await send_v2(interaction, c, ephemeral=True)

    if via_button:
        await interaction.response.defer()

    member = interaction.guild.get_member(int(ticket["user_id"]))
    if member:
        await interaction.channel.set_permissions(member, send_messages=False)

    with get_db() as con:
        con.execute(
            "UPDATE tickets SET status='closed', closed_by=?, closed_at=datetime('now') WHERE channel_id=?",
            (str(interaction.user.id), str(interaction.channel_id))
        )

    close_c = make_container(
        color=C.error,
        title="Ticket Closed",
        description="This ticket has been closed. The user can no longer send messages.",
        fields=[
            ("Closed by", f"<@{interaction.user.id}>", True),
            ("Closed at", _ts(),                        True),
        ],
    )
    closed_ar = ui.ActionRow(
        ui.Button(label="Reopen",        style=discord.ButtonStyle.success, custom_id="ticket_reopen_btn"),
        ui.Button(label="Delete Ticket", style=discord.ButtonStyle.danger,  custom_id="ticket_delete_btn"),
    )
    await send_v2(interaction.channel, close_c, closed_ar)

    log_c = make_container(
        color=C.error,
        title="Ticket Closed",
        fields=[
            ("User",      f"<@{ticket['user_id']}>",    True),
            ("Closed by", f"<@{interaction.user.id}>",  True),
            ("Channel",   interaction.channel.name,      True),
        ],
        footer=f"User ID: {ticket['user_id']}",
    )
    await send_log(LOG_CHANNEL_ID, log_c)


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

class ReviewModal(ui.Modal, title="Submit A Review"):
    review_title = ui.TextInput(
        label="Review Title",
        style=discord.TextStyle.short,
        placeholder="e.g. Great experience!",
        max_length=80,
        required=True,
    )
    review_body = ui.TextInput(
        label="Your Review",
        style=discord.TextStyle.paragraph,
        placeholder="Tell us about your experience...",
        max_length=1000,
        required=True,
    )
    review_rating = ui.TextInput(
        label="Rating (1–5)",
        style=discord.TextStyle.short,
        placeholder="Enter a number from 1 to 5",
        max_length=1,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            rating = int(self.review_rating.value.strip())
            if not 1 <= rating <= 5:
                raise ValueError
        except ValueError:
            c = make_container(color=C.error, description="Rating must be a number between 1 and 5.")
            return await send_v2(interaction, c, ephemeral=True, followup=True)

        rid = review_id()
        now = datetime.now(timezone.utc).isoformat()

        with get_db() as con:
            con.execute(
                "INSERT INTO reviews (id, guild_id, reviewer_id, reviewer, rating, title, body, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (rid, str(interaction.guild_id), str(interaction.user.id),
                 interaction.user.name, rating, self.review_title.value, self.review_body.value, now)
            )

        log_c = make_container(
            color=C.review,
            author=f"Review from {interaction.user.name}",
            title=self.review_title.value,
            description=self.review_body.value,
            fields=[("Rating", stars(rating), False)],
            footer=f"{interaction.guild.name} • Review ID: {rid}",
            show_footer=False,
        )
        await send_log(REVIEW_LOG_CHANNEL_ID, log_c)

        confirm = make_container(
            color=C.success,
            title="Review Submitted",
            description=f"Thanks for your feedback! (ID: `{rid}`)",
        )
        await send_v2(interaction, confirm, ephemeral=True, followup=True)


class ReviewPanelView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Submit A Review", style=discord.ButtonStyle.primary, custom_id="review_submit_btn")
    async def submit_btn(self, interaction: discord.Interaction, button: ui.Button):
        with get_db() as con:
            existing = con.execute(
                "SELECT id FROM reviews WHERE guild_id=? AND reviewer_id=?",
                (str(interaction.guild_id), str(interaction.user.id))
            ).fetchone()

        if existing:
            c = make_container(
                color=C.warning,
                description=f"You already submitted a review (ID: `{existing['id']}`). Contact an admin to update it.",
            )
            return await send_v2(interaction, c, ephemeral=True)

        await interaction.response.send_modal(ReviewModal())


# ── Ready: register persistent views ─────────────────────────────────────────

@bot.event
async def on_ready():
    bot.add_view(TicketPanelView())
    bot.add_view(TicketManageView())
    bot.add_view(TicketClosedView())
    bot.add_view(ReviewPanelView())
    print(f"[ready] {bot.user} ({bot.user.id})")


# ═══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS — TICKETS
# ═══════════════════════════════════════════════════════════════════════════════

ticket_group = app_commands.Group(name="ticket", description="Ticket system")


@ticket_group.command(name="panel", description="Post a ticket panel in a channel.")
@app_commands.describe(
    channel="Where to post the panel",
    title="Panel title",
    description="Panel description",
    category="Category for new ticket channels",
)
async def ticket_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    title: str = "Support Tickets",
    description: str = "Click the button below to open a ticket. Our staff team will assist you shortly.",
    category: discord.CategoryChannel | None = None,
):
    if not is_admin(interaction.user):
        c = make_container(color=C.error, description="Admin only.")
        return await send_v2(interaction, c, ephemeral=True)

    target = channel or interaction.channel
    if category:
        cfg_set(str(interaction.guild_id), "ticket_category", str(category.id))

    panel_c = make_container(
        color=C.info,
        title=title,
        description=description,
        fields=[
            ("How it works",
             "1. Click **Create Ticket** below\n"
             "2. Answer the question in the form\n"
             "3. A private channel will be created\n"
             "4. Staff will review and respond",
             False),
        ],
        footer=interaction.guild.name,
        show_footer=False,
    )
    create_ar = ui.ActionRow(
        ui.Button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_create")
    )
    await send_v2(target, panel_c, create_ar)

    c = make_container(color=C.success, description=f"Ticket panel posted in {target.mention}.")
    await send_v2(interaction, c, ephemeral=True)


@ticket_group.command(name="close", description="Close the ticket in this channel.")
async def ticket_close_cmd(interaction: discord.Interaction):
    await do_close_ticket(interaction, via_button=False)


@ticket_group.command(name="delete", description="Delete this ticket channel.")
async def ticket_delete_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        c = make_container(color=C.error, description="Staff only.")
        return await send_v2(interaction, c, ephemeral=True)

    with get_db() as con:
        ticket = con.execute(
            "SELECT * FROM tickets WHERE channel_id=?", (str(interaction.channel_id),)
        ).fetchone()

    if not ticket:
        c = make_container(color=C.error, description="This is not a ticket channel.")
        return await send_v2(interaction, c, ephemeral=True)

    log_c = make_container(
        color=C.error,
        title="Ticket Deleted",
        fields=[
            ("User",       f"<@{ticket['user_id']}>",    True),
            ("Channel",    interaction.channel.name,       True),
            ("Deleted by", f"<@{interaction.user.id}>",  True),
        ],
    )
    await send_log(LOG_CHANNEL_ID, log_c)

    with get_db() as con:
        con.execute("DELETE FROM tickets WHERE channel_id=?", (str(interaction.channel_id),))

    c = make_container(color=C.warning, description="Deleting ticket in 5 seconds…")
    await send_v2(interaction, c)
    await asyncio.sleep(5)
    await interaction.channel.delete()


@ticket_group.command(name="add_user", description="Add a user to this ticket.")
@app_commands.describe(user="User to add")
async def ticket_add_user(interaction: discord.Interaction, user: discord.Member):
    if not is_staff(interaction.user):
        c = make_container(color=C.error, description="Staff only.")
        return await send_v2(interaction, c, ephemeral=True)

    with get_db() as con:
        ticket = con.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=?",
            (str(interaction.guild_id), str(interaction.channel_id))
        ).fetchone()

    if not ticket:
        c = make_container(color=C.error, description="This is not a ticket channel.")
        return await send_v2(interaction, c, ephemeral=True)
    if ticket["status"] != "open":
        c = make_container(color=C.warning, description="Ticket is closed. Reopen it first.")
        return await send_v2(interaction, c, ephemeral=True)

    await interaction.channel.set_permissions(
        user, view_channel=True, send_messages=True, read_message_history=True
    )
    c = make_container(
        color=C.success,
        title="User Added",
        fields=[
            ("Added",    f"<@{user.id}>",             True),
            ("Added by", f"<@{interaction.user.id}>", True),
        ],
    )
    await send_v2(interaction, c)


@ticket_group.command(name="remove_user", description="Remove a user from this ticket.")
@app_commands.describe(user="User to remove")
async def ticket_remove_user(interaction: discord.Interaction, user: discord.Member):
    if not is_staff(interaction.user):
        c = make_container(color=C.error, description="Staff only.")
        return await send_v2(interaction, c, ephemeral=True)

    with get_db() as con:
        ticket = con.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=?",
            (str(interaction.guild_id), str(interaction.channel_id))
        ).fetchone()

    if not ticket:
        c = make_container(color=C.error, description="This is not a ticket channel.")
        return await send_v2(interaction, c, ephemeral=True)
    if ticket["status"] != "open":
        c = make_container(color=C.warning, description="Ticket is closed. Reopen it first.")
        return await send_v2(interaction, c, ephemeral=True)
    if str(user.id) == ticket["user_id"]:
        c = make_container(color=C.error, description="Can't remove the ticket owner.")
        return await send_v2(interaction, c, ephemeral=True)

    await interaction.channel.set_permissions(user, view_channel=False, send_messages=False)
    c = make_container(
        color=C.error,
        title="User Removed",
        fields=[
            ("Removed",    f"<@{user.id}>",             True),
            ("Removed by", f"<@{interaction.user.id}>", True),
        ],
    )
    await send_v2(interaction, c)


@ticket_group.command(name="transcript", description="Export a text transcript of this ticket.")
async def ticket_transcript(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        c = make_container(color=C.error, description="Staff only.")
        return await send_v2(interaction, c, ephemeral=True)

    with get_db() as con:
        ticket = con.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=?",
            (str(interaction.guild_id), str(interaction.channel_id))
        ).fetchone()

    if not ticket:
        c = make_container(color=C.error, description="This is not a ticket channel.")
        return await send_v2(interaction, c, ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    messages = []
    last_id = None
    for _ in range(5):
        kw = {"limit": 100}
        if last_id:
            kw["before"] = discord.Object(id=last_id)
        batch = [m async for m in interaction.channel.history(**kw)]
        if not batch:
            break
        messages.extend(batch)
        last_id = batch[-1].id
        if len(batch) < 100:
            break
    messages.reverse()

    lines = [
        "Ticket Transcript",
        f"Server:   {interaction.guild.name}",
        f"Channel:  #{interaction.channel.name}",
        f"Opened by:{ticket['username']} ({ticket['user_id']})",
        f"Status:   {ticket['status']}",
        f"Exported: {interaction.user.name} at {datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S UTC')}",
        f"Messages: {len(messages)}",
        "=" * 60,
        "",
    ]
    for msg in messages:
        t = msg.created_at.strftime("%a, %d %b %Y %H:%M:%S UTC")
        body = msg.content or ("[embed]" if msg.embeds else ("[attachment]" if msg.attachments else "[empty]"))
        lines.append(f"[{t}] {msg.author.name}: {body}")
        for att in msg.attachments:
            lines.append(f"  [Attachment: {att.url}]")

    buf = io.BytesIO("\n".join(lines).encode())
    fname = f"transcript_{interaction.channel.name}_{int(time.time())}.txt"

    c = make_container(
        color=C.info,
        title="Ticket Transcript",
        fields=[
            ("Channel",     interaction.channel.name,       True),
            ("Messages",    str(len(messages)),              True),
            ("Exported by", f"<@{interaction.user.id}>",    True),
        ],
    )
    view = v2_view(c)
    flags = discord.MessageFlags(components_v2=True, ephemeral=True)
    await interaction.followup.send(view=view, flags=flags, file=discord.File(buf, filename=fname), ephemeral=True)


@ticket_group.command(name="stats", description="View ticket statistics.")
@app_commands.describe(days="Stats window in days (default: 7)")
async def ticket_stats(interaction: discord.Interaction, days: int = 7):
    if not is_staff(interaction.user):
        c = make_container(color=C.error, description="Staff only.")
        return await send_v2(interaction, c, ephemeral=True)

    gid = str(interaction.guild_id)
    with get_db() as con:
        total  = con.execute("SELECT COUNT(*) FROM tickets WHERE guild_id=?", (gid,)).fetchone()[0]
        open_  = con.execute("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (gid,)).fetchone()[0]
        opened = con.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND created_at >= datetime('now', ?)",
            (gid, f"-{days} days")
        ).fetchone()[0]
        closed = con.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='closed' AND created_at >= datetime('now', ?)",
            (gid, f"-{days} days")
        ).fetchone()[0]
        closers = con.execute(
            "SELECT closed_by, COUNT(*) as c FROM tickets "
            "WHERE guild_id=? AND closed_by IS NOT NULL AND created_at >= datetime('now', ?) "
            "GROUP BY closed_by ORDER BY c DESC LIMIT 5",
            (gid, f"-{days} days")
        ).fetchall()

    fields = [
        ("Total (all time)",  str(total),  True),
        ("Currently open",    str(open_),  True),
        (f"Opened ({days}d)", str(opened), True),
        (f"Closed ({days}d)", str(closed), True),
    ]
    if closers:
        fields.append(("Top closers",
                        "\n".join(f"<@{r['closed_by']}> — {r['c']} closed" for r in closers),
                        False))

    c = make_container(
        color=C.info,
        title=f"Ticket Stats — Last {days} day{'s' if days != 1 else ''}",
        fields=fields,
    )
    await send_v2(interaction, c, ephemeral=True)


bot.tree.add_command(ticket_group)

# ═══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS — REVIEWS
# ═══════════════════════════════════════════════════════════════════════════════

review_group = app_commands.Group(name="review", description="Review system")


@review_group.command(name="panel", description="Post a review panel in a channel.")
@app_commands.describe(channel="Where to post", title="Panel title", description="Panel description")
async def review_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    title: str = "Reviews",
    description: str = "Share your experience! Click the button below to leave a review.",
):
    if not is_admin(interaction.user):
        c = make_container(color=C.error, description="Admin only.")
        return await send_v2(interaction, c, ephemeral=True)

    target = channel or interaction.channel
    gid = str(interaction.guild_id)

    with get_db() as con:
        row = con.execute(
            "SELECT COUNT(*) as count, AVG(rating) as avg FROM reviews WHERE guild_id=?", (gid,)
        ).fetchone()

    fields = []
    if row and row["count"]:
        fields = [
            ("Total Reviews",  str(row["count"]),                             True),
            ("Average Rating", f"{stars(round(row['avg']))} ({row['avg']:.1f})", True),
        ]

    panel_c = make_container(
        color=C.review,
        title=title,
        description=description,
        fields=fields or None,
        footer=interaction.guild.name,
        show_footer=False,
    )
    submit_ar = ui.ActionRow(
        ui.Button(label="Submit A Review", style=discord.ButtonStyle.primary, custom_id="review_submit_btn")
    )
    await send_v2(target, panel_c, submit_ar)

    c = make_container(color=C.success, description=f"Review panel posted in {target.mention}.")
    await send_v2(interaction, c, ephemeral=True)


@review_group.command(name="list", description="List all reviews for this server.")
@app_commands.describe(page="Page number")
async def review_list(interaction: discord.Interaction, page: int = 1):
    gid = str(interaction.guild_id)
    per = 5
    offset = (page - 1) * per

    with get_db() as con:
        total   = con.execute("SELECT COUNT(*) FROM reviews WHERE guild_id=?", (gid,)).fetchone()[0]
        reviews = con.execute(
            "SELECT * FROM reviews WHERE guild_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (gid, per, offset)
        ).fetchall()

    if not reviews:
        c = make_container(color=C.warning, description="No reviews found.")
        return await send_v2(interaction, c, ephemeral=True)

    pages = max(1, -(-total // per))

    # Build one container per review, separated
    items: list[ui.Item] = []

    header = ui.TextDisplay(f"### 📋 Reviews — Page {page}/{pages}\n-# Showing {len(reviews)} of {total}")
    items.append(ui.Container(header, accent_color=C.info))

    for r in reviews:
        parts = [
            ui.TextDisplay(
                f"-# Review from {r['reviewer']}\n"
                f"### {r['title']}\n"
                f"{r['body']}\n\n"
                f"**Rating**\n{stars(r['rating'])}"
            ),
            _sep(divider=True),
            ui.TextDisplay(f"-# {interaction.guild.name} • Review ID: {r['id']}"),
        ]
        items.append(ui.Container(*parts, accent_color=C.review))

    await send_v2(interaction, *items)


@review_group.command(name="delete", description="Delete a review by ID (admin only).")
@app_commands.describe(id="Review ID")
async def review_delete(interaction: discord.Interaction, id: str):
    if not is_admin(interaction.user):
        c = make_container(color=C.error, description="Admin only.")
        return await send_v2(interaction, c, ephemeral=True)

    rid = id.upper()
    with get_db() as con:
        review = con.execute(
            "SELECT * FROM reviews WHERE id=? AND guild_id=?", (rid, str(interaction.guild_id))
        ).fetchone()

        if not review:
            c = make_container(color=C.error, description=f"No review found with ID `{rid}`.")
            return await send_v2(interaction, c, ephemeral=True)

        con.execute("DELETE FROM reviews WHERE id=?", (rid,))

    c = make_container(
        color=C.success,
        description=f"Review `{rid}` by **{review['reviewer']}** has been deleted.",
    )
    await send_v2(interaction, c, ephemeral=True)


@review_group.command(name="stats", description="Show review statistics.")
async def review_stats(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    with get_db() as con:
        s = con.execute("""
            SELECT COUNT(*) as total, AVG(rating) as avg,
                SUM(CASE WHEN rating=5 THEN 1 ELSE 0 END) as five,
                SUM(CASE WHEN rating=4 THEN 1 ELSE 0 END) as four,
                SUM(CASE WHEN rating=3 THEN 1 ELSE 0 END) as three,
                SUM(CASE WHEN rating=2 THEN 1 ELSE 0 END) as two,
                SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) as one
            FROM reviews WHERE guild_id=?
        """, (gid,)).fetchone()

    if not s or not s["total"]:
        c = make_container(color=C.warning, description="No reviews yet.")
        return await send_v2(interaction, c, ephemeral=True)

    t = s["total"]
    breakdown = "\n".join([
        f"⭐⭐⭐⭐⭐  {bar(s['five'],  t)}  {s['five']}",
        f"⭐⭐⭐⭐☆  {bar(s['four'],  t)}  {s['four']}",
        f"⭐⭐⭐☆☆  {bar(s['three'], t)}  {s['three']}",
        f"⭐⭐☆☆☆  {bar(s['two'],   t)}  {s['two']}",
        f"⭐☆☆☆☆  {bar(s['one'],   t)}  {s['one']}",
    ])
    c = make_container(
        color=C.review,
        title="⭐ Review Statistics",
        fields=[
            ("Total Reviews",  str(t),                  True),
            ("Average Rating", f"{s['avg']:.2f} / 5.00", True),
            ("Overall",        stars(round(s["avg"])),   True),
            ("Breakdown",      breakdown,                False),
        ],
    )
    await send_v2(interaction, c)


bot.tree.add_command(review_group)

# ═══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS — PROJECT / DONATE / REPLY / STATUS (Render API)
# ═══════════════════════════════════════════════════════════════════════════════

def api_headers() -> dict:
    return {"X-Secret": API_SECRET, "Content-Type": "application/json"}

async def api(method: str, path: str, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.request(method, f"{API_URL}{path}", headers=api_headers(), **kwargs) as r:
            return r.status, await r.json()

# ── /project ──────────────────────────────────────────────────────────────────

project_group = app_commands.Group(name="project", description="Manage portfolio projects")

@project_group.command(name="list", description="List all projects")
async def project_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    status, data = await api("GET", "/projects")
    if status != 200:
        c = make_container(color=C.error, description="Failed to fetch projects.")
        return await send_v2(interaction, c, ephemeral=True, followup=True)
    if not data:
        c = make_container(color=C.warning, description="No projects yet.")
        return await send_v2(interaction, c, ephemeral=True, followup=True)

    lines = "\n".join(f"**{p['title']}**  `{p['id']}`" for p in data)
    c = make_container(color=C.info, title="Projects", description=lines)
    await send_v2(interaction, c, ephemeral=True, followup=True)


@project_group.command(name="add", description="Add a project")
@app_commands.describe(title="Title", description="Description", tags="Comma-separated tags", span="Grid span 1–4")
async def project_add(interaction: discord.Interaction, title: str, description: str, tags: str = "", span: int = 2):
    await interaction.response.defer(ephemeral=True)
    payload = {"title": title, "description": description,
               "tags": [t.strip() for t in tags.split(",") if t.strip()], "span": span}
    status, data = await api("POST", "/projects", json=payload)
    if status in (200, 201):
        c = make_container(color=C.success, description=f"Project added! ID: `{data.get('id', '?')}`")
    else:
        c = make_container(color=C.error, description=f"Failed: {data}")
    await send_v2(interaction, c, ephemeral=True, followup=True)


@project_group.command(name="edit", description="Edit a project field")
@app_commands.describe(project_id="Project ID", field="Field to edit", value="New value")
async def project_edit(interaction: discord.Interaction, project_id: str, field: str, value: str):
    await interaction.response.defer(ephemeral=True)
    status, data = await api("PATCH", f"/projects/{project_id}", json={field: value})
    c = make_container(color=C.success if status == 200 else C.error,
                       description="Project updated." if status == 200 else f"Failed: {data}")
    await send_v2(interaction, c, ephemeral=True, followup=True)


@project_group.command(name="remove", description="Remove a project")
@app_commands.describe(project_id="Project ID")
async def project_remove(interaction: discord.Interaction, project_id: str):
    await interaction.response.defer(ephemeral=True)
    status, data = await api("DELETE", f"/projects/{project_id}")
    c = make_container(color=C.success if status == 200 else C.error,
                       description="Project removed." if status == 200 else f"Failed: {data}")
    await send_v2(interaction, c, ephemeral=True, followup=True)

bot.tree.add_command(project_group)

# ── /donate ───────────────────────────────────────────────────────────────────

donate_group = app_commands.Group(name="donate", description="Manage donation links")

@donate_group.command(name="list", description="Show current donation links")
async def donate_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    status, data = await api("GET", "/donate")
    if status != 200:
        c = make_container(color=C.error, description="Failed to fetch donation links.")
        return await send_v2(interaction, c, ephemeral=True, followup=True)
    if not data:
        c = make_container(color=C.warning, description="No donation links yet.")
        return await send_v2(interaction, c, ephemeral=True, followup=True)
    lines = "\n".join(f"**{d['name']}** — {d['url']}" for d in data)
    c = make_container(color=C.info, title="Donation Links", description=lines)
    await send_v2(interaction, c, ephemeral=True, followup=True)


@donate_group.command(name="set", description="Add or update a donation link")
@app_commands.describe(name="Platform name", url="Donation URL", label="Button label")
async def donate_set(interaction: discord.Interaction, name: str, url: str, label: str = ""):
    await interaction.response.defer(ephemeral=True)
    status, data = await api("POST", "/donate", json={"name": name, "url": url, "label": label})
    c = make_container(color=C.success if status in (200, 201) else C.error,
                       description=f"Donation link set for **{name}**." if status in (200, 201) else f"Failed: {data}")
    await send_v2(interaction, c, ephemeral=True, followup=True)


@donate_group.command(name="remove", description="Remove a donation link")
@app_commands.describe(name="Platform name")
async def donate_remove(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    status, data = await api("DELETE", f"/donate/{name}")
    c = make_container(color=C.success if status == 200 else C.error,
                       description=f"Removed **{name}**." if status == 200 else f"Failed: {data}")
    await send_v2(interaction, c, ephemeral=True, followup=True)

bot.tree.add_command(donate_group)

# ── /reply ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="reply", description="Email a reply to a contact form submission")
@app_commands.describe(to="Recipient email", subject="Subject", message="Email body")
async def reply_cmd(interaction: discord.Interaction, to: str, subject: str, message: str):
    await interaction.response.defer(ephemeral=True)
    try:
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"]    = FROM_EMAIL
        msg["To"]      = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)
        c = make_container(color=C.success, description=f"Email sent to **{to}**.")
    except Exception as e:
        c = make_container(color=C.error, description=f"Failed to send email: {e}")
    await send_v2(interaction, c, ephemeral=True, followup=True)

# ── /status ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="status", description="API health check + counts")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        s_p, projects = await api("GET", "/projects")
        s_d, donate   = await api("GET", "/donate")
        api_ok = s_p == 200 and s_d == 200
    except Exception:
        api_ok, projects, donate = False, [], []

    gid = str(interaction.guild_id)
    with get_db() as con:
        open_tickets  = con.execute("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (gid,)).fetchone()[0]
        total_reviews = con.execute("SELECT COUNT(*) FROM reviews WHERE guild_id=?", (gid,)).fetchone()[0]

    c = make_container(
        color=C.success if api_ok else C.error,
        title="Bot Status",
        fields=[
            ("API",           "🟢 Online" if api_ok else "🔴 Offline",           True),
            ("Projects",      str(len(projects)) if api_ok else "—",              True),
            ("Donate links",  str(len(donate))   if api_ok else "—",              True),
            ("Open tickets",  str(open_tickets),                                   True),
            ("Total reviews", str(total_reviews),                                  True),
        ],
    )
    await send_v2(interaction, c, ephemeral=True, followup=True)

# ── Run ───────────────────────────────────────────────────────────────────────

bot.run(TOKEN)
