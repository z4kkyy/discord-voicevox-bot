""""
Copyright © Krypton 2019-2023 - https://github.com/kkrypt0nn (https://krypton.ninja)

Version: 6.1.0

Modified by z4kky - https://github.com/z4kkyy
"""

import asyncio
import json
import os
import re
import tempfile
# import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import discord
import requests
from discord.ext import commands
# from discord.ext import tasks
from discord.ext.commands import Context
from dotenv import load_dotenv
from voicevox_core import VoicevoxCore

# from selenium.webdriver.common.by import By
# from selenium.webdriver.common.keys import Keys


class SelectSpeakerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__()
        self.selected_speaker_id = 3
        self.selected_speaker = "ずんだもん（ノーマル）"
        self.speaker_dict = {
            "ずんだもん（あまあま）": 1,
            "ずんだもん（ノーマル）": 3,
            "四国めたん（あまあま）": 0,
            "四国めたん（ノーマル）": 2,
            "春日部つむぎ（ノーマル）": 8,
            "雨晴はう（ノーマル）": 10,
            "冥鳴ひまり（ノーマル）": 14,
            "青山龍星（ノーマル）": 13,
            "青山龍星（囁き）": 86,
            "青山龍星（しっとり）": 84,
        }
        self.waiter = asyncio.Event()

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Select a speaker!",
        min_values=1,
        max_values=1,
        disabled=False
    )
    async def select_speaker(
        self, interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        self.selected_speaker = select.values[0]
        self.selected_speaker_id = self.speaker_dict[self.selected_speaker]
        select.disabled = True
        await interaction.response.edit_message(view=self)
        embed = discord.Embed(
            title="VoiceVox Bot",
            description=(f"Successfully set to {self.selected_speaker}!"),
            color=0x00FF00,
        )
        await interaction.followup.send(embed=embed)
        self.waiter.set()


class VoiceVox(commands.Cog, name="voicevox"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.server_to_voice_client = defaultdict(lambda: None)
        self.server_to_if_connected = defaultdict(lambda: None)
        self.server_to_text_input_channel = defaultdict(lambda: None)
        self.server_to_speaker_id = defaultdict(lambda: 3)
        self.server_to_speaker = defaultdict(lambda: "ずんだもん（ノーマル）")
        self.server_to_user_channel = defaultdict(lambda: None)
        self.server_to_audio_queue = defaultdict(asyncio.Queue)
        self.server_to_if_playing = defaultdict(lambda: False)

        self.server_to_expected_disconnection = defaultdict(lambda: False)  # for unexpected disconnection
        self.POST_URL = os.getenv("NGROK_URL")

        self.JTALK_DICT_DIR = os.path.realpath(os.path.expanduser("~/open_jtalk_dic_utf_8-1.11"))
        self.voicevox_core = VoicevoxCore(open_jtalk_dict_dir=self.JTALK_DICT_DIR)
        self.voicevox_core.load_model(3)

    def _post_audio_query(self, text: str, speaker: int) -> str:
        """
        Posts a query to the VoiceVox API and returns the audio URL.

        :param text: The text to post to the API.
        :param speaker: The speaker ID to use.
        """
        post_url = self.POST_URL + "/audio_query"
        post_data = {
            "text": text,
            "speaker": speaker,
        }
        response = requests.post(post_url, params=post_data)
        return response.json()

    def _post_synthesis(self, data: dict, speaker: int) -> bytes:
        """
        Posts a synthesis to the VoiceVox API and returns the audio URL.

        :param data: The data to post to the API.
        :param speaker: The speaker ID to use.
        """
        post_url = self.POST_URL + "/synthesis"
        post_data = {
            "speaker": speaker,
        }
        response = requests.post(post_url, params=post_data, json=data)
        return response.content

    def _generate_audio_file(self, text: str, speaker: int) -> str:
        """
        Generates an audio file using the specified speaker.

        :param text: The text to generate the audio file from.
        :param speaker: The speaker ID to use.
        """
        # if speaker == 100:  # hiro
        #     file_path = self._generate_hiro_audio(text)
        #     return file_path
        # else:

        # print(f"Generating audio file for '{text}' with speaker ID {speaker}.")

        if not self.voicevox_core.is_model_loaded(speaker):  # NOTE: use VoiceVoxCore instead of the API (24.5.7~)
            self.voicevox_core.load_model(speaker)
        audio_data = self.voicevox_core.tts(text, speaker)

        # # past implementation
        # text_data = self._post_audio_query(text, speaker)
        # audio_data = self._post_synthesis(text_data, speaker)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            file_path = f.name
        return file_path

    def _create_after_callback(self, guild_id, previous_path) -> callable:
        # create recursive callback function
        def after_callback(error):
            async def play_next():
                if error:
                    print(f"Error: {error}")
                self.server_to_if_playing[guild_id] = False

                # remove the previous audio file

                if not re.search(r'[^/]+$', previous_path).group().startswith("CUSTOMSTICKER"):
                    os.remove(previous_path)

                if not self.server_to_audio_queue[guild_id].empty():
                    next_audio_path = await self.server_to_audio_queue[guild_id].get()
                    self.server_to_if_playing[guild_id] = True
                    source = discord.FFmpegPCMAudio(next_audio_path)
                    voice_client = self.server_to_voice_client[guild_id]
                    voice_client.play(source, after=self._create_after_callback(guild_id, next_audio_path))  # recursive call
                else:
                    pass

            # schedule the coroutine to be run on the event loop
            asyncio.run_coroutine_threadsafe(play_next(), self.bot.loop)

        return after_callback

    async def _add_to_queue(self, path: str, guild_id: str) -> None:
        """
        Adds an audio file to the queue.

        :param path: The path of the audio file to add.
        :param guild_id: The ID of the guild to add the audio file to.
        """
        voice_client = self.server_to_voice_client[guild_id]
        if voice_client is None:
            return
        if self.server_to_if_playing[guild_id]:
            await self.server_to_audio_queue[guild_id].put(path)
        else:
            self.server_to_if_playing[guild_id] = True
            voice_client.play(discord.FFmpegPCMAudio(path), after=self._create_after_callback(guild_id, path))

    @commands.Cog.listener()
    async def on_message(self, message) -> None:
        """
        Handles the on_message event.

        :param message: The message sent.
        """
        if message.author.bot:
            return
        if message.channel != self.server_to_text_input_channel[message.guild.id]:
            return
        if message.content.startswith(self.bot.config["prefix"]):
            return
        if message.content.startswith("/"):
            return
        if "https://" in message.content or "http://" in message.content:
            return
        if message.content.startswith("*ig"):
            return
        if message.content.startswith("```"):
            return

        message_content = message.content
        guild_id = message.guild.id
        speaker_to_use = self.server_to_speaker_id[message.guild.id]

        json_path = str(Path(__file__).resolve().parent.parent) + "/custom_setting.json"

        # load custom settings
        with open(json_path, "r") as file:
            custom_setting = json.load(file)

        #  get custom setting dict. key: emoji_id, sticker_id, author_id
        custom_emoji = {int(key): val for key, val in custom_setting.get("custom_emoji").items()}
        custom_sticker = {int(key): val for key, val in custom_setting.get("custom_sticker").items()}
        custom_speaker = {int(key): val for key, val in custom_setting.get("custom_speaker").items()}

        # check if the message author has a specific speaker setting
        if message.author.id in custom_speaker.keys():
            speaker_to_use = custom_speaker[message.author.id]["speaker_id"]  # int

        # check if the message has stickers and if they are the specific ones
        if len(message.stickers) > 0:
            sticker_id = message.stickers[0].id
            if sticker_id in custom_sticker.keys():  # at most 1 sticker for each message
                if custom_sticker[sticker_id]["filename"] is None:
                    content = custom_sticker[sticker_id]["content"]
                    path = self._generate_audio_file(content, speaker_to_use)
                else:
                    filename = custom_sticker[sticker_id]["filename"]
                    path = str(Path(__file__).resolve().parent.parent) + "/audio/" + filename

                await self._add_to_queue(path=path, guild_id=message.guild.id)
                return
            else:
                return

        # check if the message has emojis and if they are the specific ones
        contained_emoji = re.findall(r'<:\w+:\d+>', message.content)
        if len(contained_emoji) > 0:
            for emoji in contained_emoji:
                emoji_id = int(re.findall(r'\d+', emoji)[0])
                if emoji_id in custom_emoji.keys():
                    if custom_emoji[emoji_id]["filename"] is None:
                        content = custom_emoji[emoji_id]["content"]
                        path = self._generate_audio_file(content, speaker_to_use)
                    else:
                        filename = custom_sticker[sticker_id]["filename"]
                        path = str(Path(__file__).resolve().parent.parent) + "/audio/" + filename

                    await self._add_to_queue(path=path, guild_id=message.guild.id)
            return

        print(f'new message: {message_content!r}')

        # replace "w" with "わら"
        message_content = message_content.replace("w", "わら")
        message_content = message_content.replace("ｗ", "わら")
        # replace successive "笑" in the end with "わら"
        message_content = re.sub(r'笑+$', lambda m: 'わら' * len(m.group()), message_content)
        # remove user mentions
        message_content = re.sub(r"<@\d+>", "", message_content)

        # generate audio file
        path = self._generate_audio_file(message_content, speaker_to_use)
        print(f'successfully generated. path: {path}')

        try:
            await self._add_to_queue(path=path, guild_id=guild_id)
        except Exception as e:
            print(f"Error during message handling: {e}")

        # show logs
        self.bot.logger.info(f"[VoiceVox] Input query text: '{message.content}' by {message.author}")
        query_hist_path = str(Path(__file__).resolve().parent.parent) + "/query_hist.txt"
        with open(query_hist_path, 'a') as file:
            file.write(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] discord_bot: VOICEVOX query: '{message.content}' by {message.author} (ID: {message.author.id}) \n"
            )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after) -> None:
        """
        Handles the on_voice_state_update event.
        This function is used to detect unexpected disconnection of the bot from a voice channel.

        :param member: The member who updated their voice state.
        :param before: The voice state before the update.
        :param after: The voice state after the update.
        """
        if member.id == self.bot.user.id:
            guild_id = member.guild.id
            if before.channel is not None and after.channel is None:
                if self.server_to_expected_disconnection[guild_id]:
                    print("Detected normal disconnection.")
                    self.server_to_expected_disconnection[guild_id] = False
                else:
                    print("Detected unexpected disconnection. Check the close code.")
                    # reconnect to the voice channel
                    self.server_to_voice_client[guild_id] = await before.channel.connect()

    @commands.hybrid_command(
        name="join",
        description="Joins a voice channel.",
    )
    async def join(self, context: Context) -> None:
        """
        Joins a voice channel.

        :param context: The application command context.
        """
        user = context.author
        if_send_embed = True

        if user.voice is None:
            embed = discord.Embed(
                description="You are not connected to a voice channel.", color=0xE02B2B
            )
            await context.reply(embed=embed)
            return
        if self.server_to_voice_client[context.guild.id] is not None:
            if self.server_to_voice_client[context.guild.id].is_connected() is True:
                self. server_to_expected_disconnection[context.guild.id] = True
                await self.server_to_voice_client[context.guild.id].disconnect()
                self.server_to_voice_client[context.guild.id] = None
                if_send_embed = False

        # set the server settings
        self.server_to_text_input_channel[context.guild.id] = context.channel
        self.server_to_voice_client[context.guild.id] = await user.voice.channel.connect()
        self.server_to_if_connected[context.guild.id] = True
        self.server_to_user_channel[context.guild.id] = user.voice.channel

        # send logs to the text channel
        latency = self.bot.latency * 1000
        if if_send_embed:
            embed = discord.Embed(
                title=f"VoiceVox Bot: {self.server_to_speaker[context.guild.id]}",
                description=(f"Joined {user.voice.channel.mention} (Ping: {latency:.0f}ms)"),
                color=0x00FF00,
            )
            await context.reply(embed=embed)
        else:
            embed = discord.Embed(
                description="Reconnected to the voice channel.", color=0xE02B2B
            )
            await context.send(embed=embed)

    @commands.hybrid_command(
        name="jointhis",
        description="Joins the specified voice channel.",
    )
    async def jointhis(self, context: Context, channel_id: str) -> None:
        """
        Joins the specified voice channel.

        :param context: The application command context.
        :param channel_id: The ID of the voice channel to join.
        """
        channel = discord.utils.get(context.guild.voice_channels, id=int(channel_id))
        if_send_embed = True

        if channel is None:
            embed = discord.Embed(
                description=f"Voice channel with ID {channel_id} not found.", color=0xE02B2B
            )
            await context.reply(embed=embed)
            return

        if self.server_to_voice_client[context.guild.id] is not None:
            if self.server_to_voice_client[context.guild.id].is_connected() is True:
                self. server_to_expected_disconnection[context.guild.id] = True
                await self.server_to_voice_client[context.guild.id].disconnect()
                self.server_to_voice_client[context.guild.id] = None
                if_send_embed = False

        # set the server settings
        self.server_to_text_input_channel[context.guild.id] = context.channel
        self.server_to_voice_client[context.guild.id] = await channel.connect()
        self.server_to_if_connected[context.guild.id] = True
        self.server_to_user_channel[context.guild.id] = channel

        # send logs to the text channel
        latency = self.bot.latency * 1000
        if if_send_embed:
            embed = discord.Embed(
                title=f"VoiceVox Bot: {self.server_to_speaker[context.guild.id]}",
                description=(f"Joined {channel.mention} (Ping: {latency:.0f}ms)"),
                color=0x00FF00,
            )
            await context.reply(embed=embed)
        else:
            embed = discord.Embed(
                description="Reconnected to the voice channel.", color=0xE02B2B
            )
            await context.send(embed=embed)

    @commands.hybrid_command(
        name="leave",
        description="Leaves a voice channel.",
    )
    async def leave(self, context: Context) -> None:
        """
        Leaves a voice channel.

        :param context: The application command context.
        """
        voice_client = self.server_to_voice_client[context.guild.id]
        if voice_client is None:
            embed = discord.Embed(
                description="VoiceVox Bot is not connected to a voice channel.", color=0xE02B2B
            )
            await context.reply(embed=embed)
            return
        else:
            embed = discord.Embed(
                description=f"Leaving {voice_client.channel.mention} 👋", color=0xE02B2B
            )
            await context.send(embed=embed)
            self. server_to_expected_disconnection[context.guild.id] = True
            await voice_client.disconnect()

            # reset the server settings
            self.server_to_voice_client[context.guild.id] = None
            self.server_to_if_connected[context.guild.id] = False
            self.server_to_text_input_channel[context.guild.id] = None
            self.server_to_user_channel[context.guild.id] = None
            self.server_to_audio_queue[context.guild.id] = asyncio.Queue()
            self.server_to_if_playing[context.guild.id] = False

    @commands.hybrid_command(
        name="hardreset",
        description="Reset all the variables",
    )
    async def hardreset(self, context: Context) -> None:
        """
        Hard reset.

        :param context: The application command context.
        """
        embed = discord.Embed(
            description="Successfully reset.", color=0xE02B2B
        )
        await context.send(embed=embed)

        guild_id = context.guild.id
        voice_client = self.server_to_voice_client[guild_id]
        if voice_client is not None:
            await voice_client.disconnect()
        self.server_to_voice_client[guild_id] = None
        self.server_to_if_connected[guild_id] = False
        self.server_to_text_input_channel[guild_id] = None
        self.server_to_user_channel[guild_id] = None
        self.server_to_audio_queue[guild_id] = asyncio.Queue()
        self.server_to_if_playing[guild_id] = False

    @commands.hybrid_command(
        name="change",
        description="Changes the speaker.",
    )
    async def change(self, context: Context) -> None:
        """
        Changes the speaker.

        :param context: The application command context.
        """
        view = SelectSpeakerView()
        for speaker in view.speaker_dict.keys():
            view.select_speaker.add_option(
                label=speaker,
                value=speaker,
            )
        await context.send(view=view)
        await view.waiter.wait()
        self.server_to_speaker_id[context.guild.id] = view.selected_speaker_id
        self.server_to_speaker[context.guild.id] = view.selected_speaker

    @commands.hybrid_command(
        name="speaker",
        description="Returns the current speaker.",
    )
    async def speaker(self, context: Context) -> None:
        """
        Returns the current speaker.

        :param context: The application command context.
        """
        user = context.author
        latency = self.bot.latency * 1000
        embed = discord.Embed(
            title=f"VoiceVox Bot: {self.server_to_speaker[context.guild.id]}",
            description=(f"Joined {user.voice.channel.mention} (Ping: {latency:.0f}ms)"),
            color=0x00FF00,
        )
        await context.reply(embed=embed)


load_dotenv()


async def setup(bot) -> None:
    await bot.add_cog(VoiceVox(bot))
