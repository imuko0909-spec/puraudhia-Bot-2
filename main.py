from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# HP制 人狼Bot
#
# ・参加パネル
# ・役職ランダム配布
# ・プレイヤー専用プライベートスレッド
# ・役職画像表示
# ・投票
# ・騎士
# ・占い師
# ・人狼
# ・黒猫
# ・ライフ自動管理
# ・朝の結果発表
# ・勝敗自動判定
# ============================================================


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

BASE_DIR = Path(__file__).parent
ASSET_DIR = BASE_DIR / "assets"


# ============================================================
# ログ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("jinro_bot")


# ============================================================
# Bot設定
# ============================================================

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# ゲームルール
# ============================================================

DEFAULT_DAY_MINUTES = 3

# CO解禁日
CO_UNLOCK_DAY = 2

# 騎士
KNIGHT_CAN_GUARD_SAME_TARGET_CONSECUTIVELY = True

# 人狼ダメージ
FIRST_NIGHT_WOLF_DAMAGE = 2
NORMAL_WOLF_DAMAGE = 3

# 投票
VOTE_SOLO_DAMAGE = 2
VOTE_TIE_DAMAGE = 1

# 占い師
SEER_DAMAGE_TO_WOLF_SIDE = 2

# 騎士防衛成功時
KNIGHT_BLOCK_DAMAGE = 1

# 黒猫
BLACKCAT_REVENGE_DAMAGE = 2


# ============================================================
# 役職
# ============================================================

@dataclass(frozen=True)
class RoleDef:
    key: str
    name: str
    hp: int
    image: str
    team: str
    description: str


ROLES = {

    "villager": RoleDef(
        key="villager",
        name="村人",
        hp=5,
        image="villager.png",
        team="village",
        description=(
            "《一般人》\n\n"
            "能力はありません。\n"
            "会議と投票で人狼を見つけましょう。"
        ),
    ),

    "seer": RoleDef(
        key="seer",
        name="占い師",
        hp=5,
        image="seer.png",
        team="village",
        description=(
            "《人狼を見つける役職》\n\n"
            "夜にプレイヤーを1人選び、陣営を確認できます。\n\n"
            "結果は\n"
            "・人狼陣営\n"
            "・人狼陣営ではない\n\n"
            "のどちらかです。\n\n"
            "人狼または黒猫を占った場合、"
            "対象に2ダメージ与えます。\n\n"
            "同じ相手を連続で占うことも可能です。"
        ),
    ),

    "knight": RoleDef(
        key="knight",
        name="騎士",
        hp=5,
        image="knight.png",
        team="village",
        description=(
            "《市民を守る役職》\n\n"
            "夜にプレイヤーを1人選んで守ります。\n\n"
            "守った相手を人狼が攻撃した場合、"
            "人狼の攻撃を無効にします。\n\n"
            "その代わり騎士本人が1ダメージ受けます。\n\n"
            "自分自身を守ることはできません。"
        ),
    ),

    "wolf": RoleDef(
        key="wolf",
        name="人狼",
        hp=10,
        image="wolf.png",
        team="wolf",
        description=(
            "《市民陣営を倒す役職》\n\n"
            "夜にプレイヤーを1人選んで攻撃します。\n\n"
            "1日目：2ダメージ\n"
            "2日目以降：3ダメージ"
        ),
    ),

    "blackcat": RoleDef(
        key="blackcat",
        name="黒猫",
        hp=6,
        image="blackcat.png",
        team="wolf",
        description=(
            "《自分を犠牲にして人狼を勝たせる役職》\n\n"
            "投票によるダメージで死亡した場合、"
            "プレイヤーを1人指定して2ダメージ与えます。\n\n"
            "勝敗判定では人狼陣営としてカウントします。"
        ),
    ),

    "traitor": RoleDef(
        key="traitor",
        name="裏切り者",
        hp=5,
        image="traitor.png",
        team="wolf",
        description=(
            "《場をかき乱して人狼を勝たせる役職》\n\n"
            "能力はありません。\n"
            "人狼陣営を勝利へ導いてください。"
        ),
    ),
}


# ============================================================
# プレイヤーデータ
# ============================================================

@dataclass
class PlayerState:
    user_id: int
    role_key: str
    hp: int
    max_hp: int

    alive: bool = True

    private_thread_id: Optional[int] = None

    last_guard_target: Optional[int] = None

    death_cause: Optional[str] = None


# ============================================================
# ゲーム設定
# ============================================================

