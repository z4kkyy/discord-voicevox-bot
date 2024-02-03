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
            # "西村博之": 100,
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
        self.voice_client = None
        self.text_input_channel = None
        self.speaker_id = 3
        self.speaker = "ずんだもん（ノーマル）"
        # self.user_to_speaker_id = {}

    def post_audio_query(self, text: str, speaker: int) -> str:
        """
        Posts a query to the VoiceVox API and returns the audio URL.

        :param text: The text to post to the API.
        :param speaker: The speaker ID to use.
        """
        post_url = "https://c6b5-125-12-117-6.ngrok-free.app/audio_query"
        post_data = {
            "text": text,
            "speaker": speaker,
        }
        response = requests.post(post_url, params=post_data)
        return response.json()

    def post_synthesis(self, data: dict, speaker: int) -> bytes:
        """
        Posts a synthesis to the VoiceVox API and returns the audio URL.

        :param data: The data to post to the API.
        :param speaker: The speaker ID to use.
        """
        post_url = "https://c6b5-125-12-117-6.ngrok-free.app/synthesis"
        post_data = {
            "speaker": speaker,
        }
        response = requests.post(post_url, params=post_data, json=data)
        return response.content

    def generate_hiroyuki_audio(self, text: str) -> str:
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

    def save_tempfile(self, text: str, speaker: int) -> str:
        if speaker == 100:  # Hiroyuki
            file_path = self.generate_hiroyuki_audio(text)
            return file_path
        else:
            text_data = self.post_audio_query(text, speaker)
            audio_data = self.post_synthesis(text_data, speaker)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                file_path = f.name
            return file_path

    @commands.Cog.listener()
    async def on_message(self, message) -> None:
        """
        Handles the on_message event.

        :param message: The message sent.
        """
        if message.author.bot:
            return
        if message.channel != self.text_input_channel:
            return
        if message.content.startswith(self.bot.config["prefix"] or "/"):
            return
        if message.content.startswith("https://") or message.content.startswith("http://"):
            return

        speaker_to_use = self.speaker_id
        if message.author.id == 848533749708357666:  # For Briki#2549
            speaker_to_use = 14

        path = self.save_tempfile(message.content, speaker_to_use)  # current implementation
        print(f"[VoiceVox] Input query text: {message.content}")
        self.voice_client.play(discord.FFmpegPCMAudio(path))

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

        self.text_input_channel = context.channel
        self.voice_client = await user.voice.channel.connect()
        latency = self.bot.latency * 1000
        embed = discord.Embed(
            title=f"VoiceVox Bot: {self.speaker}",
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
        if self.voice_client is None:
            await context.reply("You are not connected to a voice channel.")
            return
        await context.reply(f"Leaving {context.channel.mention} 👋")
        await self.voice_client.disconnect()
        self.text_input_channel = None

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
        self.speaker_id = view.selected_speaker_id
        self.speaker = view.selected_speaker
        # print("speaker_id: ", self.speaker_id)

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
            title=f"VoiceVox Bot: {self.speaker}",
            description=(f"Joined {user.voice.channel.mention} (Ping: {latency:.0f}ms)"),
            color=0x00FF00,
        )
        await context.reply(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(VoiceVox(bot))
