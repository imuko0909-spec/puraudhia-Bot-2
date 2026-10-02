from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# 🌙 Luna - VC募集Bot
# 𝐋𝐮𝐦𝐢𝐞𝐫𝐞 専用
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "luna_recruit.db")

BOT_NAME = "ルナ"
SERVER_NAME = "𝐋𝐮𝐦𝐢𝐞𝐫𝐞"

# 同じ人が連続で募集を出すまでの秒数
RECRUIT_COOLDOWN = 60


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("luna-recruit")


# =========================================================
# 🕰️ 時刻
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


# =========================================================
# 🗃️ DATABASE
# =========================================================

class Database:

    def __init__(self, path: str):

        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS guild_settings(
                guild_id INTEGER PRIMARY KEY,
                recruit_channel_id INTEGER,
                notify_role_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS recruitments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,

                recruit_type TEXT,
                people TEXT,
                first_time TEXT,
                listener TEXT,
                comment TEXT,
                voice_channel_id INTEGER,

                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cooldowns(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_recruit_at TEXT NOT NULL,

                PRIMARY KEY(guild_id, user_id)
            );
            """
        )

        self.conn.commit()

    # -----------------------------------------------------

    def ensure_guild(self, guild_id: int):

        self.conn.execute(
            """
            INSERT OR IGNORE INTO guild_settings(guild_id)
            VALUES(?)
            """,
            (guild_id,),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def settings(self, guild_id: int):

        self.ensure_guild(guild_id)

        return self.conn.execute(
            """
            SELECT *
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    # -----------------------------------------------------

    def set_channel(
        self,
        guild_id: int,
        channel_id: int,
    ):

        self.ensure_guild(guild_id)

        self.conn.execute(
            """
            UPDATE guild_settings
            SET recruit_channel_id = ?
            WHERE guild_id = ?
            """,
            (
                channel_id,
                guild_id,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def set_notify_role(
        self,
        guild_id: int,
        role_id: Optional[int],
    ):

        self.ensure_guild(guild_id)

        self.conn.execute(
            """
            UPDATE guild_settings
            SET notify_role_id = ?
            WHERE guild_id = ?
            """,
            (
                role_id,
                guild_id,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def add_recruitment(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        message_id: int,
        recruit_type: str,
        people: str,
        first_time: str,
        listener: str,
        comment: str,
        voice_channel_id: Optional[int],
    ):

        self.conn.execute(
            """
            INSERT INTO recruitments(
                guild_id,
                user_id,
                channel_id,
                message_id,
                recruit_type,
                people,
                first_time,
                listener,
                comment,
                voice_channel_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                channel_id,
                message_id,
                recruit_type,
                people,
                first_time,
                listener,
                comment,
                voice_channel_id,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def recent_recruitments(
        self,
        guild_id: int,
        limit: int = 10,
    ):

        return self.conn.execute(
            """
            SELECT *
            FROM recruitments
            WHERE guild_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                guild_id,
                limit,
            ),
        ).fetchall()

    # -----------------------------------------------------

    def set_cooldown(
        self,
        guild_id: int,
        user_id: int,
    ):

        self.conn.execute(
            """
            INSERT OR REPLACE INTO cooldowns(
                guild_id,
                user_id,
                last_recruit_at
            )
            VALUES (?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def cooldown_remaining(
        self,
        guild_id: int,
        user_id: int,
    ) -> int:

        row = self.conn.execute(
            """
            SELECT last_recruit_at
            FROM cooldowns
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

        if not row:
            return 0

        try:

            last = datetime.fromisoformat(
                row["last_recruit_at"]
            )

        except Exception:
            return 0

        diff = (
            utcnow() - last
        ).total_seconds()

        remaining = (
            RECRUIT_COOLDOWN
            - int(diff)
        )

        return max(
            remaining,
            0,
        )


db = Database(DB_PATH)


# =========================================================
# 🎨 共通Embed
# =========================================================

def luna_embed(
    title: str,
    description: str,
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:

    embed = discord.Embed(
        title=f"🌙 {title}",
        description=description,
        color=discord.Color.from_rgb(
            175,
            155,
            235,
        ),
    )

    embed.set_author(
        name=f"{BOT_NAME}｜{SERVER_NAME} VC募集案内"
    )

    if guild and guild.icon:

        embed.set_thumbnail(
            url=guild.icon.url
        )

    embed.set_footer(
        text=(
            "話したい時に、話したい人と。"
            " ルナがきっかけを作ります 🌙"
        )
    )

    return embed


# =========================================================
# 🤖 BOT
# =========================================================

class LunaBot(commands.Bot):

    def __init__(self):

        intents = discord.Intents.default()

        intents.guilds = True
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self):

        self.add_view(
            RecruitPanelView()
        )

        try:

            synced = await self.tree.sync()

            log.info(
                "Synced %s commands.",
                len(synced),
            )

        except Exception:

            log.exception(
                "Slash command sync failed"
            )


bot = LunaBot()


# =========================================================
# 🌙 募集パネル
# =========================================================

class RecruitPanelView(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    # -----------------------------------------------------
    # 募集
    # -----------------------------------------------------

    @discord.ui.button(
        label="VC募集する",
        emoji="🎙️",
        style=discord.ButtonStyle.primary,
        custom_id="luna:create_recruitment",
    )
    async def create_recruitment(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        remaining = db.cooldown_remaining(
            interaction.guild.id,
            interaction.user.id,
        )

        if remaining > 0:

            await interaction.response.send_message(
                (
                    "少しだけ待ってね 🌙\n"
                    f"あと **{remaining}秒** で"
                    "もう一度募集できます。"
                ),
                ephemeral=True,
            )

            return

        await interaction.response.send_modal(
            RecruitModal()
        )

    # -----------------------------------------------------
    # 最近の募集
    # -----------------------------------------------------

    @discord.ui.button(
        label="最近の募集",
        emoji="🕒",
        style=discord.ButtonStyle.secondary,
        custom_id="luna:recent_recruitments",
    )
    async def recent(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        rows = db.recent_recruitments(
            interaction.guild.id,
            10,
        )

        if not rows:

            await interaction.response.send_message(
                "まだ募集履歴はありません 🌙",
                ephemeral=True,
            )

            return

        lines = []

        for row in rows:

            user = (
                interaction.guild.get_member(
                    row["user_id"]
                )
            )

            name = (
                user.display_name
                if user
                else "退出済みメンバー"
            )

            try:

                dt = datetime.fromisoformat(
                    row["created_at"]
                )

                timestamp = int(
                    dt.timestamp()
                )

                time_text = (
                    f"<t:{timestamp}:R>"
                )

            except Exception:

                time_text = ""

            lines.append(
                (
                    f"🎙️ **{row['recruit_type']}**\n"
                    f"募集主：{name}\n"
                    f"人数：{row['people']}\n"
                    f"{time_text}"
                )
            )

        await interaction.response.send_message(
            embed=luna_embed(
                "最近のVC募集",
                "\n\n".join(lines),
                interaction.guild,
            ),
            ephemeral=True,
        )


# =========================================================
# 📝 募集入力
# =========================================================

class RecruitModal(
    discord.ui.Modal,
    title="🌙 ルナ｜VC募集",
):

    recruit_type = discord.ui.TextInput(
        label="どんな募集？",
        placeholder=(
            "例：まったり雑談 / ながら / ゲーム / 少人数"
        ),
        required=True,
        max_length=50,
    )

    people = discord.ui.TextInput(
        label="募集人数",
        placeholder=(
            "例：2〜3人 / 何人でも / あと1人"
        ),
        required=False,
        max_length=30,
    )

    first_time = discord.ui.TextInput(
        label="初対面",
        placeholder=(
            "例：歓迎 / 大歓迎 / どちらでも"
        ),
        required=False,
        max_length=30,
    )

    listener = discord.ui.TextInput(
        label="聞き専",
        placeholder=(
            "例：OK / NG / どちらでも"
        ),
        required=False,
        max_length=30,
    )

    comment = discord.ui.TextInput(
        label="ひとこと",
        placeholder=(
            "例：少し話そ〜！気軽にどうぞ"
        ),
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=300,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:
            return

        guild = interaction.guild

        settings = db.settings(
            guild.id
        )

        # ---------------------------------
        # 募集投稿先
        # ---------------------------------

        recruit_channel = None

        if settings[
            "recruit_channel_id"
        ]:

            recruit_channel = (
                guild.get_channel(
                    settings[
                        "recruit_channel_id"
                    ]
                )
            )

        if not isinstance(
            recruit_channel,
            discord.TextChannel,
        ):

            recruit_channel = (
                interaction.channel
                if isinstance(
                    interaction.channel,
                    discord.TextChannel,
                )
                else None
            )

        if not recruit_channel:

            await interaction.response.send_message(
                (
                    "募集チャンネルが"
                    "設定されていません。"
                ),
                ephemeral=True,
            )

            return

        # ---------------------------------
        # 現在VC
        # ---------------------------------

        voice_channel = None

        member = interaction.user

        if (
            isinstance(
                member,
                discord.Member,
            )
            and member.voice
            and member.voice.channel
        ):

            voice_channel = (
                member.voice.channel
            )

        # ---------------------------------
        # Embed
        # ---------------------------------

        embed = discord.Embed(
            title="🎙️ VC募集",
            color=discord.Color.from_rgb(
                175,
                155,
                235,
            ),
            timestamp=utcnow(),
        )

        embed.set_author(
            name=interaction.user.display_name,
            icon_url=(
                interaction.user.display_avatar.url
            ),
        )

        embed.set_thumbnail(
            url=(
                interaction.user.display_avatar.url
            )
        )

        embed.add_field(
            name="🌙 募集主",
            value=interaction.user.mention,
            inline=False,
        )

        embed.add_field(
            name="💬 タイプ",
            value=(
                self.recruit_type.value
                or "雑談"
            ),
            inline=True,
        )

        embed.add_field(
            name="👥 人数",
            value=(
                self.people.value
                or "何人でも"
            ),
            inline=True,
        )

        embed.add_field(
            name="🤝 初対面",
            value=(
                self.first_time.value
                or "歓迎"
            ),
            inline=True,
        )

        embed.add_field(
            name="🎧 聞き専",
            value=(
                self.listener.value
                or "OK"
            ),
            inline=True,
        )

        if voice_channel:

            embed.add_field(
                name="🔊 今いるVC",
                value=voice_channel.mention,
                inline=False,
            )

        if self.comment.value:

            embed.add_field(
                name="💭 コメント",
                value=self.comment.value,
                inline=False,
            )

        embed.set_footer(
            text=(
                f"{BOT_NAME}｜"
                "気軽に話そ〜 🌙"
            )
        )

        # ---------------------------------
        # 通知ロール
        # ---------------------------------

        content = None

        allowed_mentions = (
            discord.AllowedMentions.none()
        )

        role_id = settings[
            "notify_role_id"
        ]

        if role_id:

            role = guild.get_role(
                role_id
            )

            if role:

                content = (
                    f"{role.mention} "
                    "新しいVC募集があります 🌙"
                )

                allowed_mentions = (
                    discord.AllowedMentions(
                        roles=[role],
                        users=False,
                        everyone=False,
                    )
                )

        # ---------------------------------
        # 投稿
        # ---------------------------------

        try:

            message = (
                await recruit_channel.send(
                    content=content,
                    embed=embed,
                    allowed_mentions=(
                        allowed_mentions
                    ),
                )
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                (
                    "募集を投稿できませんでした💦\n"
                    "ルナに「メッセージを送信」"
                    "「埋め込みリンク」の権限を"
                    "付けてください。"
                ),
                ephemeral=True,
            )

            return

        # ---------------------------------
        # DB保存
        # ---------------------------------

        db.add_recruitment(
            guild.id,
            interaction.user.id,
            recruit_channel.id,
            message.id,
            self.recruit_type.value,
            self.people.value or "何人でも",
            self.first_time.value or "歓迎",
            self.listener.value or "OK",
            self.comment.value or "",
            (
                voice_channel.id
                if voice_channel
                else None
            ),
        )

        db.set_cooldown(
            guild.id,
            interaction.user.id,
        )

        # ---------------------------------
        # 完了
        # ---------------------------------

        await interaction.response.send_message(
            (
                "🌙 **募集を投稿しました！**\n\n"
                f"{message.jump_url}"
            ),
            ephemeral=True,
        )


# =========================================================
# ⚙️ SETUP
# =========================================================

@bot.tree.command(
    name="luna_setup",
    description="ルナの募集チャンネルを設定します",
)
@app_commands.describe(
    募集チャンネル="VC募集を投稿するチャンネル",
    通知ロール="募集時に通知するロール（任意）",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def luna_setup(
    interaction: discord.Interaction,
    募集チャンネル: discord.TextChannel,
    通知ロール: Optional[
        discord.Role
    ] = None,
):

    if not interaction.guild:
        return

    db.set_channel(
        interaction.guild.id,
        募集チャンネル.id,
    )

    db.set_notify_role(
        interaction.guild.id,
        (
            通知ロール.id
            if 通知ロール
            else None
        ),
    )

    text = (
        f"🎙️ 募集チャンネル\n"
        f"{募集チャンネル.mention}\n\n"
    )

    if 通知ロール:

        text += (
            f"🔔 通知ロール\n"
            f"{通知ロール.mention}"
        )

    else:

        text += (
            "🔔 通知ロール\n"
            "なし"
        )

    await interaction.response.send_message(
        embed=luna_embed(
            "設定完了",
            text,
            interaction.guild,
        ),
        ephemeral=True,
    )


# =========================================================
# 🌙 PANEL
# =========================================================

@bot.tree.command(
    name="luna_panel",
    description="ルナのVC募集パネルを設置します",
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def luna_panel(
    interaction: discord.Interaction,
):

    if not interaction.guild:
        return

    embed = luna_embed(
        "VC募集",
        (
            "誰かと話したい時は、"
            "ルナにおまかせ 🌙\n\n"

            "下の **「VC募集する」** を押して、"
            "募集内容を入力するだけ！\n\n"

            "文章を考えるのが苦手な人も、"
            "気軽に使ってね。"
        ),
        interaction.guild,
    )

    embed.add_field(
        name="募集できる内容",
        value=(
            "💬 雑談\n"
            "🌿 まったり\n"
            "🎮 ゲーム\n"
            "🎧 ながら通話\n"
            "🌟 初対面歓迎\n"
            "🌙 寝る前に少し\n"
            "👥 少人数\n"
            "✨ その他なんでも"
        ),
        inline=False,
    )

    await interaction.channel.send(
        embed=embed,
        view=RecruitPanelView(),
    )

    await interaction.response.send_message(
        "🌙 ルナの募集パネルを設置しました。",
        ephemeral=True,
    )


# =========================================================
# 📊 設定確認
# =========================================================

@bot.tree.command(
    name="luna_settings",
    description="ルナの現在の設定を確認します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def luna_settings(
    interaction: discord.Interaction,
):

    if not interaction.guild:
        return

    guild = interaction.guild

    settings = db.settings(
        guild.id
    )

    channel = (
        guild.get_channel(
            settings[
                "recruit_channel_id"
            ]
        )
        if settings[
            "recruit_channel_id"
        ]
        else None
    )

    role = (
        guild.get_role(
            settings[
                "notify_role_id"
            ]
        )
        if settings[
            "notify_role_id"
        ]
        else None
    )

    await interaction.response.send_message(
        embed=luna_embed(
            "現在の設定",
            (
                "🎙️ **募集チャンネル**\n"
                f"{channel.mention if channel else '未設定'}\n\n"

                "🔔 **通知ロール**\n"
                f"{role.mention if role else 'なし'}\n\n"

                "⏱️ **連続募集制限**\n"
                f"{RECRUIT_COOLDOWN}秒"
            ),
            guild,
        ),
        ephemeral=True,
    )


# =========================================================
# ❗ ERROR
# =========================================================

@bot.tree.error
async def tree_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):

    if isinstance(
        error,
        app_commands.MissingPermissions,
    ):

        message = (
            "このコマンドは管理者専用です。"
        )

    else:

        log.error(
            "Command error: %s",
            error,
        )

        message = (
            "処理中にエラーが発生しました💦"
        )

    if interaction.response.is_done():

        await interaction.followup.send(
            message,
            ephemeral=True,
        )

    else:

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


# =========================================================
# 🌙 READY
# =========================================================

@bot.event
async def on_ready():

    log.info(
        "Logged in as %s (%s)",
        bot.user,
        bot.user.id if bot.user else "?",
    )

    try:

        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="みんなのVC募集 🌙",
            )
        )

    except Exception:
        pass


# =========================================================
# 🚀 START
# =========================================================

if __name__ == "__main__":

    if not TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。"
        )

    bot.run(TOKEN)
