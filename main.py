from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

import discord


# ============================================================
# Puraudhia プロフィール表示専用Bot
#
# ・VC生成しない
# ・パネル設置しない
# ・シャベレアで自動移動された最終VCを監視
# ・指定カテゴリー内VCだけプロフィール表示
# ・VCのインチャへ直接プロフィールカード投稿
# ・「プロフィールを見る」で本人のプロフィール投稿へジャンプ
# ・表示名検索対応
# ============================================================


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()


# ============================================================
# Puraudhia
# ============================================================

GUILD_ID = 1458016711344263170


# ============================================================
# 監視カテゴリー
# ============================================================

PROFILE_VOICE_CATEGORY_ID = 1469270048970375240


# ============================================================
# プロフィールチャンネル
# ============================================================

MALE_PROFILE_CHANNEL_ID = 1513571967700172830
FEMALE_PROFILE_CHANNEL_ID = 1513572899896823979


# ============================================================
# 性別ロール
# ============================================================

MALE_ROLE_ID = 1458031487671861341
FEMALE_ROLE_ID = 1458022601258700800


# ============================================================
# 動作設定
# ============================================================

# シャベレアの自動移動が完了するまで待つ
MOVE_SETTLE_SECONDS = 5.0

# プロフィール検索で遡る件数
PROFILE_SCAN_LIMIT = 3000

# 同じVCで同じ人のプロフィールは1回だけ表示
POST_ONCE_PER_ROOM = True


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("puraudhia-profile")


# ============================================================
# Intents
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.voice_states = True
intents.message_content = True


# ============================================================
# Client
# ============================================================

client = discord.Client(
    intents=intents
)


# ============================================================
# キャッシュ
# ============================================================

# user_id -> profile jump URL
profile_cache: dict[int, str] = {}

# (voice_channel_id, user_id)
posted_in_room: set[tuple[int, int]] = set()

# user_id -> asyncio.Task
settle_tasks: dict[int, asyncio.Task] = {}


# ============================================================
# 共通
# ============================================================

async def get_channel_safe(
    channel_id: int,
):

    channel = client.get_channel(
        channel_id
    )

    if channel is not None:
        return channel

    try:

        return await client.fetch_channel(
            channel_id
        )

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):

        return None


def normalize_text(
    text: str,
) -> str:
    """
    比較用に文字列を正規化
    """

    return (
        text
        .strip()
        .lower()
        .replace(" ", "")
        .replace("　", "")
        .replace("\n", "")
    )


# ============================================================
# 性別ロール
# ============================================================

def member_gender(
    member: discord.Member,
) -> Optional[str]:

    role_ids = {
        role.id
        for role in member.roles
    }

    has_male = (
        MALE_ROLE_ID in role_ids
    )

    has_female = (
        FEMALE_ROLE_ID in role_ids
    )

    if has_male and not has_female:
        return "male"

    if has_female and not has_male:
        return "female"

    return None


def profile_channel_candidates(
    member: discord.Member,
) -> list[int]:

    gender = member_gender(
        member
    )

    if gender == "male":

        return [
            MALE_PROFILE_CHANNEL_ID
        ]

    if gender == "female":

        return [
            FEMALE_PROFILE_CHANNEL_ID
        ]

    # 性別ロール無しなら男女両方検索
    return [
        MALE_PROFILE_CHANNEL_ID,
        FEMALE_PROFILE_CHANNEL_ID,
    ]


# ============================================================
# Embedをテキスト化
# ============================================================

def embed_to_text(
    embed: discord.Embed,
) -> str:

    parts: list[str] = []

    if embed.title:
        parts.append(
            embed.title
        )

    if embed.description:
        parts.append(
            embed.description
        )

    if (
        embed.author
        and embed.author.name
    ):

        parts.append(
            embed.author.name
        )

    if (
        embed.footer
        and embed.footer.text
    ):

        parts.append(
            embed.footer.text
        )

    for field in embed.fields:

        if field.name:

            parts.append(
                field.name
            )

        if field.value:

            parts.append(
                field.value
            )

    return "\n".join(
        parts
    )


# ============================================================
# プロフィール本人判定
# ============================================================

