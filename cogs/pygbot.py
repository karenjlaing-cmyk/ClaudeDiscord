import os
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from helpers.constants import MAINTEMPLATE, BOTNAME
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

class Chatbot:
    def __init__(self, bot):
        self.bot = bot
        self.char_name = BOTNAME
        self.api_key = bot.openai

    async def get_history(self, channel_id):
        try:
            result = supabase.table("conversation_history") \
                .select("role, content") \
                .eq("channel_id", channel_id) \
                .order("created_at", desc=False) \
                .limit(20) \
                .execute()
            return [{"role": r["role"], "content": r["content"]} for r in result.data]
        except Exception as e:
            print(f"Error fetching history: {e}")
            return []

    async def save_message(self, channel_id, role, content):
        try:
            supabase.table("conversation_history").insert({
                "channel_id": channel_id,
                "role": role,
                "content": content
            }).execute()
        except Exception as e:
            print(f"Error saving message: {e}")

    async def generate_response(self, message, message_content):
        channel_id = str(message.channel.id)
        name = message.author.display_name
        history = await self.get_history(channel_id)

        system_prompt = MAINTEMPLATE.replace("{history}", "").replace("{input}", "").strip()

        user_message = f"{name}: {message_content}"
        await self.save_message(channel_id, "user", user_message)
        history.append({"role": "user", "content": user_message})

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

        await self.save_message(channel_id, "assistant", response_text)
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