@dataclass
class GameConfig:

    villager: int = 2
    seer: int = 1
    knight: int = 1
    wolf: int = 1
    blackcat: int = 1
    traitor: int = 0

    day_minutes: int = DEFAULT_DAY_MINUTES

    def role_pool(self) -> list[str]:

        pool = []

        pool.extend(["villager"] * self.villager)
        pool.extend(["seer"] * self.seer)
        pool.extend(["knight"] * self.knight)
        pool.extend(["wolf"] * self.wolf)
        pool.extend(["blackcat"] * self.blackcat)
        pool.extend(["traitor"] * self.traitor)

        return pool

    @property
    def total(self) -> int:
        return len(self.role_pool())


# ============================================================
# ゲームデータ
# ============================================================

@dataclass
class GameState:

    guild_id: int
    channel_id: int
    host_id: int

    config: GameConfig = field(default_factory=GameConfig)

    joined: list[int] = field(default_factory=list)

    players: dict[int, PlayerState] = field(default_factory=dict)

    day: int = 1

    phase: str = "lobby"

    started: bool = False

    votes: dict[int, int] = field(default_factory=dict)

    guard_target: Optional[int] = None
    guard_actor: Optional[int] = None

    seer_target: Optional[int] = None
    seer_actor: Optional[int] = None

    wolf_target: Optional[int] = None
    wolf_actor: Optional[int] = None

    blackcat_target: Optional[int] = None
    blackcat_actor: Optional[int] = None

    public_results: list[str] = field(default_factory=list)

    voted_blackcat_pending: Optional[int] = None


games: dict[int, GameState] = {}


# ============================================================
# 共通処理
# ============================================================

def alive_ids(game: GameState) -> list[int]:

    return [
        uid
        for uid, player in game.players.items()
        if player.alive
    ]


def member_name(
    guild: discord.Guild,
    uid: int
) -> str:

    member = guild.get_member(uid)

    if member:
        return member.display_name

    return f"User {uid}"


def hp_bar(
    hp: int,
    max_hp: int
) -> str:

    hp = max(0, hp)

    full = "❤️" * hp

    empty = "🖤" * max(
        0,
        max_hp - hp
    )

    return f"{full}{empty}\n**{hp}/{max_hp}**"


async def get_private_thread(
    guild: discord.Guild,
    player: PlayerState
):

    if not player.private_thread_id:
        return None

    thread = guild.get_thread(
        player.private_thread_id
    )

    if thread:
        return thread

    try:

        channel = await guild.fetch_channel(
            player.private_thread_id
        )

        if isinstance(
            channel,
            discord.Thread
        ):
            return channel

    except Exception:
        pass

    return None


async def safe_send_thread(
    guild: discord.Guild,
    player: PlayerState,
    *args,
    **kwargs
):

    thread = await get_private_thread(
        guild,
        player
    )

    if thread:
        await thread.send(
            *args,
            **kwargs
        )


# ============================================================
# 役職カード
# ============================================================

async def send_role_card(
    guild: discord.Guild,
    player: PlayerState
):

    role = ROLES[
        player.role_key
    ]

    embed = discord.Embed(
        title=f"🎭 あなたの役職：{role.name}",
        description=role.description,
        color=discord.Color.dark_gold()
    )

    embed.add_field(
        name="❤️ ライフ",
        value=hp_bar(
            player.hp,
            player.max_hp
        ),
        inline=False
    )

    team_name = (
        "村人陣営"
        if role.team == "village"
        else "人狼陣営"
    )

    embed.add_field(
        name="陣営",
        value=team_name,
        inline=False
    )

    image_path = (
        ASSET_DIR /
        role.image
    )

    if image_path.exists():

        file = discord.File(
            image_path,
            filename=role.image
        )

        embed.set_image(
            url=f"attachment://{role.image}"
        )

        await safe_send_thread(
            guild,
            player,
            file=file,
            embed=embed
        )

    else:

        await safe_send_thread(
            guild,
            player,
            embed=embed
        )


# ============================================================
# HP通知
# ============================================================

async def update_hp_notice(
    guild: discord.Guild,
    player: PlayerState
):

    await safe_send_thread(
        guild,
        player,
        "❤️ **現在のライフ**\n"
        f"{hp_bar(player.hp, player.max_hp)}"
    )


# ============================================================
# ダメージ
# ============================================================

def apply_damage(
    game: GameState,
    uid: int,
    amount: int,
    cause: str
):

    player = game.players[
        uid
    ]

    if not player.alive:
        return False

    player.hp -= amount

    if player.hp < 0:
        player.hp = 0

    died = (
        player.hp <= 0
    )

    if died:

        player.alive = False

        player.death_cause = cause

    return died


# ============================================================
# 勝敗判定
# ============================================================

