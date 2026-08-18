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
# ・VC生成はしない
# ・パネル設置もしない
# ・シャベレアで自動移動された「最終VC」を監視
# ・対象カテゴリー内のVCだけプロフィール表示
# ・VCのインチャへ直接プロフィールカードを投稿
# ・「プロフィールを見る」で本人のプロフィールへジャンプ
# ============================================================


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()


# ============================================================
# Puraudhia 設定
# ============================================================

GUILD_ID = 1458016711344263170


# ============================================================
# プロフィール表示対象カテゴリー
# ============================================================

PROFILE_VOICE_CATEGORY_IDS = {
    1519531377811259392,
    1519531975231279176,
    1519531583210655854,
    1519531758809518212,
}


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

# シャベレアの自動移動が終わるまで待つ時間
MOVE_SETTLE_SECONDS = 3.0

# プロフィール検索で遡る最大投稿数
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

log = logging.getLogger(
    "puraudhia-profile"
)


# ============================================================
# Intents
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.voice_states = True

# プロフィール投稿本文・EmbedからIDを探すために必要
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

# user_id -> profile URL
profile_cache: dict[int, str] = {}

# (voice_channel_id, user_id)
posted_in_room: set[tuple[int, int]] = set()

# ユーザーごとの「移動確定待ち」タスク
settle_tasks: dict[int, asyncio.Task] = {}


# ============================================================
# 共通関数
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


def member_gender(
    member: discord.Member,
) -> Optional[str]:

    role_ids = {
        role.id
        for role in member.roles
    }

    male = (
        MALE_ROLE_ID
        in role_ids
    )

    female = (
        FEMALE_ROLE_ID
        in role_ids
    )

    if male and not female:
        return "male"

    if female and not male:
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

    # 性別ロールが無い場合も両方探す
    return [
        MALE_PROFILE_CHANNEL_ID,
        FEMALE_PROFILE_CHANNEL_ID,
    ]


# ============================================================
# Embed内文字取得
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

    if embed.author:
        if embed.author.name:
            parts.append(
                embed.author.name
            )

    if embed.footer:
        if embed.footer.text:
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

    # 本人が直接プロフィール投稿している場合
    if (
        not message.author.bot
        and message.author.id
        == member.id
    ):
        return True

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

    # 本文
    if (
        user_id_text in content
        or mention_1 in content
        or mention_2 in content
    ):
        return True

    # Discord mention情報
    for user in message.mentions:

        if user.id == member.id:
            return True

    # Embed
    for embed in message.embeds:

        text = embed_to_text(
            embed
        )

        if (
            user_id_text in text
            or mention_1 in text
            or mention_2 in text
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
                        "Profile found | user=%s | message=%s",
                        member.id,
                        message.id,
                    )

                    return message

        except discord.Forbidden:

            log.warning(
                "プロフィールCHを閲覧できません: %s",
                channel_id,
            )

        except discord.HTTPException:

            log.exception(
                "プロフィール検索エラー: %s",
                channel_id,
            )

    return None


async def get_profile_url(
    member: discord.Member,
) -> Optional[str]:

    cached = profile_cache.get(
        member.id
    )

    if cached:
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
# VCのインチャへプロフィール投稿
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
            "Profile posted | room=%s | user=%s",
            channel.id,
            member.id,
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
# シャベレア自動移動対応
# ============================================================

async def wait_for_final_voice_channel(
    member: discord.Member,
):

    try:

        # シャベレアが移動させるまで待つ
        await asyncio.sleep(
            MOVE_SETTLE_SECONDS
        )

        # 現在のVCを改めて取得
        voice_state = member.voice

        if voice_state is None:
            return

        channel = voice_state.channel

        if channel is None:
            return

        if not isinstance(
            channel,
            discord.VoiceChannel,
        ):
            return

        # --------------------------------------------
        # 対象カテゴリー以外なら無視
        # --------------------------------------------

        if (
            channel.category_id
            not in PROFILE_VOICE_CATEGORY_IDS
        ):
            return

        log.info(
            "Final VC confirmed | user=%s | room=%s | category=%s",
            member.id,
            channel.id,
            channel.category_id,
        )

        # --------------------------------------------
        # 最終的にいるVCのインチャへ投稿
        # --------------------------------------------

        await post_profile_to_voice_chat(
            channel,
            member,
        )

    except asyncio.CancelledError:

        # 再移動された場合は前の待機処理を破棄
        return

    finally:

        current_task = asyncio.current_task()

        if (
            settle_tasks.get(
                member.id
            )
            is current_task
        ):

            settle_tasks.pop(
                member.id,
                None,
            )


# ============================================================
# Bot起動
# ============================================================

@client.event
async def on_ready():

    log.info(
        "Logged in as %s (%s)",
        client.user,
        (
            client.user.id
            if client.user
            else "?"
        ),
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
            "Watching categories: %s",
            sorted(
                PROFILE_VOICE_CATEGORY_IDS
            ),
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

    if member.bot:
        return

    if member.guild.id != GUILD_ID:
        return

    # VC変更無し
    if before.channel == after.channel:
        return

    # --------------------------------------------
    # このユーザーの前回の待機処理をキャンセル
    #
    # 待機VC
    # ↓
    # シャベレア生成VC
    #
    # と短時間で動いた場合に、
    # 待機VCへ投稿されるのを防ぐ
    # --------------------------------------------

    old_task = settle_tasks.get(
        member.id
    )

    if (
        old_task
        and not old_task.done()
    ):

        old_task.cancel()

    # 完全退出ならここで終了
    if after.channel is None:
        return

    # --------------------------------------------
    # 新しい移動先が発生したら、
    # 3秒後に「現在いるVC」を再確認
    # --------------------------------------------

    task = asyncio.create_task(
        wait_for_final_voice_channel(
            member
        )
    )

    settle_tasks[
        member.id
    ] = task


# ============================================================
# VC削除時
# 投稿済みキャッシュを掃除
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
# プロフィール投稿キャッシュ更新
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

    # --------------------------------------------
    # 本人が直接投稿
    # --------------------------------------------

    if not message.author.bot:

        profile_cache[
            message.author.id
        ] = message.jump_url

    # --------------------------------------------
    # メンション
    # --------------------------------------------

    for user in message.mentions:

        profile_cache[
            user.id
        ] = message.jump_url

    # --------------------------------------------
    # 本文・EmbedのIDを検索
    # --------------------------------------------

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
