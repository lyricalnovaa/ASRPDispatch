import os
import asyncio
import discord
from discord.ext import commands
import speech_recognition as sr
import edge_tts
from pydub import AudioSegment
from dotenv import load_dotenv

# === ENV & CONFIG ===
load_dotenv()
TOKEN = os.getenv("TOKEN")
CONFIG = {
    "RTO_CHANNEL_ID": 1341573057952878674,  # Replace with your voice channel ID
    "VOICE": "en-US-GuyNeural",
    "OUTPUT_TTS_FILE": "dispatch_tts.mp3"
}

# === BOT SETUP ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# === GLOBAL STATE ===
voice_client_ref = None
listening_task = None
recognizer = sr.Recognizer()
audio_queue = asyncio.Queue()
is_listening = False
unit_status = {}  # callsign -> status (10-8, 10-7, etc.)

# === TTS HANDLER ===
async def speak(text: str):
    global voice_client_ref
    if not voice_client_ref or not voice_client_ref.is_connected():
        print("[ERROR] Not connected to a voice channel.")
        return

    tts = edge_tts.Communicate(text, CONFIG["VOICE"])
    temp_file = CONFIG["OUTPUT_TTS_FILE"]
    await tts.save(temp_file)

    if voice_client_ref.is_playing():
        voice_client_ref.stop()

    source = discord.FFmpegPCMAudio(temp_file)
    voice_client_ref.play(source)

    while voice_client_ref.is_playing():
        await asyncio.sleep(0.1)

    try:
        os.remove(temp_file)
    except OSError as e:
        print(f"Error removing file: {e}")

# === STT HANDLER ===
async def process_audio_queue():
    global audio_queue, is_listening
    while is_listening:
        try:
            user, pcm_data = await asyncio.wait_for(audio_queue.get(), timeout=10)
            if pcm_data is None:
                continue

            # Convert Discord PCM audio to WAV for SpeechRecognition
            audio_segment = AudioSegment(
                pcm_data,
                frame_rate=48000,
                sample_width=2,
                channels=2
            ).set_frame_rate(16000).set_channels(1)
            temp_wav_file = f"temp_audio_{user.id}.wav"
            audio_segment.export(temp_wav_file, format="wav")

            with sr.AudioFile(temp_wav_file) as source:
                audio_data = recognizer.record(source)

            try:
                text = recognizer.recognize_google(audio_data).upper()
                callsign = user.display_name[:5]  # first 5 characters

                # Handle unit status updates
                if "10-8" in text:
                    unit_status[callsign] = "10-8"
                    await speak(f"{callsign} is now 10-8, available for calls.")
                elif "10-7" in text:
                    unit_status[callsign] = "10-7"
                    await speak(f"{callsign} is now 10-7, out of service.")
                elif "10-6" in text:
                    unit_status[callsign] = "10-6"
                    await speak(f"{callsign} is now 10-6, busy.")
                elif "DISPATCH" in text and "NEED UNIT" in text:
                    # Dispatch the first available unit
                    available_units = [c for c, s in unit_status.items() if s == "10-8"]
                    if available_units:
                        assigned = available_units[0]
                        unit_status[assigned] = "10-6"
                        await speak(f"Dispatching {assigned} to the call.")
                    else:
                        await speak("No units are currently available.")

            except sr.UnknownValueError:
                print(f"[RECOGNITION] Could not understand {user.display_name}")
            except sr.RequestError as e:
                print(f"[RECOGNITION ERROR] Google Speech Recognition failed: {e}")

            # Clean up temp file
            try:
                os.remove(temp_wav_file)
            except OSError as e:
                print(f"Error removing file: {e}")

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"[PROCESS ERROR] {e}")

def on_audio_receive(user, pcm_data):
    if is_listening:
        asyncio.run_coroutine_threadsafe(audio_queue.put((user, pcm_data)), bot.loop)

# === BOT EVENTS ===
@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user}")

@bot.event
async def on_voice_state_update(member, before, after):
    global voice_client_ref, listening_task, is_listening
    if member == bot.user and before.channel and not after.channel:
        # Bot disconnected, clean up
        print("[DISCONNECT] Bot was disconnected from VC. Cleaning up.")
        if listening_task:
            listening_task.cancel()
            await asyncio.gather(listening_task, return_exceptions=True)
            listening_task = None
        is_listening = False
        voice_client_ref = None

# === COMMANDS ===
@bot.command(name="start")
async def start_dispatcher(ctx):
    global voice_client_ref, listening_task, is_listening
    channel = bot.get_channel(CONFIG["RTO_CHANNEL_ID"])

    if not isinstance(channel, discord.VoiceChannel):
        await ctx.send("❌ Invalid RTO channel ID.")
        return

    if is_listening:
        await ctx.send("📻 Dispatcher is already live.")
        return

    try:
        voice_client_ref = await channel.connect()
        voice_client_ref.listen(on_audio_receive)
        is_listening = True
        listening_task = bot.loop.create_task(process_audio_queue())
        bot_callsign = bot.user.display_name[:5]
        await speak(f"{bot_callsign} 10-8")
        await ctx.send(f"✅ Dispatcher is now online as {bot_callsign}!")
    except Exception as e:
        await ctx.send(f"❌ Could not connect to VC: {e}")
        print(f"[ERROR] {e}")

@bot.command(name="stop")
async def stop_dispatcher(ctx):
    global voice_client_ref, listening_task, is_listening
    if voice_client_ref and voice_client_ref.is_connected():
        await voice_client_ref.disconnect()
        voice_client_ref = None
        if listening_task:
            listening_task.cancel()
            await asyncio.gather(listening_task, return_exceptions=True)
            listening_task = None
        is_listening = False
        await ctx.send("✅ Dispatcher has been shut down.")
    else:
        await ctx.send("❌ Dispatcher is not running.")

# === START ===
if __name__ == "__main__":
    if not TOKEN:
        print("TOKEN not set. Exiting.")
    else:
        bot.run(TOKEN)