def check_winner(
    game: GameState
) -> Optional[str]:

    living_players = [
        player
        for player
        in game.players.values()
        if player.alive
    ]

    living_wolves = [
        player
        for player
        in living_players
        if player.role_key == "wolf"
    ]

    # 人狼が全滅
    if not living_wolves:

        return "village"

    wolf_side = [
        player
        for player
        in living_players
        if ROLES[player.role_key].team == "wolf"
    ]

    village_side = [
        player
        for player
        in living_players
        if ROLES[player.role_key].team == "village"
    ]

    # 村人側全滅
    if not village_side:

        return "wolf"

    # 人狼側と村側が同数以下
    if len(wolf_side) >= len(village_side):

        return "wolf"

    return None


# ============================================================
# プレイヤー選択
# ============================================================

class PlayerSelect(
    discord.ui.Select
):

    def __init__(
        self,
        game: GameState,
        actor_id: int,
        action: str,
        guild: discord.Guild
    ):

        self.game = game

        self.actor_id = actor_id

        self.action = action

        options = []

        for uid in alive_ids(game):

            if action in {
                "guard",
                "seer",
                "wolf"
            }:

                if uid == actor_id:
                    continue

            options.append(
                discord.SelectOption(
                    label=member_name(
                        guild,
                        uid
                    ),
                    value=str(uid)
                )
            )

        placeholder_map = {

            "vote":
                "投票する相手を選択",

            "guard":
                "護衛する相手を選択",

            "seer":
                "占う相手を選択",

            "wolf":
                "襲撃する相手を選択",

            "blackcat":
                "道連れにする相手を選択"
        }

        super().__init__(
            placeholder=placeholder_map[action],
            min_values=1,
            max_values=1,
            options=options[:25]
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):

        if interaction.user.id != self.actor_id:

            await interaction.response.send_message(
                "これはあなた専用の操作です。",
                ephemeral=True
            )

            return

        guild = interaction.guild

        if not guild:
            return

        target_id = int(
            self.values[0]
        )

        game = self.game

        # ============================================
        # 投票
        # ============================================

        if self.action == "vote":

            if game.phase != "vote":

                await interaction.response.send_message(
                    "現在は投票フェイズではありません。",
                    ephemeral=True
                )

                return

            game.votes[
                self.actor_id
            ] = target_id

            await interaction.response.send_message(
                f"🗳️ **{member_name(guild, target_id)}** さんに投票しました。",
                ephemeral=True
            )

        # ============================================
        # 騎士
        # ============================================

        elif self.action == "guard":

            if game.phase != "night":

                await interaction.response.send_message(
                    "現在は夜ではありません。",
                    ephemeral=True
                )

                return

            actor = game.players[
                self.actor_id
            ]

            if target_id == self.actor_id:

                await interaction.response.send_message(
                    "自分自身は護衛できません。",
                    ephemeral=True
                )

                return

            if (
                not KNIGHT_CAN_GUARD_SAME_TARGET_CONSECUTIVELY
                and actor.last_guard_target == target_id
            ):

                await interaction.response.send_message(
                    "同じ相手を連続で守ることはできません。",
                    ephemeral=True
                )

                return

            game.guard_actor = self.actor_id

            game.guard_target = target_id

            await interaction.response.send_message(
                f"🛡️ **{member_name(guild, target_id)}** さんを護衛します。",
                ephemeral=True
            )

        # ============================================
        # 占い師
        # ============================================

        elif self.action == "seer":

            if game.phase != "night":

                await interaction.response.send_message(
                    "現在は夜ではありません。",
                    ephemeral=True
                )

                return

            game.seer_actor = self.actor_id

            game.seer_target = target_id

            target = game.players[
                target_id
            ]

            if target.role_key in {
                "wolf",
                "blackcat",
                "traitor"
            }:

                result = "人狼陣営"

            else:

                result = "人狼陣営ではない"

            await interaction.response.send_message(
                f"🔮 **占い結果**\n\n"
                f"{member_name(guild, target_id)} さんは\n"
                f"**「{result}」** です。",
                ephemeral=True
            )

        # ============================================
        # 人狼
        # ============================================

        elif self.action == "wolf":

            if game.phase != "night":

                await interaction.response.send_message(
                    "現在は夜ではありません。",
                    ephemeral=True
                )

                return

            game.wolf_actor = self.actor_id

            game.wolf_target = target_id

            await interaction.response.send_message(
                f"🐺 **{member_name(guild, target_id)}** さんを襲撃対象にしました。",
                ephemeral=True
            )

        # ============================================
        # 黒猫
        # ============================================

        elif self.action == "blackcat":

            game.blackcat_actor = self.actor_id

            game.blackcat_target = target_id

            await interaction.response.send_message(
                f"🐈‍⬛ **{member_name(guild, target_id)}** さんを道連れ対象にしました。",
                ephemeral=True
            )


# ============================================================
# 行動View
# ============================================================

