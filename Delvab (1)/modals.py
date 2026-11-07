from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import ui

if TYPE_CHECKING:
    from bot import VouchBot

log = logging.getLogger("vouchbot.modals")


class VouchModal(ui.Modal):
    def __init__(
        self,
        bot: "VouchBot",
        *,
        mode: str = "now",
        prefill_trader: str = "",
        prefill_trade: str = "",
        channel: discord.TextChannel | None = None,
    ):
        titles = {
            "now": "New Vouch",
            "close": "Close Ticket · Vouch",
        }
        super().__init__(title=titles.get(mode, "New Vouch"), timeout=600)
        self.bot = bot
        self.mode = mode
        self.ticket_channel = channel

        self.trader = ui.TextInput(
            custom_id="trader",
            placeholder="@user, user ID, or username",
            default=prefill_trader or None,
            max_length=100,
        )
        self.add_item(
            ui.Label(
                text="Trader",
                description="Mention, user ID, or username",
                component=self.trader,
            )
        )

        self.trade = ui.TextInput(
            custom_id="trade",
            placeholder="In-Game Items ⇄ GCash",
            default=prefill_trade or None,
            max_length=80,
        )
        self.add_item(
            ui.Label(
                text="Trade type",
                description="What was traded",
                component=self.trade,
            )
        )

        self.rating = ui.TextInput(
            custom_id="rating",
            placeholder="1-5",
            default="5",
            max_length=5,
        )
        self.add_item(
            ui.Label(
                text="Rating (1-5)",
                description="Trader's rating for this trade",
                component=self.rating,
            )
        )

        self.review = ui.TextInput(
            custom_id="review",
            style=discord.TextStyle.paragraph,
            placeholder="Very patient and professional middleman throughout.",
            max_length=400,
        )
        self.add_item(
            ui.Label(
                text="Review",
                description="Short review shown on the vouch card",
                component=self.review,
            )
        )

        self.proof = ui.FileUpload(
            custom_id="proof",
            required=False,
            max_values=5,
        )
        self.add_item(
            ui.Label(
                text="Proof screenshots",
                description="Up to 5 images or clips (optional)",
                component=self.proof,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_modal_submit(interaction, self)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("Modal submit failed", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Something went wrong. Check the bot logs.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Something went wrong. Check the bot logs.", ephemeral=True
                )
        except discord.HTTPException:
            pass


class VouchPromptView(ui.View):
    def __init__(
        self,
        bot: "VouchBot",
        *,
        mode: str = "now",
        prefill_trader: str = "",
        prefill_trade: str = "",
        channel: discord.TextChannel | None = None,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.mode = mode
        self.prefill_trader = prefill_trader
        self.prefill_trade = prefill_trade
        self.ticket_channel = channel
        self.message: discord.Message | None = None

    @ui.button(
        label="Open vouch form",
        emoji="📝",
        style=discord.ButtonStyle.primary,
    )
    async def open_form(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        await interaction.response.send_modal(
            VouchModal(
                self.bot,
                mode=self.mode,
                prefill_trader=self.prefill_trader,
                prefill_trade=self.prefill_trade,
                channel=self.ticket_channel,
            )
        )
        self.stop()
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
