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
# ・同じ人のプロフィールは1部屋につき1枚だけ
# ・退出したらその人のプロフィール投稿を削除
# ・再入室したらまた新しく表示
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

MOVE_SETTLE_SECONDS = 5.0
PROFILE_SCAN_LIMIT = 3000


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

# (voice_channel_id, user_id) -> 投稿したメッセージID
posted_messages: dict[tuple[int, int], int] = {}

# user_id -> settle task
settle_tasks: dict[int, asyncio.Task] = {}

# 同じユーザーの入室処理が重複しないようにする
profile_locks: dict[int, asyncio.Lock] = {}


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

    has_male = MALE_ROLE_ID in role_ids
    has_female = FEMALE_ROLE_ID in role_ids

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

    return [
        MALE_PROFILE_CHANNEL_ID,
        FEMALE_PROFILE_CHANNEL_ID,
    ]


# ============================================================
# Embed → テキスト
# ============================================================

def embed_to_text(
    embed: discord.Embed,
) -> str:

    parts: list[str] = []

    if embed.title:
        parts.append(embed.title)

    if embed.description:
        parts.append(embed.description)

    if embed.author and embed.author.name:
        parts.append(embed.author.name)

    if embed.footer and embed.footer.text:
        parts.append(embed.footer.text)

    for field in embed.fields:

        if field.name:
            parts.append(field.name)

        if field.value:
            parts.append(field.value)

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

    # 本人が直接投稿
    if (
        not message.author.bot
        and message.author.id == member.id
    ):
        return True

    user_id_text = str(
        member.id
    )

    mention_1 = f"<@{member.id}>"
    mention_2 = f"<@!{member.id}>"

    content = (
        message.content
        or ""
    )

    # ID / メンション
    if (
        user_id_text in content
        or mention_1 in content
        or mention_2 in content
    ):
        return True

    for user in message.mentions:

        if user.id == member.id:
            return True

    # 名前候補
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

    normalized_content = normalize_text(
        content
    )

    for name in name_candidates:

        if (
            f"{name}のプロフィール"
            in normalized_content
        ):
            return True

    # Embed検索
    for embed in message.embeds:

        if embed.title:

            title = normalize_text(
                embed.title
            )

            if title in profile_title_candidates:
                return True

            for name in name_candidates:

                if (
                    name
                    and name in title
                    and "プロフィール" in title
                ):
                    return True

        embed_text = normalize_text(
            embed_to_text(
                embed
            )
        )

        if (
            user_id_text in embed_text
            or normalize_text(mention_1) in embed_text
            or normalize_text(mention_2) in embed_text
        ):
            return True

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
# プロフィール投稿
# ============================================================

async def post_profile_to_voice_chat(
    channel: discord.VoiceChannel,
    member: discord.Member,
) -> None:

    key = (
        channel.id,
        member.id,
    )

    lock = profile_locks.setdefault(
        member.id,
        asyncio.Lock(),
    )

    async with lock:

        # 同じVCですでに1枚出ていれば重複させない
        if key in posted_messages:

            log.info(
                "Profile already exists | room=%s | user=%s",
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

                sent_message = await channel.send(
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

                sent_message = await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )

            # 投稿したメッセージIDを記録
            posted_messages[
                key
            ] = sent_message.id

            log.info(
                "Profile posted | room=%s | user=%s | message=%s",
                channel.id,
                member.id,
                sent_message.id,
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
# 退出時プロフィール削除
# ============================================================

async def delete_profile_message(
    channel: discord.VoiceChannel,
    member_id: int,
) -> None:

    key = (
        channel.id,
        member_id,
    )

    message_id = posted_messages.pop(
        key,
        None,
    )

    if message_id is None:
        return

    try:

        message = await channel.fetch_message(
            message_id
        )

        await message.delete()

        log.info(
            "Profile deleted | room=%s | user=%s | message=%s",
            channel.id,
            member_id,
            message_id,
        )

    except discord.NotFound:

        log.info(
            "Profile message already deleted | room=%s | user=%s",
            channel.id,
            member_id,
        )

    except discord.Forbidden:

        log.warning(
            "プロフィール投稿を削除できません | room=%s",
            channel.id,
        )

    except discord.HTTPException:

        log.exception(
            "プロフィール削除エラー | room=%s",
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
                return

        voice_state = member.voice

        if voice_state is None:
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

        if (
            channel.category_id
            != PROFILE_VOICE_CATEGORY_ID
        ):
            return

        await post_profile_to_voice_chat(
            channel,
            member,
        )

    except asyncio.CancelledError:
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
# VC入退室・移動
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

    # ========================================================
    # ① 元いたVCのプロフィールを削除
    # ========================================================

    if isinstance(
        before.channel,
        discord.VoiceChannel,
    ):

        if (
            before.channel.category_id
            == PROFILE_VOICE_CATEGORY_ID
        ):

            await delete_profile_message(
                before.channel,
                member.id,
            )


    # ========================================================
    # ② 前の待機タスクをキャンセル
    # ========================================================

    old_task = settle_tasks.get(
        member.id
    )

    if (
        old_task
        and not old_task.done()
    ):

        old_task.cancel()


    # ========================================================
    # ③ 完全退出なら終了
    # ========================================================

    if after.channel is None:

        log.info(
            "Voice leave | user=%s",
            member.id,
        )

        return


    # ========================================================
    # ④ 新しいVCへ入ったら5秒後に最終位置確認
    # ========================================================

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
        for key in posted_messages
        if key[0] == channel.id
    ]

    for key in keys:

        posted_messages.pop(
            key,
            None,
        )

    log.info(
        "Room cache cleared | room=%s",
        channel.id,
    )


# ============================================================
# 新しいプロフィール投稿キャッシュ
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

    if not message.author.bot:

        profile_cache[
            message.author.id
        ] = message.jump_url

    for user in message.mentions:

        profile_cache[
            user.id
        ] = message.jump_url

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