def message_matches_member(
    message: discord.Message,
    member: discord.Member,
) -> bool:

    # ========================================================
    # ① 本人が直接投稿
    # ========================================================

    if (
        not message.author.bot
        and message.author.id == member.id
    ):
        return True


    # ========================================================
    # ② ID / メンション
    # ========================================================

    user_id_text = str(
        member.id
    )

    mention_1 = (
        f"<@{member.id}>"
    )

    mention_2 = (
        f"<@!{member.id}>"
    )

    content = (
        message.content
        or ""
    )

    if (
        user_id_text in content
        or mention_1 in content
        or mention_2 in content
    ):
        return True


    for user in message.mentions:

        if user.id == member.id:
            return True


    # ========================================================
    # ③ 名前候補を作る
    # ========================================================

    display_name = normalize_text(
        member.display_name
    )

    username = normalize_text(
        member.name
    )

    global_name = normalize_text(
        member.global_name
        or ""
    )

    name_candidates = {
        display_name,
        username,
        global_name,
    }

    name_candidates.discard("")

    profile_title_candidates = {
        f"{name}のプロフィール"
        for name in name_candidates
    }


    # ========================================================
    # ④ 本文に表示名がある場合
    # ========================================================

    normalized_content = normalize_text(
        content
    )

    for name in name_candidates:

        if (
            f"{name}のプロフィール"
            in normalized_content
        ):
            return True


    # ========================================================
    # ⑤ Embed検索
    # ========================================================

    for embed in message.embeds:

        # ----------------------------------------
        # Embedタイトル
        # ----------------------------------------

        if embed.title:

            title = normalize_text(
                embed.title
            )

            # 例:
            # coconaのプロフィール
            if title in profile_title_candidates:
                return True

            # 少し表記揺れがあっても拾う
            for name in name_candidates:

                if (
                    name
                    and name in title
                    and "プロフィール" in title
                ):
                    return True


        # ----------------------------------------
        # Embed全体
        # ----------------------------------------

        embed_text_raw = embed_to_text(
            embed
        )

        embed_text = normalize_text(
            embed_text_raw
        )

        if (
            user_id_text in embed_text
            or normalize_text(mention_1) in embed_text
            or normalize_text(mention_2) in embed_text
        ):
            return True


        # 表示名 + プロフィール
        for name in name_candidates:

            if (
                name
                and name in embed_text
                and "プロフィール" in embed_text
            ):
                return True


    return False


# ============================================================
# プロフィール検索
# ============================================================

async def find_profile_message(
    member: discord.Member,
) -> Optional[discord.Message]:

    channel_ids = profile_channel_candidates(
        member
    )

    log.info(
        "Searching profile | user=%s | display_name=%s | channels=%s",
        member.id,
        member.display_name,
        channel_ids,
    )

    for channel_id in channel_ids:

        channel = await get_channel_safe(
            channel_id
        )

        if channel is None:

            log.warning(
                "Profile channel not found: %s",
                channel_id,
            )

            continue

        if not hasattr(
            channel,
            "history",
        ):

            log.warning(
                "Channel has no history(): %s",
                channel_id,
            )

            continue

        try:

            async for message in channel.history(
                limit=PROFILE_SCAN_LIMIT,
                oldest_first=False,
            ):

                if message_matches_member(
                    message,
                    member,
                ):

                    profile_cache[
                        member.id
                    ] = message.jump_url

                    log.info(
                        "Profile found | user=%s | message=%s | url=%s",
                        member.id,
                        message.id,
                        message.jump_url,
                    )

                    return message

        except discord.Forbidden:

            log.warning(
                "プロフィールCHを閲覧できません | channel=%s",
                channel_id,
            )

        except discord.HTTPException:

            log.exception(
                "プロフィール検索エラー | channel=%s",
                channel_id,
            )

    log.warning(
        "Profile not found | user=%s | display_name=%s",
        member.id,
        member.display_name,
    )

    return None


async def get_profile_url(
    member: discord.Member,
) -> Optional[str]:

    cached = profile_cache.get(
        member.id
    )

    if cached:

        log.info(
            "Profile cache hit | user=%s",
            member.id,
        )

        return cached

    message = await find_profile_message(
        member
    )

    if message is None:
        return None

    return message.jump_url