class PlayerActionView(
    discord.ui.View
):

    def __init__(
        self,
        game: GameState,
        actor_id: int,
        action: str,
        guild: discord.Guild
    ):

        super().__init__(
            timeout=None
        )

        select = PlayerSelect(
            game,
            actor_id,
            action,
            guild
        )

        self.add_item(
            select
        )


# ============================================================
# 投票パネル
# ============================================================

async def send_vote_panels(
    guild: discord.Guild,
    game: GameState
):

    for uid in alive_ids(game):

        player = game.players[
            uid
        ]

        await safe_send_thread(
            guild,
            player,
            "🗳️ **投票フェイズ**\n\n"
            "下から投票するプレイヤーを選択してください。\n\n"
            "単独最多票：2ダメージ\n"
            "同票最多：対象全員1ダメージ",
            view=PlayerActionView(
                game,
                uid,
                "vote",
                guild
            )
        )


# ============================================================
# 投票処理
# ============================================================

async def resolve_votes(
    guild: discord.Guild,
    game: GameState
):

    if not game.votes:

        game.public_results.append(
            "🗳️ 投票によるダメージはありませんでした。"
        )

        return

    counts = {}

    for voter_id, target_id in game.votes.items():

        voter = game.players.get(
            voter_id
        )

        if not voter:
            continue

        if not voter.alive:
            continue

        counts[target_id] = (
            counts.get(
                target_id,
                0
            )
            + 1
        )

    if not counts:
        return

    max_votes = max(
        counts.values()
    )

    targets = [
        uid
        for uid, votes
        in counts.items()
        if votes == max_votes
    ]

    if len(targets) == 1:

        damage = VOTE_SOLO_DAMAGE

    else:

        damage = VOTE_TIE_DAMAGE

    for uid in targets:

        player = game.players[
            uid
        ]

        died = apply_damage(
            game,
            uid,
            damage,
            "vote"
        )

        game.public_results.append(
            f"💥 {member_name(guild, uid)} さんが "
            f"{damage}ダメージを受けました。"
        )

        await update_hp_notice(
            guild,
            player
        )

        if died:

            game.public_results.append(
                f"☠️ {member_name(guild, uid)} さんが死亡しました。"
            )

            # 黒猫
            if player.role_key == "blackcat":

                game.voted_blackcat_pending = uid


# ============================================================
# 夜の行動パネル
# ============================================================

async def send_night_actions(
    guild: discord.Guild,
    game: GameState
):

    for uid, player in game.players.items():

        if not player.alive:
            continue

        # 騎士
        if player.role_key == "knight":

            await safe_send_thread(
                guild,
                player,
                "🛡️ **騎士の行動**\n\n"
                "今夜守るプレイヤーを選択してください。",
                view=PlayerActionView(
                    game,
                    uid,
                    "guard",
                    guild
                )
            )

        # 占い師
        elif player.role_key == "seer":

            await safe_send_thread(
                guild,
                player,
                "🔮 **占い師の行動**\n\n"
                "今夜占うプレイヤーを選択してください。",
                view=PlayerActionView(
                    game,
                    uid,
                    "seer",
                    guild
                )
            )

        # 人狼
        elif player.role_key == "wolf":

            await safe_send_thread(
                guild,
                player,
                "🐺 **人狼の行動**\n\n"
                "今夜攻撃するプレイヤーを選択してください。",
                view=PlayerActionView(
                    game,
                    uid,
                    "wolf",
                    guild
                )
            )

    # 黒猫
    if game.voted_blackcat_pending:

        uid = game.voted_blackcat_pending

        player = game.players[
            uid
        ]

        await safe_send_thread(
            guild,
            player,
            "🐈‍⬛ **黒猫の能力発動**\n\n"
            "投票によって死亡しました。\n"
            "道連れにするプレイヤーを選択してください。",
            view=PlayerActionView(
                game,
                uid,
                "blackcat",
                guild
            )
        )


# ============================================================
# 夜処理
# ============================================================

