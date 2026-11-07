from __future__ import annotations

import logging

import discord
from discord import ui

from config import CONFIG, save_config

log = logging.getLogger("vouchbot.panel")


def is_owner(member: discord.abc.User | discord.Member) -> bool:
    if member.id in CONFIG["owner_ids"]:
        return True
    return isinstance(member, discord.Member) and member.guild_permissions.administrator


class ConfigPanel(ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild
        self._build()

    def _role_names(self, role_ids: list[int]) -> str:
        names = []
        for role_id in role_ids:
            role = self.guild.get_role(int(role_id))
            names.append(f"@{role.name}" if role else f"`deleted role {role_id}`")
        return ", ".join(names) or "`not set`"

    def _user_names(self, user_ids: list[int]) -> str:
        names = []
        for user_id in user_ids:
            member = self.guild.get_member(int(user_id))
            names.append(
                f"@{member.display_name}" if member else f"`user {user_id}`"
            )
        return ", ".join(names) or "`none`"

    def _channel_name(self, channel_id: int) -> str:
        if not channel_id:
            return "`not set`"
        channel = self.guild.get_channel(int(channel_id))
        return f"#{channel.name}" if channel else f"`deleted channel {channel_id}`"

    def _category_names(self, channel_ids: list[int]) -> str:
        names = []
        for channel_id in channel_ids:
            channel = self.guild.get_channel(int(channel_id))
            names.append(
                f"#{channel.name}" if channel else f"`deleted category {channel_id}`"
            )
        return ", ".join(names) or "`not set`"

    def _build(self) -> None:
        self.clear_items()
        container = ui.Container(accent_colour=0x5865F2)
        container.add_item(
            ui.TextDisplay(
                "## ⚙️ Bot Configuration\n"
                "-# Owner only · every change saves instantly"
            )
        )
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"**Middleman roles**  {self._role_names(CONFIG['staff_role_ids'])}\n"
                f"**Trader roles**  {self._role_names(CONFIG['trader_role_ids'])}\n"
                f"**Whitelist**  {self._role_names(CONFIG['whitelist_role_ids'])} · "
                f"{self._user_names(CONFIG['whitelist_user_ids'])}\n"
                f"**Vouch channel**  {self._channel_name(CONFIG['vouch_channel_id'])}\n"
                f"**Ticket categories**  {self._category_names(CONFIG['ticket_category_ids'])}"
            )
        )
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                "-# Middleman roles can run `.vouch`, `.close`, `.vouchqueue` and "
                "`.vouchauto`.\n"
                "-# Whitelisted users and roles can run `.vouch` without being staff.\n"
                "-# Traders with a trader role get a ✅ on their vouch cards."
            )
        )

        mm_select = ui.RoleSelect(
            custom_id="cfg_mm_roles",
            placeholder="Middleman roles (staff access)",
            min_values=0,
            max_values=15,
            default_values=[
                discord.Object(id=role_id, type=discord.Role)
                for role_id in CONFIG["staff_role_ids"]
                if self.guild.get_role(int(role_id))
            ],
        )
        mm_select.callback = self._on_mm_roles
        self.mm_select = mm_select
        container.add_item(ui.ActionRow(mm_select))

        trader_select = ui.RoleSelect(
            custom_id="cfg_trader_roles",
            placeholder="Trader roles (verified tag)",
            min_values=0,
            max_values=15,
            default_values=[
                discord.Object(id=role_id, type=discord.Role)
                for role_id in CONFIG["trader_role_ids"]
                if self.guild.get_role(int(role_id))
            ],
        )
        trader_select.callback = self._on_trader_roles
        self.trader_select = trader_select
        container.add_item(ui.ActionRow(trader_select))

        whitelist_defaults = [
            discord.Object(id=role_id, type=discord.Role)
            for role_id in CONFIG["whitelist_role_ids"]
            if self.guild.get_role(int(role_id))
        ] + [
            discord.Object(id=user_id, type=discord.User)
            for user_id in CONFIG["whitelist_user_ids"]
            if self.guild.get_member(int(user_id))
        ]
        whitelist_select = ui.MentionableSelect(
            custom_id="cfg_whitelist",
            placeholder="Whitelist users and roles",
            min_values=0,
            max_values=25,
            default_values=whitelist_defaults,
        )
        whitelist_select.callback = self._on_whitelist
        self.whitelist_select = whitelist_select
        container.add_item(ui.ActionRow(whitelist_select))

        channel_defaults = []
        vouch_channel = self.guild.get_channel(int(CONFIG["vouch_channel_id"]))
        if vouch_channel:
            channel_defaults.append(
                discord.Object(id=vouch_channel.id, type=discord.TextChannel)
            )
        channel_select = ui.ChannelSelect(
            custom_id="cfg_vouch_channel",
            placeholder="Vouch channel",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            default_values=channel_defaults,
        )
        channel_select.callback = self._on_vouch_channel
        self.channel_select = channel_select
        container.add_item(ui.ActionRow(channel_select))

        category_select = ui.ChannelSelect(
            custom_id="cfg_ticket_categories",
            placeholder="Ticket categories (.close)",
            channel_types=[discord.ChannelType.category],
            min_values=0,
            max_values=10,
            default_values=[
                discord.Object(id=channel_id, type=discord.CategoryChannel)
                for channel_id in CONFIG["ticket_category_ids"]
                if self.guild.get_channel(int(channel_id))
            ],
        )
        category_select.callback = self._on_ticket_categories
        self.category_select = category_select
        container.add_item(ui.ActionRow(category_select))

        self.add_item(container)

    async def _reject(self, interaction: discord.Interaction) -> bool:
        if is_owner(interaction.user):
            return False
        await interaction.response.send_message(
            "Only server owners can change the configuration.", ephemeral=True
        )
        return True

    async def _commit(self, interaction: discord.Interaction, note: str) -> None:
        try:
            save_config(CONFIG)
        except OSError:
            log.exception("Could not write config.json")
            await interaction.response.send_message(
                "❌ Could not save `config.json`. Check the bot logs.", ephemeral=True
            )
            return
        log.info("Config updated: %s", note)
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_mm_roles(self, interaction: discord.Interaction) -> None:
        if await self._reject(interaction):
            return
        CONFIG["staff_role_ids"] = [role.id for role in self.mm_select.values]
        await self._commit(interaction, "middleman roles")

    async def _on_trader_roles(self, interaction: discord.Interaction) -> None:
        if await self._reject(interaction):
            return
        CONFIG["trader_role_ids"] = [role.id for role in self.trader_select.values]
        await self._commit(interaction, "trader roles")

    async def _on_whitelist(self, interaction: discord.Interaction) -> None:
        if await self._reject(interaction):
            return
        CONFIG["whitelist_role_ids"] = [
            entry.id
            for entry in self.whitelist_select.values
            if isinstance(entry, discord.Role)
        ]
        CONFIG["whitelist_user_ids"] = [
            entry.id
            for entry in self.whitelist_select.values
            if isinstance(entry, discord.User)
        ]
        await self._commit(interaction, "whitelist")

    async def _on_vouch_channel(self, interaction: discord.Interaction) -> None:
        if await self._reject(interaction):
            return
        CONFIG["vouch_channel_id"] = (
            self.channel_select.values[0].id if self.channel_select.values else 0
        )
        await self._commit(interaction, "vouch channel")

    async def _on_ticket_categories(self, interaction: discord.Interaction) -> None:
        if await self._reject(interaction):
            return
        CONFIG["ticket_category_ids"] = [
            channel.id for channel in self.category_select.values
        ]
        await self._commit(interaction, "ticket categories")

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: ui.Item
    ) -> None:
        log.exception("Config panel failed", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Could not save that change.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Could not save that change.", ephemeral=True
                )
        except discord.HTTPException:
            pass