# ============================================================
# プロフィールカード
# ============================================================

def create_profile_embed(
    member: discord.Member,
    profile_found: bool,
) -> discord.Embed:

    gender = member_gender(
        member
    )

    if gender == "female":

        color = discord.Color.from_rgb(
            244,
            145,
            190,
        )

    elif gender == "male":

        color = discord.Color.from_rgb(
            110,
            165,
            235,
        )

    else:

        color = discord.Color.from_rgb(
            175,
            145,
            225,
        )

    embed = discord.Embed(
        title="🏫 プロフィール",
        description=(
            f"{member.mention} さんが"
            "お部屋に参加しました！"
        ),
        color=color,
    )

    if profile_found:

        embed.add_field(
            name="📖 プロフィール",
            value=(
                "下のボタンから"
                "プロフィールを確認できます。"
            ),
            inline=False,
        )

    else:

        embed.add_field(
            name="⚠️ プロフィール",
            value=(
                "プロフィール投稿を"
                "見つけられませんでした。"
            ),
            inline=False,
        )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.set_footer(
        text=f"ID: {member.id}"
    )

    return embed


def create_profile_view(
    profile_url: str,
) -> discord.ui.View:

    view = discord.ui.View(
        timeout=None
    )

    button = discord.ui.Button(
        label="プロフィールを見る",
        emoji="📖",
        style=discord.ButtonStyle.link,
        url=profile_url,
    )

    view.add_item(
        button
    )

    return view


# ============================================================
# VCインチャへプロフィール投稿
# ============================================================

async def post_profile_to_voice_chat(
    channel: discord.VoiceChannel,
    member: discord.Member,
) -> None:

    key = (
        channel.id,
        member.id,
    )

    if (
        POST_ONCE_PER_ROOM
        and key in posted_in_room
    ):

        log.info(
            "Already posted | room=%s | user=%s",
            channel.id,
            member.id,
        )

        return

    profile_url = await get_profile_url(
        member
    )

    embed = create_profile_embed(
        member,
        profile_url is not None,
    )

    try:

        if profile_url:

            await channel.send(
                embed=embed,
                view=create_profile_view(
                    profile_url
                ),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )

        else:

            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )

        posted_in_room.add(
            key
        )

        log.info(
            "Profile posted | room=%s | user=%s | found=%s",
            channel.id,
            member.id,
            profile_url is not None,
        )

    except discord.Forbidden:

        log.warning(
            "VCインチャへ送信できません | room=%s",
            channel.id,
        )

    except discord.HTTPException:

        log.exception(
            "VCインチャ送信エラー | room=%s",
            channel.id,
        )


# ============================================================
# シャベレア移動後の最終VC確認
# ============================================================

async def wait_for_final_voice_channel(
    guild_id: int,
    member_id: int,
):

    try:

        await asyncio.sleep(
            MOVE_SETTLE_SECONDS
        )

        guild = client.get_guild(
            guild_id
        )

        if guild is None:

            log.warning(
                "Guild not found while settling: %s",
                guild_id,
            )

            return

        member = guild.get_member(
            member_id
        )

        if member is None:

            try:

                member = await guild.fetch_member(
                    member_id
                )

            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):

                log.warning(
                    "Member not found: %s",
                    member_id,
                )

                return

        voice_state = member.voice

        if voice_state is None:

            log.info(
                "No voice state after settle | user=%s",
                member_id,
            )

            return

        channel = voice_state.channel

        if channel is None:
            return

        log.info(
            "FINAL CHECK | user=%s | channel=%s | category=%s",
            member.id,
            channel.id,
            channel.category_id,
        )

        if not isinstance(
            channel,
            discord.VoiceChannel,
        ):

            return

        # 指定カテゴリー以外は無視
        if (
            channel.category_id
            != PROFILE_VOICE_CATEGORY_ID
        ):

            log.info(
                "Ignored category | user=%s | category=%s",
                member.id,
                channel.category_id,
            )

            return

        await post_profile_to_voice_chat(
            channel,
            member,
        )

    except asyncio.CancelledError:

        log.info(
            "Settle task cancelled | user=%s",
            member_id,
        )

        return

    finally:

        current_task = asyncio.current_task()

        if (
            settle_tasks.get(
                member_id
            )
            is current_task
        ):

            settle_tasks.pop(
                member_id,
                None,
            )