async def resolve_night(
    guild: discord.Guild,
    game: GameState
):

    # ========================================================
    # 騎士
    # ========================================================

    if game.guard_actor:

        guard = game.players.get(
            game.guard_actor
        )

        if guard:

            guard.last_guard_target = (
                game.guard_target
            )

    # ========================================================
    # 占い師
    # ========================================================

    if (
        game.seer_actor
        and game.seer_target
    ):

        seer = game.players.get(
            game.seer_actor
        )

        target = game.players.get(
            game.seer_target
        )

        if (
            seer
            and seer.alive
            and target
            and target.alive
            and target.role_key in {
                "wolf",
                "blackcat"
            }
        ):

            died = apply_damage(
                game,
                game.seer_target,
                SEER_DAMAGE_TO_WOLF_SIDE,
                "seer"
            )

            game.public_results.append(
                f"💥 {member_name(guild, game.seer_target)} さんが "
                f"{SEER_DAMAGE_TO_WOLF_SIDE}ダメージを受けました。"
            )

            await update_hp_notice(
                guild,
                target
            )

            if died:

                game.public_results.append(
                    f"☠️ {member_name(guild, game.seer_target)} さんが死亡しました。"
                )

    # ========================================================
    # 人狼
    # ========================================================

    if (
        game.wolf_actor
        and game.wolf_target
    ):

        wolf = game.players.get(
            game.wolf_actor
        )

        target = game.players.get(
            game.wolf_target
        )

        if (
            wolf
            and wolf.alive
            and target
            and target.alive
        ):

            guarded = (
                game.guard_target
                == game.wolf_target
            )

            guard = None

            if game.guard_actor:

                guard = game.players.get(
                    game.guard_actor
                )

            if (
                guarded
                and guard
                and guard.alive
            ):

                died = apply_damage(
                    game,
                    game.guard_actor,
                    KNIGHT_BLOCK_DAMAGE,
                    "guard"
                )

                await safe_send_thread(
                    guild,
                    guard,
                    "🛡️ **防衛成功！**\n\n"
                    "あなたが守っていたプレイヤーが人狼に襲われました。\n\n"
                    "人狼の攻撃を無効化しました。\n"
                    f"あなたは{KNIGHT_BLOCK_DAMAGE}ダメージを受けました。"
                )

                game.public_results.append(
                    f"💥 {member_name(guild, game.guard_actor)} さんが "
                    f"{KNIGHT_BLOCK_DAMAGE}ダメージを受けました。"
                )

                await update_hp_notice(
                    guild,
                    guard
                )

                if died:

                    game.public_results.append(
                        f"☠️ {member_name(guild, game.guard_actor)} さんが死亡しました。"
                    )

            else:

                if game.day == 1:

                    damage = (
                        FIRST_NIGHT_WOLF_DAMAGE
                    )

                else:

                    damage = (
                        NORMAL_WOLF_DAMAGE
                    )

                died = apply_damage(
                    game,
                    game.wolf_target,
                    damage,
                    "wolf"
                )

                game.public_results.append(
                    f"💥 {member_name(guild, game.wolf_target)} さんが "
                    f"{damage}ダメージを受けました。"
                )

                await update_hp_notice(
                    guild,
                    target
                )

                if died:

                    game.public_results.append(
                        f"☠️ {member_name(guild, game.wolf_target)} さんが死亡しました。"
                    )

    # ========================================================
    # 黒猫
    # ========================================================

    if (
        game.voted_blackcat_pending
        and game.blackcat_target
    ):

        target = game.players.get(
            game.blackcat_target
        )

        if (
            target
            and target.alive
        ):

            died = apply_damage(
                game,
                game.blackcat_target,
                BLACKCAT_REVENGE_DAMAGE,
                "blackcat"
            )

            game.public_results.append(
                f"💥 {member_name(guild, game.blackcat_target)} さんが "
                f"{BLACKCAT_REVENGE_DAMAGE}ダメージを受けました。"
            )

            await update_hp_notice(
                guild,
                target
            )

            if died:

                game.public_results.append(
                    f"☠️ {member_name(guild, game.blackcat_target)} さんが死亡しました。"
                )


# ============================================================
# ターンリセット
# ============================================================

def reset_round_actions(
    game: GameState
):

    game.votes.clear()

    game.guard_target = None
    game.guard_actor = None

    game.seer_target = None
    game.seer_actor = None

    game.wolf_target = None
    game.wolf_actor = None

    game.blackcat_target = None
    game.blackcat_actor = None

    game.voted_blackcat_pending = None

    game.public_results.clear()


# ============================================================
# 参加パネル
# ============================================================

class LobbyView(
    discord.ui.View
):

    def __init__(
        self,
        game: GameState
    ):

        super().__init__(
            timeout=None
        )

        self.game = game

    @discord.ui.button(
        label="参加する",
        style=discord.ButtonStyle.success,
        emoji="🐾"
    )
    async def join_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.game.started:

            await interaction.response.send_message(
                "すでにゲームは始まっています。",
                ephemeral=True
            )

            return

        if interaction.user.id in self.game.joined:

            await interaction.response.send_message(
                "すでに参加しています。",
                ephemeral=True
            )

            return

        self.game.joined.append(
            interaction.user.id
        )

        await interaction.response.send_message(
            f"🐾 参加しました！\n"
            f"現在 **{len(self.game.joined)}人** 参加中です。",
            ephemeral=True
        )

    @discord.ui.button(
        label="参加を取り消す",
        style=discord.ButtonStyle.secondary,
        emoji="↩️"
    )
    async def leave_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.game.started:

            await interaction.response.send_message(
                "ゲーム開始後は参加を取り消せません。",
                ephemeral=True
            )

            return

        if interaction.user.id in self.game.joined:

            self.game.joined.remove(
                interaction.user.id
            )

        await interaction.response.send_message(
            "参加を取り消しました。",
            ephemeral=True
        )


