import os
import asyncio
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from helpers.constants import MAINTEMPLATE, BOTNAME

class Chatbot:
    def __init__(self, bot):
        self.bot = bot
        self.histories = {}
        self.char_name = BOTNAME
        self.api_key = bot.openai

    async def get_history(self, channel_id):
        if channel_id not in self.histories:
            self.histories[channel_id] = []
        return self.histories[channel_id]

    async def generate_response(self, message, message_content):
        channel_id = str(message.channel.id)
        name = message.author.display_name
        history = await self.get_history(channel_id)

        system_prompt = MAINTEMPLATE.replace("{history}", "").replace("{input}", "").strip()

        history.append({"role": "user", "content": f"{name}: {message_content}"})

        if len(history) > 20:
            history = history[-20:]
            self.histories[channel_id] = history

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": history
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload
            ) as resp:
                data = await resp.json()
                response_text = data["content"][0]["text"]

        history.append({"role": "assistant", "content": response_text})
        return response_text


class ChatbotCog(commands.Cog, name="chatbot"):
    def __init__(self, bot):
        self.bot = bot
        self.chatbot = Chatbot(bot)

    @commands.command(name="chat")
    async def chat_command(self, message, message_content):
        response = await self.chatbot.generate_response(message, message_content)
        return response


async def setup(bot):
    await bot.add_cog(ChatbotCog(bot))