# ============================================================
# 起動
# ============================================================

@client.event
async def on_ready():

    log.info(
        "Logged in as %s (%s)",
        client.user,
        client.user.id if client.user else "?",
    )

    guild = client.get_guild(
        GUILD_ID
    )

    if guild:

        log.info(
            "Puraudhia connected: %s (%s)",
            guild.name,
            guild.id,
        )

        log.info(
            "Watching category: %s",
            PROFILE_VOICE_CATEGORY_ID,
        )

    else:

        log.warning(
            "Puraudhia guild not found: %s",
            GUILD_ID,
        )


# ============================================================
# VC入退室・移動監視
# ============================================================

@client.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):

    log.info(
        "VOICE EVENT | user=%s | before=%s | after=%s",
        member.id,
        before.channel.id if before.channel else None,
        after.channel.id if after.channel else None,
    )

    if member.bot:
        return

    if member.guild.id != GUILD_ID:
        return

    if before.channel == after.channel:
        return

    old_task = settle_tasks.get(
        member.id
    )

    if (
        old_task
        and not old_task.done()
    ):

        old_task.cancel()

    if after.channel is None:

        log.info(
            "Voice leave | user=%s",
            member.id,
        )

        return

    task = asyncio.create_task(
        wait_for_final_voice_channel(
            member.guild.id,
            member.id,
        )
    )

    settle_tasks[
        member.id
    ] = task


# ============================================================
# VC削除時
# ============================================================

@client.event
async def on_guild_channel_delete(
    channel: discord.abc.GuildChannel,
):

    if channel.guild.id != GUILD_ID:
        return

    keys = [
        key
        for key in posted_in_room
        if key[0] == channel.id
    ]

    for key in keys:

        posted_in_room.discard(
            key
        )

    log.info(
        "Room cache cleared | room=%s",
        channel.id,
    )


# ============================================================
# 新しいプロフィール投稿をキャッシュ
# ============================================================

@client.event
async def on_message(
    message: discord.Message,
):

    if message.guild is None:
        return

    if message.guild.id != GUILD_ID:
        return

    if message.channel.id not in {
        MALE_PROFILE_CHANNEL_ID,
        FEMALE_PROFILE_CHANNEL_ID,
    }:
        return

    # 本人が直接投稿
    if not message.author.bot:

        profile_cache[
            message.author.id
        ] = message.jump_url


    # メンション
    for user in message.mentions:

        profile_cache[
            user.id
        ] = message.jump_url


    # ID検索
    searchable_text = (
        message.content
        or ""
    )

    for embed in message.embeds:

        searchable_text += (
            "\n"
            + embed_to_text(
                embed
            )
        )

    possible_ids = re.findall(
        r"\b\d{17,20}\b",
        searchable_text,
    )

    for raw_id in possible_ids:

        try:

            user_id = int(
                raw_id
            )

            profile_cache[
                user_id
            ] = message.jump_url

        except ValueError:
            pass


# ============================================================
# プロフィール編集時
# ============================================================

@client.event
async def on_message_edit(
    before: discord.Message,
    after: discord.Message,
):

    if after.guild is None:
        return

    if after.guild.id != GUILD_ID:
        return

    if after.channel.id not in {
        MALE_PROFILE_CHANNEL_ID,
        FEMALE_PROFILE_CHANNEL_ID,
    }:
        return

    if not after.author.bot:

        profile_cache[
            after.author.id
        ] = after.jump_url

    for user in after.mentions:

        profile_cache[
            user.id
        ] = after.jump_url


# ============================================================
# 起動
# ============================================================

if __name__ == "__main__":

    if not TOKEN:

        raise RuntimeError(
            "環境変数 DISCORD_TOKEN が設定されていません。"
        )

    client.run(
        TOKEN
    )