# ============================================================
# GM管理パネル
# ============================================================

class GMView(
    discord.ui.View
):

    def __init__(
        self,
        game: GameState
    ):

        super().__init__(
            timeout=None
        )

        self.game = game

    async def check_gm(
        self,
        interaction: discord.Interaction
    ):

        if (
            interaction.user.id
            == self.game.host_id
        ):

            return True

        if interaction.user.guild_permissions.manage_guild:

            return True

        await interaction.response.send_message(
            "GM専用の操作です。",
            ephemeral=True
        )

        return False

    # ========================================================
    # 昼
    # ========================================================

    @discord.ui.button(
        label="昼開始",
        style=discord.ButtonStyle.primary,
        emoji="☀️"
    )
    async def day_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await self.check_gm(
            interaction
        ):
            return

        self.game.phase = "day"

        if self.game.day >= CO_UNLOCK_DAY:

            co_text = (
                "✅ 役職CO・虚偽報告可能"
            )

        else:

            co_text = (
                "🚫 役職CO・虚偽報告禁止"
            )

        await interaction.response.send_message(
            f"☀️ **{self.game.day}日目・昼**\n\n"
            f"会議時間：**{self.game.config.day_minutes}分**\n\n"
            f"{co_text}\n\n"
            "VCで自由に会議してください。"
        )

    # ========================================================
    # 投票
    # ========================================================

    @discord.ui.button(
        label="投票開始",
        style=discord.ButtonStyle.secondary,
        emoji="🗳️"
    )
    async def vote_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await self.check_gm(
            interaction
        ):
            return

        self.game.phase = "vote"

        self.game.votes.clear()

        await interaction.response.send_message(
            "🌆 **投票フェイズ開始**\n\n"
            "各プレイヤーの専用スレッドに"
            "投票画面を送りました。\n\n"
            "投票先・投票数は公開されません。"
        )

        await send_vote_panels(
            interaction.guild,
            self.game
        )

    # ========================================================
    # 夜
    # ========================================================

    @discord.ui.button(
        label="夜開始",
        style=discord.ButtonStyle.danger,
        emoji="🌙"
    )
    async def night_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await self.check_gm(
            interaction
        ):
            return

        if self.game.phase != "vote":

            await interaction.response.send_message(
                "先に投票フェイズを開始してください。",
                ephemeral=True
            )

            return

        await interaction.response.defer()

        await resolve_votes(
            interaction.guild,
            self.game
        )

        self.game.phase = "night"

        await interaction.followup.send(
            "🌙 **夜フェイズ開始**\n\n"
            "役職を持っているプレイヤーは"
            "専用スレッドから行動してください。"
        )

        await send_night_actions(
            interaction.guild,
            self.game
        )

    # ========================================================
    # 朝
    # ========================================================

    @discord.ui.button(
        label="朝にする",
        style=discord.ButtonStyle.success,
        emoji="🌅"
    )
    async def morning_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await self.check_gm(
            interaction
        ):
            return

        if self.game.phase != "night":

            await interaction.response.send_message(
                "現在は夜フェイズではありません。",
                ephemeral=True
            )

            return

        await interaction.response.defer()

        await resolve_night(
            interaction.guild,
            self.game
        )

        winner = check_winner(
            self.game
        )

        if self.game.public_results:

            result_text = "\n".join(
                self.game.public_results
            )

        else:

            result_text = (
                "昨晩、公開されるダメージ情報はありませんでした。"
            )

        # ====================================================
        # 勝者あり
        # ====================================================

        if winner:

            if winner == "village":

                win_text = (
                    "🏆 **村人陣営の勝利！**"
                )

            else:

                win_text = (
                    "🏆 **人狼陣営の勝利！**"
                )

            role_reveal = []

            for uid, player in self.game.players.items():

                role_reveal.append(
                    f"・{member_name(interaction.guild, uid)}"
                    f"：**{ROLES[player.role_key].name}**"
                )

            await interaction.followup.send(
                "🌅 **朝になりました**\n\n"
                f"{result_text}\n\n"
                f"{win_text}\n\n"
                "🎭 **役職公開**\n"
                + "\n".join(role_reveal)
            )

            self.game.phase = "ended"

            return

        # ====================================================
        # 続行
        # ====================================================

        await interaction.followup.send(
            "🌅 **朝になりました**\n\n"
            f"{result_text}\n\n"
            f"生存者：**{len(alive_ids(self.game))}名**"
        )

        self.game.day += 1

        self.game.phase = "day"

        reset_round_actions(
            self.game
        )

    # ========================================================
    # HP確認
    # ========================================================

    @discord.ui.button(
        label="HP確認",
        style=discord.ButtonStyle.secondary,
        emoji="❤️"
    )
    async def hp_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await self.check_gm(
            interaction
        ):
            return

        lines = []

        for uid, player in self.game.players.items():

            status = (
                "🟢"
                if player.alive
                else "☠️"
            )

            lines.append(
                f"{status} "
                f"{member_name(interaction.guild, uid)}"
                f"｜{ROLES[player.role_key].name}"
                f"｜{player.hp}/{player.max_hp}"
            )

        await interaction.response.send_message(
            "❤️ **GM用 HP一覧**\n\n"
            + "\n".join(lines),
            ephemeral=True
        )

    # ========================================================
    # 終了
    # ========================================================

    @discord.ui.button(
        label="ゲーム終了",
        style=discord.ButtonStyle.danger,
        emoji="🛑"
    )
    async def end_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await self.check_gm(
            interaction
        ):
            return

        self.game.phase = "ended"

        await interaction.response.send_message(
            "🛑 人狼ゲームを終了しました。"
        )


