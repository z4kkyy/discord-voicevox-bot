""""
Copyright © Krypton 2019-2023 - https://github.com/kkrypt0nn (https://krypton.ninja)

Version: 6.1.0

Modified by Y.Ozaki - https://github.com/mttk1528
"""

import os
import tempfile
import requests
import asyncio
import time
from datetime import datetime, timedelta
from collections import defaultdict

import discord
from discord.ext import commands
from discord.ext.commands import Context

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


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
            "西村博之": 100,
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
        # print("selected_speaker_id: ", self.selected_speaker_id, "selected_speaker: ", self.selected_speaker)
        self.waiter.set()


class VoiceVox(commands.Cog, name="voicevox"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.server_to_voice_client = defaultdict(lambda: None)
        self.server_to_text_input_channel = defaultdict(lambda: None)
        self.server_to_speaker_id = defaultdict(lambda: 3)
        self.server_to_speaker = defaultdict(lambda: "ずんだもん（ノーマル）")
        self.server_to_user_channel = defaultdict(lambda: None)
        self.server_to_audio_queue = defaultdict(asyncio.Queue)
        self.server_to_if_playing = defaultdict(lambda: False)

    def _post_audio_query(self, text: str, speaker: int) -> str:
        """
        Posts a query to the VoiceVox API and returns the audio URL.

        :param text: The text to post to the API.
        :param speaker: The speaker ID to use.
        """
        post_url = "https://4dde-125-12-117-6.ngrok-free.app/audio_query"
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
        post_url = "https://4dde-125-12-117-6.ngrok-free.app/synthesis"
        post_data = {
            "speaker": speaker,
        }
        response = requests.post(post_url, params=post_data, json=data)
        return response.content

    def _generate_hiroyuki_audio(self, text: str) -> str:
        """
        Generates an audio file using the Hiroyuki speaker.

        :param text: The text to generate the audio file from.
        """
        driver = self.bot.driver
        download_dir = self.bot.download_dir
        for filename in os.listdir(download_dir):
            file_path = os.path.join(download_dir, filename)
            os.remove(file_path)
        text_input = driver.find_element(By.XPATH, "/html/body/div/div[1]/div/div[1]/div[4]/div/div[1]/div")

        text_input.send_keys(Keys.CONTROL + "a")
        text_input.send_keys(Keys.DELETE)
        text_input.send_keys(text)
        # time.sleep(1)

        self.bot.logger.info("[HIROYUKI] Starting audio download.")
        download_button = driver.find_element(By.XPATH, "/html/body/div/div[1]/div/div[1]/div[4]/div/div[2]/button[2]")
        download_button.click()
        while len(os.listdir(download_dir)) == 0:
            time.sleep(1)
        self.bot.logger.info("[HIROYUKI] Download finished.")
        driver.save_screenshot(f"img/{str(datetime.now())}.png")  # Save a screenshot for debug
        start_time = datetime.now()
        while (datetime.now() - start_time) < timedelta(seconds=10):
            if len(os.listdir(download_dir)) > 0:
                file_name = os.listdir(download_dir)[0]
                os.rename(f"{download_dir}/{file_name}", f"{download_dir}/audio.wav")
                break
            else:
                time.sleep(1)
                continue
        return f"{download_dir}/audio.wav"

    def _save_tempfile(self, text: str, speaker: int) -> str:
        if speaker == 100:  # Hiroyuki
            file_path = self._generate_hiroyuki_audio(text)
            return file_path
        else:
            text_data = self._post_audio_query(text, speaker)
            audio_data = self._post_synthesis(text_data, speaker)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                file_path = f.name
            return file_path

    def create_after_callback(self, guild_id) -> None:
        # create recursive callback function
        def after_callback(error):
            async def play_next():
                if error:
                    print(f"Error: {error}")
                self.server_to_if_playing[guild_id] = False

                if not self.server_to_audio_queue[guild_id].empty():
                    next_audio_path = await self.server_to_audio_queue[guild_id].get()
                    self.server_to_if_playing[guild_id] = True
                    source = discord.FFmpegPCMAudio(next_audio_path)
                    voice_client = self.server_to_voice_client[guild_id]
                    voice_client.play(source, after=self.create_after_callback(guild_id))
                else:
                    pass

            # schedule the coroutine to be run on the event loop
            asyncio.run_coroutine_threadsafe(play_next(), self.bot.loop)

        return after_callback

    async def add_to_queue(self, path: str, guild_id: str) -> None:
        voice_client = self.server_to_voice_client[guild_id]
        if voice_client is None:
            return
        if self.server_to_if_playing[guild_id]:
            await self.server_to_audio_queue[guild_id].put(path)
        else:
            self.server_to_if_playing[guild_id] = True
            voice_client.play(discord.FFmpegPCMAudio(path), after=self.create_after_callback(guild_id))

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
        if message.content.startswith("https://") or message.content.startswith("http://"):
            return

        speaker_to_use = self.server_to_speaker_id[message.guild.id]

        if message.author.id == 848533749708357666:  # For Briki#2549
            speaker_to_use = 14

        guild_id = message.guild.id
        path = self._save_tempfile(message.content, speaker_to_use) 
        await self.add_to_queue(path=path, guild_id=guild_id)
        print(f"[VoiceVox] Input query text: {message.content}")

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
        if user.voice is None:
            await context.reply("You are not connected to a voice channel.")
            return
        if self.server_to_voice_client[context.guild.id] is not None:
            await context.reply("VoiceVox Bot is already connected to a voice channel.")
            return

        self.server_to_text_input_channel[context.guild.id] = context.channel
        self.server_to_voice_client[context.guild.id] = await user.voice.channel.connect()
        self.server_to_user_channel[context.guild.id] = user.voice.channel
        latency = self.bot.latency * 1000
        embed = discord.Embed(
            title=f"VoiceVox Bot: {self.server_to_speaker[context.guild.id]}",
            description=(f"Joined {user.voice.channel.mention} (Ping: {latency:.0f}ms)"),
            color=0x00FF00,
        )
        await context.reply(embed=embed)

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
            await context.reply("VoiceVox Bot is not connected to a voice channel.")
            return
        else:
            await context.reply(f"Leaving {voice_client.channel.mention} 👋")
            await voice_client.disconnect()
            self.server_to_text_input_channel[context.guild.id] = None

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


async def setup(bot) -> None:
    await bot.add_cog(VoiceVox(bot))
