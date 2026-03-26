import os
import aiohttp
import discord
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import commands
from helpers.constants import MAINTEMPLATE, BOTNAME
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Discord message character limit
DISCORD_MAX_LENGTH = 2000


def split_message(text, max_length=DISCORD_MAX_LENGTH):
    """Split a message into chunks that fit within Discord's character limit.
    Prefers splitting at paragraph breaks, then newlines, then sentence boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_length:
        # Find the best split point within the limit
        split_at = max_length

        # Try paragraph break (double newline)
        para_break = remaining.rfind("\n\n", 0, max_length)
        if para_break > max_length * 0.3:  # Don't split too early
            split_at = para_break + 2  # Include the double newline

        # Try single newline
        elif (nl := remaining.rfind("\n", 0, max_length)) > max_length * 0.3:
            split_at = nl + 1

        # Try sentence boundary (. ! ?)
        elif (sent := max(
            remaining.rfind(". ", 0, max_length),
            remaining.rfind("! ", 0, max_length),
            remaining.rfind("? ", 0, max_length),
        )) > max_length * 0.3:
            split_at = sent + 2

        # Try space as last resort
        elif (space := remaining.rfind(" ", 0, max_length)) > max_length * 0.3:
            split_at = space + 1

        # Hard split at limit if nothing else works
        chunk = remaining[:split_at].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks


def format_timestamp(iso_string):
    """Convert ISO timestamp to a readable short format for context."""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt

        if diff.days == 0:
            return dt.strftime("%H:%M")
        elif diff.days == 1:
            return f"yesterday {dt.strftime('%H:%M')}"
        elif diff.days < 7:
            return dt.strftime("%A %H:%M")
        else:
            return dt.strftime("%d %b %H:%M")
    except Exception:
        return ""


class Chatbot:
    def __init__(self, bot):
        self.bot = bot
        self.char_name = BOTNAME
        self.api_key = bot.openai

    async def get_history(self, channel_id):
        try:
            # Fetch the 20 most recent messages (newest first) with timestamps
            result = supabase.table("conversation_history") \
                .select("role, content, created_at") \
                .eq("channel_id", channel_id) \
                .order("created_at", desc=True) \
                .limit(20) \
                .execute()

            # Reverse to chronological order for the API (oldest first)
            rows = list(reversed(result.data))

            # Build messages with timestamp context
            messages = []
            last_timestamp = None
            for r in rows:
                content = r["content"]
                ts = r.get("created_at", "")

                # Add a time marker when there's a significant gap (>2 hours)
                if ts and last_timestamp:
                    try:
                        current = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        previous = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
                        gap_hours = (current - previous).total_seconds() / 3600
                        if gap_hours > 2:
                            time_label = format_timestamp(ts)
                            if time_label:
                                content = f"[{time_label}] {content}"
                    except Exception:
                        pass

                messages.append({"role": r["role"], "content": content})
                last_timestamp = ts

            return messages
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

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload
                ) as resp:
                    if resp.status != 200:
                        error_data = await resp.text()
                        print(f"Anthropic API error {resp.status}: {error_data}")
                        return f"*[Connection hiccup — API returned {resp.status}. I'm still here, just temporarily muted.]*"

                    data = await resp.json()

                    # Validate response structure
                    if "content" not in data or not data["content"]:
                        print(f"Unexpected API response structure: {data}")
                        return "*[Something came back wrong from the API. No content in response. Try again?]*"

                    response_text = data["content"][0]["text"]

        except aiohttp.ClientError as e:
            print(f"Network error calling Anthropic API: {e}")
            return "*[Lost connection to the API. Network issue. I'll be back when it resolves.]*"
        except Exception as e:
            print(f"Unexpected error in generate_response: {e}")
            return f"*[Something broke unexpectedly: {type(e).__name__}. Check the logs.]*"

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

    async def chat_command_nr(self, author_name, channel_id, message_content):
        """Save a message to history without generating a response (listen-only mode)."""
        user_message = f"{author_name}: {message_content}"
        await self.chatbot.save_message(str(channel_id), "user", user_message)


async def setup(bot):
    await bot.add_cog(ChatbotCog(bot))