# ============================================================
# プライベートスレッド作成
# ============================================================

async def make_private_thread(
    channel: discord.TextChannel,
    member: discord.Member
):

    thread = await channel.create_thread(
        name=f"🎭｜{member.display_name}-専用",
        type=discord.ChannelType.private_thread,
        invitable=False,
        auto_archive_duration=1440,
        reason="HP制人狼 プレイヤー専用"
    )

    await thread.add_user(
        member
    )

    return thread


# ============================================================
# /人狼パネル
# ============================================================

@bot.tree.command(
    name="人狼パネル",
    description="HP制人狼の参加パネルを作成します"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def jinro_panel(
    interaction: discord.Interaction
):

    if not interaction.guild:

        await interaction.response.send_message(
            "サーバー内で使用してください。",
            ephemeral=True
        )

        return

    if not isinstance(
        interaction.channel,
        discord.TextChannel
    ):

        await interaction.response.send_message(
            "テキストチャンネルで実行してください。",
            ephemeral=True
        )

        return

    old_game = games.get(
        interaction.guild.id
    )

    if (
        old_game
        and old_game.phase != "ended"
    ):

        await interaction.response.send_message(
            "現在進行中または準備中のゲームがあります。",
            ephemeral=True
        )

        return

    game = GameState(
        guild_id=interaction.guild.id,
        channel_id=interaction.channel.id,
        host_id=interaction.user.id
    )

    games[
        interaction.guild.id
    ] = game

    embed = discord.Embed(
        title="🐺 HP制 人狼ゲーム",
        description=(
            "参加する人は下のボタンを押してください。\n\n"
            f"現在の必要人数：**{game.config.total}人**\n\n"
            "GMは `/人狼設定` で役職人数を変更できます。"
        ),
        color=discord.Color.dark_red()
    )

    await interaction.response.send_message(
        embed=embed,
        view=LobbyView(game)
    )


# ============================================================
# /人狼設定
# ============================================================

@bot.tree.command(
    name="人狼設定",
    description="人狼ゲームの役職人数を設定します"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def jinro_setup(
    interaction: discord.Interaction,

    村人: app_commands.Range[int, 0, 20] = 2,

    占い師: app_commands.Range[int, 0, 5] = 1,

    騎士: app_commands.Range[int, 0, 5] = 1,

    人狼: app_commands.Range[int, 1, 10] = 1,

    黒猫: app_commands.Range[int, 0, 5] = 1,

    裏切り者: app_commands.Range[int, 0, 5] = 0,

    昼分数: app_commands.Range[int, 1, 10] = 3
):

    if not interaction.guild:
        return

    game = games.get(
        interaction.guild.id
    )

    if not game:

        await interaction.response.send_message(
            "先に `/人狼パネル` を実行してください。",
            ephemeral=True
        )

        return

    if game.started:

        await interaction.response.send_message(
            "ゲーム開始後は役職設定を変更できません。",
            ephemeral=True
        )

        return

    game.config = GameConfig(
        villager=村人,
        seer=占い師,
        knight=騎士,
        wolf=人狼,
        blackcat=黒猫,
        traitor=裏切り者,
        day_minutes=昼分数
    )

    await interaction.response.send_message(
        "✅ **人狼設定を変更しました**\n\n"
        f"👤 村人：{村人}\n"
        f"🔮 占い師：{占い師}\n"
        f"🛡️ 騎士：{騎士}\n"
        f"🐺 人狼：{人狼}\n"
        f"🐈‍⬛ 黒猫：{黒猫}\n"
        f"🎭 裏切り者：{裏切り者}\n\n"
        f"必要人数：**{game.config.total}人**\n"
        f"昼会議：**{昼分数}分**",
        ephemeral=True
    )


# ============================================================
# /人狼開始
# ============================================================

@bot.tree.command(
    name="人狼開始",
    description="参加者を確定して人狼ゲームを開始します"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def jinro_start(
    interaction: discord.Interaction
):

    if not interaction.guild:
        return

    game = games.get(
        interaction.guild.id
    )

    if not game:

        await interaction.response.send_message(
            "先に `/人狼パネル` を実行してください。",
            ephemeral=True
        )

        return

    if game.started:

        await interaction.response.send_message(
            "すでにゲームは開始されています。",
            ephemeral=True
        )

        return

    if len(game.joined) != game.config.total:

        await interaction.response.send_message(
            f"現在の参加者：**{len(game.joined)}人**\n"
            f"必要人数：**{game.config.total}人**",
            ephemeral=True
        )

        return

    channel = interaction.guild.get_channel(
        game.channel_id
    )

    if not isinstance(
        channel,
        discord.TextChannel
    ):

        await interaction.response.send_message(
            "ゲームチャンネルが見つかりません。",
            ephemeral=True
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    role_pool = (
        game.config.role_pool()
    )

    random.shuffle(
        role_pool
    )

    try:

        # ====================================================
        # 役職配布
        # ====================================================

        for user_id, role_key in zip(
            game.joined,
            role_pool
        ):

            member = interaction.guild.get_member(
                user_id
            )

            if not member:

                member = await interaction.guild.fetch_member(
                    user_id
                )

            role = ROLES[
                role_key
            ]

            thread = await make_private_thread(
                channel,
                member
            )

            player = PlayerState(
                user_id=user_id,
                role_key=role_key,
                hp=role.hp,
                max_hp=role.hp,
                private_thread_id=thread.id
            )

            game.players[
                user_id
            ] = player

        game.started = True

        game.phase = "day"

        # ====================================================
        # カード送信
        # ====================================================

        for player in game.players.values():

            await send_role_card(
                interaction.guild,
                player
            )

        # ====================================================
        # 管理パネル
        # ====================================================

        await channel.send(
            "🎮 **HP制 人狼ゲーム開始！**\n\n"
            f"参加者：**{len(game.players)}名**\n\n"
            "各プレイヤー専用スレッドに"
            "役職カードを配布しました。\n\n"
            "GMは下のボタンからゲームを進めてください。",
            view=GMView(game)
        )

        await interaction.followup.send(
            "✅ 役職配布・専用スレッド作成が完了しました。",
            ephemeral=True
        )

    except discord.Forbidden:

        await interaction.followup.send(
            "❌ Botの権限が不足しています。\n\n"
            "以下を確認してください。\n"
            "・チャンネルを見る\n"
            "・メッセージを送信\n"
            "・ファイルを添付\n"
            "・プライベートスレッドを作成\n"
            "・スレッドでメッセージを送信\n"
            "・スレッドを管理",
            ephemeral=True
        )

    except Exception as e:

        log.exception(
            "人狼開始エラー"
        )

        await interaction.followup.send(
            f"❌ エラーが発生しました。\n```{e}```",
            ephemeral=True
        )


# ============================================================
# /人狼リセット
# ============================================================

@bot.tree.command(
    name="人狼リセット",
    description="現在の人狼ゲームをリセットします"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def jinro_reset(
    interaction: discord.Interaction
):

    if not interaction.guild:
        return

    games.pop(
        interaction.guild.id,
        None
    )

    await interaction.response.send_message(
        "♻️ 人狼ゲームをリセットしました。",
        ephemeral=True
    )


# ============================================================
# スラッシュコマンドエラー
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        message = (
            "❌ このコマンドは管理者専用です。"
        )

    else:

        message = (
            f"❌ エラーが発生しました。\n"
            f"`{error}`"
        )

        log.exception(
            "App command error",
            exc_info=error
        )

    try:

        if interaction.response.is_done():

            await interaction.followup.send(
                message,
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    except Exception:
        pass


# ============================================================
# 起動
# ============================================================

@bot.event
async def on_ready():

    log.info(
        "ログインしました: %s",
        bot.user
    )

    try:

        synced = await bot.tree.sync()

        log.info(
            "スラッシュコマンド同期完了: %s個",
            len(synced)
        )

    except Exception:

        log.exception(
            "スラッシュコマンド同期失敗"
        )


if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN が設定されていません。"
    )


bot.run(
    TOKEN
)
