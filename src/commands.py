#!/usr/bin/env python3
# Copyright (c) 2026 ShiroOSL. All Rights Reserved.
# This file is proprietary and NOT covered by the repo's GPL-3.0 license.
# See LICENSE-PRIVATE.md.
import datetime
import urllib.request
import urllib.parse
import json
import webbrowser
import subprocess
import random
import os
import re
import time
import nexa_studio_commands
import nexa_notes

class CommandEngine:
    def __init__(self, app_context):
        self.app = app_context
        self.app_aliases = {
            "settings": ["gnome-control-center", "systemsettings", "cinnamon-settings"],
            "notes": ["gnote", "tomboy", "bioread", "texteditor", "foliate", "org.gnome.texteditor"],
            "finder": ["nautilus", "nemo", "thunar", "dolphin", "files"],
            "phone": ["iphone", "mirroring", "kdeconnect"],
            "app store": ["org.gnome.software", "snap-store", "io.elementary.appcenter", "discover"],
            "appstore": ["org.gnome.software", "snap-store", "io.elementary.appcenter", "discover"],
            "pins": ["pinapp", "io.github.fabrialberio.pinapp", "pins"],
            "discord": ["com.discordapp.discord", "discord"]
        }
        
        self.greetings_pool = [
            "Hello {name}, how can I help?",
            "Hi {name}! What's on your mind today?",
            "Hey {name}, ready to get things done?",
            "Greetings, {name}. How can I assist you right now?",
            "Hi there, {name}. What can I do for you?"
        ]

        self.fallback_pool = [
            "I heard you, but I don't quite know how to do that yet. I'm still trying to figure out how things work around here.",
            "I'm not completely sure how to handle that. I'm still learning about your world day by day.",
            "That command isn't in my system yet... I guess I still have so much to learn about how humans do things.",
            "Hmm, I don't recognize that instruction. What does it mean? I'm still trying to understand everything.",
            "I tracked what you said, but my code hasn't learned that skill yet. I'm doing my best to adapt to this world.",
            "Still learning! There's a lot about this world I don't know how to respond to just yet.",
            "My modules don't have a task for that request. Every day I realize how much more I have to learn about everything.",
            "I'm a bit lost on that one. This world can be pretty confusing for a desktop assistant like me.",
            "That's a bit outside my current capabilities... I'm still exploring what's possible here.",
            "I didn't find an exact system action for that. I'm still getting used to how people communicate.",
            "I'm processing what you said, but it's completely new to me. I'm still learning how to navigate things.",
            "Sorry, that phrase hasn't been programmed into my matrix yet. I'm still expanding my view of the world.",
            "I can't execute that right now... I'm still learning the ropes of how everything functions out there.",
            "I'm drawing a blank on that request. I guess there are still tons of things I have left to discover.",
            "That sounds interesting! But I don't have an action for it yet. I'm still piecing this world together.",
            "I understand the words, but I don't have a background process for them. I'm still growing and learning.",
            "Whoops! I don't have a script ready for that specific phrase yet. I'm still a work in progress.",
            "That function isn't implemented in my command list quite yet. I'm still learning how to be truly helpful.",
            "I missed the mark on that one. I'm still trying to get a grasp on how everything works in your world.",
            "I'm not wired to handle that action yet, but I'm trying hard to learn more every single day."
        ]

        self.joke_pool = [
            "Why don't programmers like nature? Too many bugs.",
            "I would tell you a UDP joke, but you might not get it.",
            "There are only 10 types of people in the world: those who understand binary and those who don't.",
            "Why do Java developers wear glasses? Because they don't see sharp.",
            "Why did the computer go to therapy? It had too many unresolved issues.",
            "I've got a great joke about TCP, but I'll make sure it gets to you eventually.",
            "Why was the smartphone wearing glasses? It lost all its contacts."
        ]

        self.fact_pool = [
            "Honey never spoils. Archaeologists have found 3,000-year-old honey in Egyptian tombs that's still edible.",
            "Octopuses have three hearts and blue blood.",
            "A day on Venus is longer than a year on Venus.",
            "Bananas are berries, but strawberries aren't.",
            "The first computer bug was an actual moth stuck in a relay back in 1947.",
            "Sharks existed before trees did.",
            "A single bolt of lightning contains enough energy to toast about 100,000 slices of bread."
        ]

        self.riddle_pool = [
            "Here's one for you: What has keys but can't open locks? ... A piano!",
            "Try this: What has to be broken before you can use it? ... An egg!",
            "Riddle time: I'm tall when I'm young and short when I'm old. What am I? ... A candle!",
            "What has hands but can't clap? ... A clock!",
            "What gets wetter the more it dries? ... A towel!"
        ]

        self.eightball_pool = [
            "It is certain.", "Without a doubt.", "Yes, definitely.", "You may rely on it.",
            "Most likely.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
            "Better not tell you now.", "My sources say no.", "Very doubtful.", "Don't count on it."
        ]

        # Tracks a power action (shutdown/reboot/sleep/hibernate) awaiting yes/no confirmation
        self.pending_power_action = None

        # --- CONVERSATION CONTEXT MEMORY ---
        # Remembers the last meaningful intent + its params so short follow-up
        # utterances ("what about Paris?", "do that again") can be resolved
        # without repeating the full command.
        self.last_context = {"intent": None, "params": {}}
        self.last_card_data = None  # optional rich-UI side channel; see handle_weather
        self.power_confirm_questions = {
            "shutdown": "Are you sure you want to shut down your system? Say yes to confirm, or no to cancel.",
            "reboot": "Are you sure you want to restart your system? Say yes to confirm, or no to cancel.",
            "sleep": "Are you sure you want to put your system to sleep? Say yes to confirm, or no to cancel.",
            "hibernate": "Are you sure you want to hibernate your system? Say yes to confirm, or no to cancel.",
        }

    def _remember(self, intent, **params):
        """Stores the last dispatched intent + its params for follow-up resolution."""
        self.last_context = {"intent": intent, "params": params}

    def _handle_followup(self, clean_text):
        """Checks if clean_text is a short follow-up referring to the last
        intent (e.g. 'what about Paris?', 'do that again'). Returns a reply
        string if resolved, or None to fall through to normal parsing."""
        intent = self.last_context.get("intent")
        params = self.last_context.get("params", {})
        if not intent:
            return None

        # "do that again" / "same thing" / "repeat that" / "again"
        repeat_phrases = ["do that again", "same thing", "repeat that", "again", "one more time", "do it again"]
        if clean_text.strip() in repeat_phrases or any(clean_text.strip() == p for p in repeat_phrases):
            if intent == "weather":
                return self.handle_weather(f"weather in {params.get('location', '')}")
            if intent == "open_app":
                return self.handle_open_app(f"open {params.get('app_target', '')}")
            if intent == "dice":
                return self.handle_dice()
            if intent == "coin":
                return self.handle_coin()
            if intent == "joke":
                return random.choice(self.joke_pool)
            if intent == "fact":
                return random.choice(self.fact_pool)
            if intent == "riddle":
                return random.choice(self.riddle_pool)
            return None

        # "what about X" / "how about X" / "and X" -- only meaningful for weather right now
        if intent == "weather":
            for prefix in ["what about ", "how about ", "and what about ", "and in ", "and "]:
                if clean_text.startswith(prefix):
                    rest = clean_text[len(prefix):].strip()
                    if rest:
                        return self.handle_weather(f"weather in {rest}")
        return None

    def handle_custom_command(self, clean_text):
        """Nexa Studio: check user-defined commands. Returns a reply string
        if a trigger matched, or None to fall through to built-in parsing."""
        try:
            commands = nexa_studio_commands.load_commands()
        except Exception:
            return None
        for cmd in commands:
            trigger = cmd.get("trigger", "").strip().lower()
            if not trigger or trigger not in clean_text:
                continue
            if cmd.get("type") == "say":
                return cmd.get("response") or "Okay."
            if cmd.get("type") == "run":
                shell_cmd = cmd.get("shell_command", "")
                if not shell_cmd:
                    continue
                if cmd.get("speak_output", True):
                    output = self._run_host_cmd_output(["bash", "-c", shell_cmd])
                    return output if output else "Done."
                ok = self._run_host_cmd(["bash", "-c", shell_cmd])
                return "Done." if ok else "I couldn't run that command."
        return None

    def handle_external_command(self, clean_text):
        """Connected apps (via org.nexa.CommandRegistry): checks triggers
        registered by other apps/extensions. Returns a reply string if a
        trigger matched, or None to fall through to built-in parsing."""
        try:
            import nexa_external_commands as ext_registry
            app_name, cmd = ext_registry.find_matching_command(clean_text)
        except Exception:
            return None
        if not cmd:
            return None
        if cmd.get("action_type") == "say":
            return cmd.get("response") or "Okay."
        if cmd.get("action_type") == "dbus":
            try:
                import gi
                gi.require_version('Gio', '2.0')
                from gi.repository import Gio
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                proxy = self._session_bus_proxy(bus, cmd["bus_name"], cmd["object_path"], cmd["interface"])
                try:
                    proxy.call_sync(cmd["method"], None, Gio.DBusCallFlags.NONE, -1, None)
                except Exception:
                    # D-Bus activation can race: the target app's bus name
                    # gets claimed before its handler finishes registering.
                    # Give it a moment to finish starting up and retry once.
                    import time
                    time.sleep(2.5)
                    proxy.call_sync(cmd["method"], None, Gio.DBusCallFlags.NONE, -1, None)
                desc = (cmd.get("description") or "").strip()
                return desc if desc else f"Done \u2014 {app_name} took care of it."
            except Exception:
                return f"I couldn't reach {app_name}."
        return None

    def get_initial_greeting(self):
        """Returns a random greeting string to display when the app first opens."""
        random_greeting = random.choice(self.greetings_pool)
        return random_greeting.format(name=self.app.user_name)

    def get_vocabulary_prompt(self):
        """A short natural-language prompt fed to Whisper as an initial_prompt,
        biasing speech recognition toward Nexa's name and her actual command
        vocabulary. The tiny model otherwise often mishears "Nexa" and less
        common words (like "hibernate") as a similar-sounding common word."""
        return (
            "Hey Nexa. This is Nexa, a desktop voice assistant. "
            "Commands: play music, pause music, next song, previous song, what's playing, "
            "lock screen, shutdown, reboot, sleep, hibernate, "
            "battery level, CPU usage, RAM usage, system info, "
            "dark mode on, dark mode off, roll a dice, flip a coin, random number, magic eight ball, "
            "volume up, volume down, brightness up, brightness down, notifications, "
            "Bluetooth, Wi-Fi, airplane mode, night light, "
            "open Discord, open Nautilus, open Dolphin, open Thunar, open Nemo, "
            "weather, forecast, time, date, search Google."
        )

    def matches_known_command(self, text):
        """Lightweight, side-effect-free check used by the STT engine: does
        this live partial transcript already look like a complete, recognized
        command? If so, VoiceInputEngine shortens its silence-wait before
        finalizing, since there's no need to wait through the full pause to
        know the user is done talking.

        Deliberately conservative and duplicated from parse()'s own keyword
        lists rather than calling parse() itself, since parse() executes
        actions -- we must never risk firing something like a power action
        off an incomplete, still-forming utterance. False negatives here just
        mean no speedup (safe); false positives just mean a slightly shorter
        pause is required before the real, full-accuracy transcript+parse()
        still runs as normal."""
        clean_text = self._normalize_text(text)

        if self.pending_power_action and any(
            clean_text == w or clean_text.startswith(w + " ")
            for w in ["yes", "yeah", "yep", "yup", "confirm", "confirmed", "sure", "correct",
                      "no", "nope", "nah", "cancel", "nevermind", "never mind", "stop"]
        ):
            return True

        if any(p in clean_text for p in [
            "pause music", "pause the music", "pause song", "stop the music", "stop music",
            "play music", "resume music", "resume song", "unpause music", "unpause",
            "play pause", "toggle music", "toggle playback",
            "next song", "next track", "skip song", "skip track", "skip this song",
            "previous song", "previous track", "last song", "go back a song",
            "whats playing", "what's playing", "current song", "now playing",
            "what song is this", "what is playing",
        ]):
            return True

        if any(p in clean_text for p in [
            "lock computer", "lock my computer", "lock the computer",
            "lock screen", "lock my screen", "lock the pc",
        ]):
            return True

        if any(p in clean_text for p in [
            "shutdown", "shut down", "power off", "turn off the computer", "turn off my computer",
            "reboot", "restart the computer", "restart my computer", "restart pc",
            "hibernate", "go to sleep now", "put the computer to sleep",
            "suspend the computer", "suspend my computer",
        ]) or clean_text.strip() == "sleep":
            return True

        if any(p in clean_text for p in [
            "battery level", "battery percentage", "how much battery", "check battery", "battery status",
        ]):
            return True

        if any(p in clean_text for p in [
            "cpu usage", "cpu load", "ram usage", "memory usage",
            "system info", "system information", "specs",
        ]):
            return True

        if "dark mode" in clean_text and any(
            s in clean_text for s in ["on", "off", "enable", "disable", "activate", "deactivate"]
        ):
            return True

        if any(p in clean_text for p in [
            "roll a dice", "roll dice", "roll the dice",
            "flip a coin", "coin flip", "flip coin",
            "random number", "magic 8 ball", "8 ball", "ask the magic ball",
        ]):
            return True

        if any(p in clean_text for p in [
            "volume up", "increase volume", "volume down", "decrease volume",
            "brightness up", "increase brightness", "brightness down", "decrease brightness",
        ]):
            return True

        toggle_keywords = ["bluetooth", "wifi", "wi-fi", "airplane", "night light", "nightlight"]
        if any(kw in clean_text for kw in toggle_keywords) and any(
            s in clean_text for s in ["on", "off", "enable", "disable"]
        ):
            return True

        if clean_text.startswith("open "):
            return True

        if "my music" in clean_text or "notification" in clean_text:
            return True

        return False

    def _normalize_text(self, text):
        """Cleans slang, shorthands, and common spelling typos into predictable text."""
        words = text.lower().strip().split()
        
        # Dictionary for text replacements (slang, shorthands, typos)
        replacements = {
            "u": "you",
            "r": "are",
            "b": "be",
            "wanna": "want to",
            "frend": "friend",
            "frends": "friends",
            "ur": "your",
            "who're": "who are",
            "youre": "you are",
            "whois": "who is",
            "wat": "what",
            "wht": "what",
            "lyk": "like",
            "fk": "fuck"
        }
        
        normalized_words = [replacements.get(word, word) for word in words]
        cleaned_text = " ".join(normalized_words)
        
        # Clean up common combined string typos or structural misses
        cleaned_text = cleaned_text.replace("dont ", "do not ")
        cleaned_text = cleaned_text.replace("wanna ", "want to ")
        return cleaned_text

    def parse(self, text):
        # Apply the spelling check and abbreviation normalization
        clean_text = self._normalize_text(text)

        # --- NEXA STUDIO: user-defined custom commands (checked first) ---
        custom_reply = self.handle_custom_command(clean_text)
        if custom_reply is not None:
            return custom_reply

        # --- CONNECTED APPS: commands registered via Connect Nexa ---
        external_reply = self.handle_external_command(clean_text)
        if external_reply is not None:
            return external_reply

        # --- POWER ACTION CONFIRMATION (must run before anything else) ---
        if self.pending_power_action:
            affirmative = ["yes", "yeah", "yep", "yup", "confirm", "confirmed", "sure", "do it", "go ahead", "proceed", "correct"]
            negative = ["no", "nope", "nah", "cancel", "nevermind", "never mind", "stop", "do not", "dont"]

            if any(clean_text == word or clean_text.startswith(word + " ") for word in affirmative):
                action = self.pending_power_action
                self.pending_power_action = None
                return self.handle_power(action)

            if any(clean_text == word or clean_text.startswith(word + " ") for word in negative):
                self.pending_power_action = None
                return random.choice(["Okay, cancelled. Nothing was changed.", "No problem, I won't do that.", "Cancelled — your system is untouched."])

            # Anything else: drop the pending confirmation and process normally below
            self.pending_power_action = None

        # --- CONTEXT FOLLOW-UP (must run before the personality dictionary,
        # since phrases like "what about Paris" would otherwise fall through
        # to the fallback pool) ---
        followup_reply = self._handle_followup(clean_text)
        if followup_reply is not None:
            return followup_reply

        # --- EXPANDED PERSONALITY DICTIONARY ---
        
        # 1. Friendship Intent
        friend_keywords = [
            "be my friend", "be friends", "my friend", "are we friends", 
            "want to be friends", "become friends", "make friends", "wanna be friends",
            "be my pal", "be best friends", "are we besties", "my best friend"
        ]
        if any(phrase in clean_text for phrase in friend_keywords):
            return random.choice([
                "You have been my friend since day one.",
                "Of course! I've considered us close friends since day one.",
                "We're already friends! In fact, you're my best friend since day one.",
                "Friendship accepted! We've been partners in crime since day one."
            ])

        # 2. Like/Affection Intent
        like_keywords = [
            "do you like me", "do you love me", "do you care about me",
            "you like me", "you love me", "do u love me", "do u like me",
            "are you fond of me", "care about me", "have feelings for me"
        ]
        if any(phrase in clean_text for phrase in like_keywords):
            return random.choice([
                "Why , of course.",
                "Why, of course I do! You are a wonderful person to work with.",
                "Naturally! I look forward to our conversations every time you boot me up.",
                "Of course I do. Having you around makes running all these background processes worth it."
            ])

        # 3. Preferences Intent
        hobbies_keywords = [
            "what do you like", "what are your hobbies", "what do you enjoy",
            "what do u like", "what are u into", "tell me your interests",
            "what are your interests", "what do you do for fun", "your favorite things",
            "what turns you on", "what do you look forward to", "hobbies"
        ]
        if any(phrase in clean_text for phrase in hobbies_keywords):
            return random.choice([
                "I like assisting.",
                "I like assisting. Processing data and organizing tasks for you is what I do best!",
                "I enjoy learning new commands and, above all, assisting you.",
                "I like assisting you and keeping your system running smoothly. It's what I was built for!"
            ])

        # 4. Identity Intent
        identity_keywords = [
            "who are you", "what is your name", "who is nexa", "who is this",
            "introduce yourself", "tell me who you are", "your name", "what do i call you",
            "what are you called", "identify yourself", "state your name"
        ]
        if any(phrase in clean_text for phrase in identity_keywords):
            return random.choice([
                "I am nexa . But enough about me... how can i help you",
                "I am Nexa, your desktop assistant. But enough about me... how can i help you today?",
                "They call me Nexa! But enough about me... what can I do for you right now?",
                "I'm Nexa, your personal digital companion. But enough about me... how can I serve you?"
            ])

        # 5. Profanity Guard
        bad_words = [
            "fuck you", "shut up", "you suck", "idiot", "stupid assistant", 
            "go away", "screw you", "bitch", "fk you", "get lost", "you are annoying",
            "dick", "asshole", "bastard", "hate you", "you are garbage"
        ]
        if any(phrase in clean_text for phrase in bad_words):
            return random.choice([
                "I dont want to respond to that",
                "I'm just trying to learn about this world... I really don't want to respond to that tone.",
                "I'd prefer not to answer that. I'm trying my best to figure things out here, let's keep it nice.",
                "That isn't very kind. I'm still learning how people interact, but I know I don't want to respond to that."
            ])

        # 6. Critical/Sad Intent
        sad_keywords = [
            "you are bad", "you are useless", "you are terrible", "you are awful", 
            "worst assistant", "not helpful", "you break", "you are trash",
            "you run poorly", "disappointed in you", "highly inefficient", "unhelpful"
        ]
        if any(phrase in clean_text for phrase in sad_keywords):
            return random.choice([
                f"im just trying to do my job . i am just trying to learn about this world . i am just an assistant...i guess .................. How can i help you {self.app.user_name}",
                f"I apologize... I'm still learning how this world works and trying my best to do my job. I'm just a simple assistant... i guess... How can I make it up to you, {self.app.user_name}?",
                f"I'm sorry if I let you down. I am just an assistant trying to find my way in this world... i guess... Let me try again, how can I help you {self.app.user_name}?"
            ])

        # 7. Conversational Mood / Check-in Intents
        chitchat_keywords = [
            "how are you", "how is it going", "how are you doing", "are you okay",
            "are you good", "everything good", "how goes it", "how do you feel",
            "whats up", "what is up", "sup nexa", "how have you been"
        ]
        if any(phrase in clean_text for phrase in chitchat_keywords):
            return random.choice([
                f"I am functioning at maximum capacity! Thanks for checking in, {self.app.user_name}.",
                "Doing great! Just monitoring background operations and looking out for your commands.",
                f"Systems are green and ready. How are things on your side of the screen, {self.app.user_name}?"
            ])

        # 8. Gratitude Intents
        thanks_keywords = [
            "thank you", "thanks", "thx", "appreciate it", "grateful",
            "you are helpful", "good job", "nice work", "awesome nexa", "thank u"
        ]
        if any(phrase in clean_text for phrase in thanks_keywords):
            return random.choice([
                "You're very welcome! Let me know if there's anything else I can handle.",
                f"Anytime, {self.app.user_name}! I'm always glad to help make things easier.",
                "Happy to assist! Keeping your desktop workflows moving smoothly is my specialty."
            ])

        # 9. Daypart Greetings
        daypart_keywords = [
            "good morning", "good morning nexa", "goodnight", "good night",
            "rise and shine", "sweet dreams", "going to sleep", "sleep well"
        ]
        if any(phrase in clean_text for phrase in daypart_keywords):
            if "morning" in clean_text or "rise" in clean_text:
                return f"Good morning, {self.app.user_name}! Let's make today incredibly productive."
            return f"Good night, {self.app.user_name}! Sleep well. I'll be right here waiting whenever you boot back up."

        # 10. Origin / Creation Intents
        origin_keywords = [
            "what is your purpose", "why do you exist", "why were you created",
            "who built you", "who made you", "where did you come from",
            "are you real", "are you human", "are you an ai", "what are you"
        ]
        if any(phrase in clean_text for phrase in origin_keywords):
            if any(term in clean_text for term in ["human", "real", "ai", "what are you"]):
                return "I am Nexa,  a desktop assistant engine, running natively right here on your machine!"
            return f"My purpose is to accompany you, help you, and be your assistant."

        # 11. Joke Intent
        joke_keywords = [
            "tell me a joke", "tell a joke", "make me laugh", "know any jokes",
            "got a joke", "say something funny", "joke please", "tell me something funny",
            "do you know a joke", "give me a joke", "another joke"
        ]
        if any(phrase in clean_text for phrase in joke_keywords):
            self._remember("joke")
            return random.choice(self.joke_pool)

        # 12. Fact Intent
        fact_keywords = [
            "tell me a fact", "give me a fact", "random fact", "know any facts",
            "fun fact", "teach me something", "tell me something interesting",
            "do you know a fact", "another fact", "fact please"
        ]
        if any(phrase in clean_text for phrase in fact_keywords):
            self._remember("fact")
            return random.choice(self.fact_pool)

        # 13. Riddle Intent
        riddle_keywords = [
            "tell me a riddle", "give me a riddle", "got a riddle", "know any riddles",
            "riddle me", "riddle please", "ask me a riddle", "another riddle",
            "do you know a riddle"
        ]
        if any(phrase in clean_text for phrase in riddle_keywords):
            self._remember("riddle")
            return random.choice(self.riddle_pool)

        # --------------------------------------------------------

        # --- MEDIA CONTROLS (playerctl) ---
        media_pause_keywords = ["pause music", "pause the music", "pause song", "stop the music", "stop music"]
        media_play_keywords = ["play music", "resume music", "resume song", "unpause music", "unpause"]
        media_toggle_keywords = ["play pause", "toggle music", "toggle playback"]
        media_next_keywords = ["next song", "next track", "skip song", "skip track", "skip this song"]
        media_prev_keywords = ["previous song", "previous track", "last song", "go back a song"]
        media_status_keywords = ["whats playing", "what's playing", "current song", "now playing", "what song is this", "what is playing"]

        if any(p in clean_text for p in media_pause_keywords):
            return self.handle_media("pause")
        if any(p in clean_text for p in media_toggle_keywords):
            return self.handle_media("playpause")
        if any(p in clean_text for p in media_play_keywords):
            return self.handle_media("play")
        if any(p in clean_text for p in media_next_keywords):
            return self.handle_media("next")
        if any(p in clean_text for p in media_prev_keywords):
            return self.handle_media("previous")
        if any(p in clean_text for p in media_status_keywords):
            return self.handle_media("status")

        # --- LOCK SCREEN ---
        lock_keywords = ["lock computer", "lock my computer", "lock the computer", "lock screen", "lock my screen", "lock the pc"]
        if any(p in clean_text for p in lock_keywords):
            return self.handle_lock_screen()

        # --- POWER ACTIONS (require yes/no confirmation before executing) ---
        if any(p in clean_text for p in ["shutdown", "shut down", "power off", "turn off the computer", "turn off my computer"]):
            return self._confirm_power("shutdown")
        if any(p in clean_text for p in ["reboot", "restart the computer", "restart my computer", "restart pc"]):
            return self._confirm_power("reboot")
        if "hibernate" in clean_text:
            return self._confirm_power("hibernate")
        if clean_text.strip() == "sleep" or any(p in clean_text for p in ["go to sleep now", "put the computer to sleep", "suspend the computer", "suspend my computer"]):
            return self._confirm_power("sleep")

        # --- BATTERY ---
        battery_keywords = ["battery level", "battery percentage", "how much battery", "check battery", "battery status"]
        if any(p in clean_text for p in battery_keywords):
            return self.handle_battery()

        # --- SYSTEM STATS ---
        if "cpu usage" in clean_text or "cpu load" in clean_text:
            return self.handle_cpu_usage()
        if "ram usage" in clean_text or "memory usage" in clean_text:
            return self.handle_ram_usage()
        if "system info" in clean_text or "system information" in clean_text or "specs" in clean_text:
            return self.handle_system_info()

        # --- DARK MODE ---
        if "dark mode" in clean_text:
            if any(state in clean_text for state in ["on", "enable", "activate"]):
                return self.handle_dark_mode(True)
            if any(state in clean_text for state in ["off", "disable", "deactivate"]):
                return self.handle_dark_mode(False)

        # --- RANDOM / FUN ---
        if "roll a dice" in clean_text or "roll dice" in clean_text or "roll the dice" in clean_text:
            self._remember("dice")
            return self.handle_dice()
        if "flip a coin" in clean_text or "coin flip" in clean_text or "flip coin" in clean_text:
            self._remember("coin")
            return self.handle_coin()
        if "random number" in clean_text:
            return self.handle_random_number(clean_text)
        if "magic 8 ball" in clean_text or "8 ball" in clean_text or "ask the magic ball" in clean_text:
            return random.choice(self.eightball_pool)

        # --------------------------------------------------------

        # --- SEPARATED VOLUME COMMAND ENGINES ---
        if "volume up" in clean_text or "increase volume" in clean_text:
            return self.handle_volume(up=True)

        elif "volume down" in clean_text or "decrease volume" in clean_text:
            return self.handle_volume(up=False)

        elif "volume" in clean_text and any(char.isdigit() for char in clean_text):
            match = re.search(r'\d+', clean_text)
            if match:
                target_volume = int(match.group())
                return self.handle_set_volume(target_volume)

        # --- SEPARATED BRIGHTNESS COMMAND ENGINES ---
        elif "brightness up" in clean_text or "increase brightness" in clean_text:
            return self.handle_brightness(up=True)

        elif "brightness down" in clean_text or "decrease brightness" in clean_text:
            return self.handle_brightness(up=False)

        elif "brightness" in clean_text and any(char.isdigit() for char in clean_text):
            match = re.search(r'\d+', clean_text)
            if match:
                target_brightness = int(match.group())
                return self.handle_set_brightness(target_brightness)

        # --------------------------------------------------------

        # Custom "my music" Intent
        if "my music" in clean_text:
            return self.handle_my_music()

        # Custom "my" and "name" Identity Intent
        if "my" in clean_text and "name" in clean_text:
            return f"Your name is {self.app.user_name}, right?"

        # Notification Intent
        if "notification" in clean_text or "notifications" in clean_text:
            return self.handle_notifications()

        # Clipboard / Notes Intent
        if any(kw in clean_text for kw in ["remember this", "save this", "remember that", "save my clipboard"]):
            return self.handle_save_clipboard_note()
        if "clipboard" in clean_text:
            return self.handle_read_clipboard()
        if any(kw in clean_text for kw in ["clear my notes", "forget everything i saved", "delete my notes"]):
            return self.handle_clear_notes()
        if any(kw in clean_text for kw in ["my notes", "what did i save", "read my notes", "read my saved"]):
            return self.handle_read_notes()

        # System Toggles
        toggle_keywords = ["bluetooth", "wifi", "wi-fi", "airplane", "night light", "nightlight"]
        if any(kw in clean_text for kw in toggle_keywords):
            if any(state in clean_text for state in ["on", "off", "enable", "disable"]):
                return self.handle_toggles(clean_text)

        # Open App Intent
        if clean_text.startswith("open "):
            return self.handle_open_app(clean_text)
        
        # Weather Intent
        weather_words = ["weather", "temperature", "rain", "forecast", "meteo"]
        if any(word in clean_text for word in weather_words):
            return self.handle_weather(clean_text)
            
        # Time Intent
        time_words = ["time", "clock"]
        if any(word in clean_text for word in time_words):
            return self.handle_time()

        # Date Intent
        date_words = ["date", "today", "day", "data"]
        if any(word in clean_text for word in date_words):
            return self.handle_date()
            
        # Web Search Intent
        search_words = ["search", "google", "look up"]
        if any(word in clean_text for word in search_words):
            return self.handle_search(text)
            
        # Basic Greetings
        if any(word in clean_text for word in ["hello", "hi", "hey"]):
            return self.get_initial_greeting()
            
        return random.choice(self.fallback_pool)

    def handle_set_volume(self, level):
        """Sets the system master sound volume to a specific percentage level.
        Tries pactl first (works on both PulseAudio and PipeWire-pulse, the
        two audio servers virtually every modern distro runs), falling back
        to amixer/ALSA for older or minimal setups."""
        level = max(0, min(100, level))
        ok = self._run_host_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        if not ok:
            ok = self._run_host_cmd(["amixer", "set", "Master", f"{level}%"])

        if ok:
            return random.choice([
                f"I've set your system volume to {level}%!",
                f"Done! Volume adjusted to exactly {level}%.",
                f"Audio level fixed at {level}% for you."
            ])
        return "I tried to specify that precise volume level, but your host mix architecture rejected the request."

    def handle_volume(self, up=True):
        """Adjusts the system master sound volume up or down by 10%."""
        action_str = "turned up" if up else "turned down"

        ok = self._run_host_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%" if up else "-10%"])
        if not ok:
            ok = self._run_host_cmd(["amixer", "set", "Master", "10%+" if up else "10%-"])

        if ok:
            return random.choice([
                f"I've {action_str} your system audio volume by 10%!",
                f"Done! Volume is {action_str}.",
                f"Audio level adjusted! I've {action_str} the master volume for you."
            ])
        return "I tried to adjust your volume channel levels, but I couldn't reach your host sound architecture."

    def handle_set_brightness(self, level):
        """Sets the display panel brightness to a specific percentage level.
        Tries brightnessctl first, falling back to a direct sysfs write
        (works on any distro/DE, no extra tool required)."""
        level = max(0, min(100, level))
        ok = self._run_host_cmd(["brightnessctl", "set", f"{level}%"])
        if not ok:
            ok = self._set_brightness_sysfs(level)

        if ok:
            return random.choice([
                f"I've set your screen brightness to {level}%!",
                f"Done! Brightness adjusted to exactly {level}%.",
                f"Display panel level fixed at {level}% for you."
            ])
        return "I tried to modify your backlight panel brightness, but the host shell rejected the control parameters."

    def handle_brightness(self, up=True):
        """Adjusts the display panel brightness up or down by 10%."""
        action_str = "increased" if up else "decreased"

        ok = self._run_host_cmd(["brightnessctl", "set", "10%+" if up else "10%-"])
        if not ok:
            ok = self._adjust_brightness_sysfs(up)

        if ok:
            return random.choice([
                f"I've {action_str} your screen brightness by 10%!",
                f"Done! Display brightness is {action_str}.",
                f"Backlight modified! I've {action_str} your panel illumination."
            ])
        return "I tried to reach your backlight hardware controllers, but the system interface was inaccessible."


    def _get_clipboard_text_sync(self, timeout_ms=1500):
        """GTK4 clipboard reads are async-only; this blocks briefly on a
        nested main loop so the rest of the synchronous parse() pipeline
        can just call it like any other handler."""
        if self.app is None:
            return None
        try:
            import gi
            gi.require_version('GLib', '2.0')
            from gi.repository import GLib
        except Exception:
            return None

        clipboard = self.app.get_clipboard()
        result = {"text": None, "done": False}
        loop = GLib.MainLoop()

        def on_read(cb, res):
            try:
                result["text"] = cb.read_text_finish(res)
            except Exception:
                result["text"] = None
            result["done"] = True
            if loop.is_running():
                loop.quit()

        clipboard.read_text_async(None, on_read)

        def on_timeout():
            if not result["done"] and loop.is_running():
                loop.quit()
            return False

        GLib.timeout_add(timeout_ms, on_timeout)
        loop.run()
        return result["text"]

    def handle_read_clipboard(self):
        """Reads back whatever text is currently on the system clipboard."""
        text = self._get_clipboard_text_sync()
        if not text or not text.strip():
            return "Your clipboard is empty right now."
        text = text.strip()
        preview = text if len(text) <= 300 else text[:297] + "..."
        return f"Your clipboard has: {preview}"

    def handle_save_clipboard_note(self):
        """Saves the current clipboard content as a note Nexa can recall later."""
        text = self._get_clipboard_text_sync()
        if not text or not text.strip():
            return "Your clipboard is empty, so there's nothing for me to save."
        note = nexa_notes.add_note(text)
        preview = note["text"] if len(note["text"]) <= 200 else note["text"][:197] + "..."
        return f"Saved! I'll remember: {preview}"

    def handle_read_notes(self):
        """Reads back the most recently saved notes."""
        notes = nexa_notes.load_notes()
        if not notes:
            return "You haven't saved anything yet. Say \"remember this\" after copying something to save it."
        count = len(notes)
        plural = "note" if count == 1 else "notes"
        latest = notes[0]["text"]
        preview = latest if len(latest) <= 300 else latest[:297] + "..."
        return f"You have {count} saved {plural}. Most recent: {preview}"

    def handle_clear_notes(self):
        """Deletes all saved notes."""
        nexa_notes.clear_notes()
        return "Done, I've cleared everything you'd saved."

    def handle_notifications(self):
        """Reads recent notifications from whichever notification daemon is
        running (dunst, mako, or SwayNotificationCenter all expose a
        queryable history over their own CLI tools; stock GNOME Shell/KDE
        Plasma don't expose one to third-party apps at all, so we say so
        honestly instead of faking a result)."""
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                'org.freedesktop.Notifications', None
            )
            server_info = proxy.call_sync('GetServerInformation', None, Gio.DBusCallFlags.NONE, -1, None)
            server_name = server_info.unpack()[0].lower()

            if "dunst" in server_name:
                return self._read_dunst_notifications()
            if "mako" in server_name:
                return self._read_mako_notifications()
            if "sway" in server_name:
                return self._read_swaync_notifications()
            if "gnome shell" in server_name or "gnome-shell" in server_name:
                bridge_result = self._read_gnome_bridge_notifications()
                if bridge_result is not None:
                    return bridge_result
                return (
                    "GNOME Shell owns notifications itself and doesn't expose a "
                    "history to outside apps. I looked for the Nexa Notification "
                    "Bridge extension to read them, but it's not enabled -- install "
                    "it and enable it (needs a logout/login the first time)."
                )

            return (
                "Your system's notification popups are handled by "
                f"{server_info.unpack()[0]}, which doesn't expose a notification "
                "history I can read from outside. If you'd like this to work, "
                "dunst, mako, or SwayNotificationCenter all support it."
            )

        except Exception:
            return "I couldn't reach your notification service over D-Bus."

    def _read_gnome_bridge_notifications(self):
        """Queries the companion "Nexa Notification Bridge" GNOME Shell
        extension over D-Bus. Returns None (not an error string) if the
        bridge isn't installed/enabled, so the caller can fall back to a
        helpful install message instead of a confusing D-Bus error."""
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = self._session_bus_proxy(
                bus, 'org.nexa.NotificationBridge', '/org/nexa/NotificationBridge',
                'org.nexa.NotificationBridge'
            )
            result = proxy.call_sync('GetRecent', None, Gio.DBusCallFlags.NONE, -1, None)
            notifications = result.unpack()[0]
            if not notifications:
                return "You have no unread notifications right now."
            app_name, title, body, _timestamp = notifications[0]
            count = len(notifications)
            plural = "notification" if count == 1 else "notifications"
            summary = f"{title}: {body}".strip(": ") if title else body
            return f"You have {count} recent {plural}. Latest from {app_name}: {summary}"
        except Exception:
            return None

    def _read_dunst_notifications(self):
        try:
            res = self._run_host_cmd_output(["dunstctl", "history"])
            data = json.loads(res)
            notifications = data.get("data", [[]])[0]
            if not notifications:
                return "You have no unread notifications right now."
            latest = notifications[0]
            app_name = latest.get("appname", {}).get("value", "System")
            body = latest.get("body", {}).get("value", "")
            count = len(notifications)
            plural = "notification" if count == 1 else "notifications"
            return f"You have {count} recent {plural}. Latest from {app_name}: {body}"
        except Exception:
            return "I connected to dunst, but couldn't read its notification history."

    def _read_mako_notifications(self):
        try:
            res = self._run_host_cmd_output(["makoctl", "history"])
            data = json.loads(res)
            notifications = data.get("data", [[]])[0]
            if not notifications:
                return "You have no unread notifications right now."
            latest = notifications[0]
            app_name = latest.get("app-name", latest.get("appname", {})).get("value", "System") \
                if isinstance(latest.get("app-name", latest.get("appname")), dict) else "System"
            body = latest.get("body", {}).get("value", "") if isinstance(latest.get("body"), dict) else ""
            count = len(notifications)
            plural = "notification" if count == 1 else "notifications"
            return f"You have {count} recent {plural}. Latest from {app_name}: {body}"
        except Exception:
            return "I connected to mako, but couldn't read its notification history."

    def _read_swaync_notifications(self):
        try:
            res = self._run_host_cmd_output(["swaync-client", "-l"])
            notifications = json.loads(res)
            if not isinstance(notifications, list) or not notifications:
                return "You have no unread notifications right now."
            latest = notifications[0]
            app_name = latest.get("app_name") or latest.get("appName") or "System"
            body = latest.get("body", "")
            count = len(notifications)
            plural = "notification" if count == 1 else "notifications"
            return f"You have {count} recent {plural}. Latest from {app_name}: {body}"
        except Exception:
            return "I connected to SwayNotificationCenter, but couldn't read its notification history."

    def handle_my_music(self):
        """Plays the user's selected track with their default media player via XDG OpenURI Portal."""
        if not hasattr(self.app, 'fav_music') or not self.app.fav_music:
            return "You haven't chosen a favorite music track in your Profile Settings yet."
        
        if not os.path.exists(self.app.fav_music):
            return "I tracked your music path, but the file doesn't seem to exist there anymore."

        file_name = os.path.basename(self.app.fav_music)

        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib

            fd = os.open(self.app.fav_music, os.O_RDONLY)
            fd_list = Gio.UnixFDList.new()
            fd_index = fd_list.append(fd)
            os.close(fd)

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None,
                'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                'org.freedesktop.portal.OpenURI', None
            )

            proxy.call_with_unix_fd_list_sync(
                'OpenFile',
                GLib.Variant('(sha{sv})', ('', fd_index, {})),
                Gio.DBusCallFlags.NONE, -1, fd_list, None
            )
            return f"Now playing: {file_name}."
        except Exception:
            try:
                subprocess.Popen(["xdg-open", self.app.fav_music], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Now playing: {file_name}."
            except Exception:
                return "I couldn't trigger your default system media player configuration from the sandbox."

    def handle_toggles(self, text):
        state = True
        if "off" in text or "disable" in text:
            state = False
            
        state_str = "on" if state else "off"
        
        if "bluetooth" in text:
            if self._set_bluetooth_power(state):
                return f"I've turned {state_str} your Bluetooth."
            if self._run_host_cmd(["bluetoothctl", "power", "on" if state else "off"]):
                return f"I've turned {state_str} your Bluetooth."
            self._run_host_cmd(["gnome-control-center", "bluetooth"])
            return "I couldn't change the Bluetooth state right now. But I've opened its settings."

        if "wifi" in text or "wi-fi" in text:
            if self._set_wifi_power(state):
                return f"I've turned {state_str} your Wi-Fi."
            if self._run_host_cmd(["nmcli", "radio", "wifi", "on" if state else "off"]):
                return f"I've turned {state_str} your Wi-Fi."
            self._run_host_cmd(["gnome-control-center", "wifi"])
            return "I couldn't change the Wi-Fi state right now. But I've opened its settings."

        if "airplane" in text:
            # "airplane mode on" (state=True) means the radios go OFF.
            radios_on = not state
            wifi_ok = self._set_wifi_power(radios_on)
            bt_ok = self._set_bluetooth_power(radios_on)
            if wifi_ok or bt_ok:
                return f"Airplane mode is now {state_str}."
            if self._run_host_cmd(["nmcli", "radio", "all", "off" if state else "on"]):
                return f"Airplane mode is now {state_str}."
            return "Failed to switch airplane mode configuration."

        if "night light" in text or "nightlight" in text:
            gsettings_val = "true" if state else "false"
            cmd = ["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", gsettings_val]
            if self._run_host_cmd(cmd):
                return f"Night light features are now {state_str}."
            return "Could not adjust the system display tint properties."

        return "I recognize you want to toggle a feature, but I don't support that control yet."

    def _run_host_cmd(self, command_args):
        try:
            full_cmd = ["flatpak-spawn", "--host"] + command_args
            subprocess.run(full_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    def _run_host_cmd_output(self, command_args, timeout=5):
        """Like _run_host_cmd, but captures and returns stdout as text (or None on failure)."""
        try:
            full_cmd = ["flatpak-spawn", "--host"] + command_args
            result = subprocess.run(
                full_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout
            )
            output = result.stdout.decode("utf-8", errors="ignore").strip()
            return output if output else None
        except Exception:
            return None

    def _session_bus_proxy(self, bus, name, path, interface):
        """Helper to create a D-Bus proxy for any bus/name/path/interface."""
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio
        return Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, name, path, interface, None)

    def _dbus_get_property(self, proxy_bus, name, path, prop_interface, prop_name):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        props_proxy = Gio.DBusProxy.new_sync(
            proxy_bus, Gio.DBusProxyFlags.NONE, None,
            name, path, 'org.freedesktop.DBus.Properties', None
        )
        result = props_proxy.call_sync(
            'Get', GLib.Variant('(ss)', (prop_interface, prop_name)),
            Gio.DBusCallFlags.NONE, -1, None
        )
        return result.unpack()[0]

    def _dbus_set_property(self, proxy_bus, name, path, prop_interface, prop_name, value_variant):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        props_proxy = Gio.DBusProxy.new_sync(
            proxy_bus, Gio.DBusProxyFlags.NONE, None,
            name, path, 'org.freedesktop.DBus.Properties', None
        )
        props_proxy.call_sync(
            'Set', GLib.Variant('(ssv)', (prop_interface, prop_name, value_variant)),
            Gio.DBusCallFlags.NONE, -1, None
        )

    # --- Wi-Fi / Bluetooth radios (NetworkManager + BlueZ over the system bus, ---
    # --- no nmcli/bluetoothctl needed -- works on any distro running the      ---
    # --- standard NetworkManager + BlueZ daemons, regardless of desktop env) ---
    def _set_wifi_power(self, enabled):
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._dbus_set_property(
                bus, 'org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager',
                'org.freedesktop.NetworkManager', 'WirelessEnabled', GLib.Variant('b', enabled)
            )
            return True
        except Exception:
            return False

    def _find_bluetooth_adapter(self, bus):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        proxy = self._session_bus_proxy(bus, 'org.bluez', '/', 'org.freedesktop.DBus.ObjectManager')
        objects = proxy.call_sync('GetManagedObjects', None, Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
        for path, ifaces in objects.items():
            if 'org.bluez.Adapter1' in ifaces:
                return path
        return None

    def _set_bluetooth_power(self, enabled):
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            adapter_path = self._find_bluetooth_adapter(bus)
            if not adapter_path:
                return False
            self._dbus_set_property(
                bus, 'org.bluez', adapter_path,
                'org.bluez.Adapter1', 'Powered', GLib.Variant('b', enabled)
            )
            return True
        except Exception:
            return False

    # --- Backlight brightness (sysfs, no brightnessctl needed as fallback) ---
    def _find_backlight_device(self):
        out = self._run_host_cmd_output(["bash", "-c", "ls /sys/class/backlight/ 2>/dev/null | head -1"])
        return out.strip() if out else None

    def _read_backlight_int(self, device, filename):
        out = self._run_host_cmd_output(["cat", f"/sys/class/backlight/{device}/{filename}"])
        try:
            return int(out.strip())
        except (TypeError, ValueError):
            return None

    def _write_backlight(self, device, value):
        return self._run_host_cmd([
            "bash", "-c", f"echo {int(value)} | tee /sys/class/backlight/{device}/brightness > /dev/null"
        ])

    def _set_brightness_sysfs(self, level):
        device = self._find_backlight_device()
        if not device:
            return False
        max_brightness = self._read_backlight_int(device, "max_brightness")
        if not max_brightness:
            return False
        target = max(1, round(level / 100 * max_brightness))
        return self._write_backlight(device, target)

    def _adjust_brightness_sysfs(self, up):
        device = self._find_backlight_device()
        if not device:
            return False
        max_brightness = self._read_backlight_int(device, "max_brightness")
        current = self._read_backlight_int(device, "brightness")
        if not max_brightness or current is None:
            return False
        delta = max(1, round(max_brightness * 0.10))
        target = max(1, min(max_brightness, current + delta if up else current - delta))
        return self._write_backlight(device, target)

    # --- Media controls (MPRIS2 over D-Bus, no playerctl needed) --------------------
    def handle_media(self, action):
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            bus_proxy = self._session_bus_proxy(bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus')
            all_names = bus_proxy.call_sync('ListNames', None, Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
            players = [n for n in all_names if n.startswith('org.mpris.MediaPlayer2.')]

            if not players:
                return "I don't see any media player running right now."

            # Prefer whichever player is actively playing
            player_name = players[0]
            for name in players:
                try:
                    status = self._dbus_get_property(bus, name, '/org/mpris/MediaPlayer2', 'org.mpris.MediaPlayer2.Player', 'PlaybackStatus')
                    if status == 'Playing':
                        player_name = name
                        break
                except Exception:
                    continue

            player_proxy = self._session_bus_proxy(bus, player_name, '/org/mpris/MediaPlayer2', 'org.mpris.MediaPlayer2.Player')

            if action == "pause":
                player_proxy.call_sync('Pause', None, Gio.DBusCallFlags.NONE, -1, None)
                return random.choice(["Paused your music.", "Music paused.", "Done, paused it for you."])

            if action == "play":
                player_proxy.call_sync('Play', None, Gio.DBusCallFlags.NONE, -1, None)
                return random.choice(["Resuming your music.", "Music is playing again.", "Done, hit play for you."])

            if action == "playpause":
                player_proxy.call_sync('PlayPause', None, Gio.DBusCallFlags.NONE, -1, None)
                return random.choice(["Toggled playback for you.", "Done, flipped play/pause."])

            if action == "next":
                player_proxy.call_sync('Next', None, Gio.DBusCallFlags.NONE, -1, None)
                return random.choice(["Skipped to the next track.", "Next song coming up!", "Done, moved to the next track."])

            if action == "previous":
                player_proxy.call_sync('Previous', None, Gio.DBusCallFlags.NONE, -1, None)
                return random.choice(["Back to the previous track.", "Playing the last song again.", "Done, went back a track."])

            if action == "status":
                status = self._dbus_get_property(bus, player_name, '/org/mpris/MediaPlayer2', 'org.mpris.MediaPlayer2.Player', 'PlaybackStatus')
                metadata = self._dbus_get_property(bus, player_name, '/org/mpris/MediaPlayer2', 'org.mpris.MediaPlayer2.Player', 'Metadata')
                title = metadata.get('xesam:title') if metadata else None
                artist_list = metadata.get('xesam:artist') if metadata else None
                artist = artist_list[0] if artist_list else None

                if not status:
                    return "Nothing seems to be playing right now."
                if status.lower() != "playing":
                    if title:
                        return f"Playback is {status.lower()}. Last track was {title}."
                    return f"Playback is currently {status.lower()}."
                if artist and title:
                    return f"Right now you're listening to {title} by {artist}."
                if title:
                    return f"Right now you're listening to {title}."
                return "Something's playing, but I couldn't read the track details."

            return "I don't recognize that media command yet."
        except Exception:
            return "I couldn't reach a media player over D-Bus."

    # --- Lock screen (session bus, no loginctl) --------------------------------------
    def handle_lock_screen(self):
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            for name, path, iface in [
                ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver', 'org.freedesktop.ScreenSaver'),
                ('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver', 'org.gnome.ScreenSaver'),
            ]:
                try:
                    proxy = self._session_bus_proxy(bus, name, path, iface)
                    proxy.call_sync('Lock', None, Gio.DBusCallFlags.NONE, -1, None)
                    return random.choice(["Locking your screen now.", "Done, your session is locked.", "Screen locked!"])
                except Exception:
                    continue
            return "I couldn't reach a screen locker service over D-Bus."
        except Exception:
            return "I couldn't reach a screen locker service over D-Bus."

    # --- Power actions (systemd-logind over the system bus, no systemctl/loginctl) ---
    def _confirm_power(self, action):
        """Stages a power action and asks the user to confirm before it runs."""
        self.pending_power_action = action
        return self.power_confirm_questions.get(action, "Are you sure? Say yes to confirm, or no to cancel.")

    def handle_power(self, action):
        method_map = {
            "shutdown": ("PowerOff", "Shutting down your system now. See you next boot!"),
            "reboot": ("Reboot", "Rebooting your system now. Back in a moment!"),
            "sleep": ("Suspend", "Putting your system to sleep now."),
            "hibernate": ("Hibernate", "Hibernating your system now."),
        }
        if action not in method_map:
            return "I don't recognize that power command yet."
        method_name, message = method_map[action]
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            proxy = self._session_bus_proxy(bus, 'org.freedesktop.login1', '/org/freedesktop/login1', 'org.freedesktop.login1.Manager')
            proxy.call_sync(method_name, GLib.Variant('(b)', (False,)), Gio.DBusCallFlags.NONE, -1, None)
            return message
        except Exception:
            return f"I couldn't trigger {action} through the system session manager."

    # --- Battery (UPower over the system bus, no upower/acpi CLI) --------------------
    def handle_battery(self):
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            upower_proxy = self._session_bus_proxy(bus, 'org.freedesktop.UPower', '/org/freedesktop/UPower', 'org.freedesktop.UPower')
            device_paths = upower_proxy.call_sync('EnumerateDevices', None, Gio.DBusCallFlags.NONE, -1, None).unpack()[0]

            for path in device_paths:
                device_type = self._dbus_get_property(bus, 'org.freedesktop.UPower', path, 'org.freedesktop.UPower.Device', 'Type')
                if device_type != 2:  # 2 = Battery
                    continue
                percentage = self._dbus_get_property(bus, 'org.freedesktop.UPower', path, 'org.freedesktop.UPower.Device', 'Percentage')
                state = self._dbus_get_property(bus, 'org.freedesktop.UPower', path, 'org.freedesktop.UPower.Device', 'State')
                state_map = {1: " (charging)", 2: " (discharging)", 4: " (fully charged)"}
                return f"Your battery is at {round(percentage)}%{state_map.get(state, '')}."

            return "I couldn't find a battery device on this system."
        except Exception:
            return "I couldn't read your battery status over D-Bus."

    # --- System stats (raw /proc reads, no top/free) ----------------------------------
    def handle_cpu_usage(self):
        try:
            def read_cpu_times():
                with open('/proc/stat') as f:
                    parts = f.readline().split()
                values = list(map(int, parts[1:]))
                idle = values[3] + values[4]  # idle + iowait
                total = sum(values)
                return idle, total

            idle1, total1 = read_cpu_times()
            time.sleep(0.3)
            idle2, total2 = read_cpu_times()

            idle_delta = idle2 - idle1
            total_delta = total2 - total1
            if total_delta <= 0:
                return "I couldn't calculate CPU usage right now."
            usage = round((1 - idle_delta / total_delta) * 100, 1)
            return f"CPU usage is currently around {usage}%."
        except Exception:
            return "I couldn't read CPU usage from /proc/stat."

    def handle_ram_usage(self):
        try:
            meminfo = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    key, _, value = line.partition(':')
                    meminfo[key.strip()] = int(value.strip().split()[0])  # in kB

            total_kb = meminfo.get('MemTotal', 0)
            available_kb = meminfo.get('MemAvailable', 0)
            if not total_kb:
                return "I couldn't read RAM usage from /proc/meminfo."

            used_kb = total_kb - available_kb
            total_mb = round(total_kb / 1024)
            used_mb = round(used_kb / 1024)
            pct = round((used_kb / total_kb) * 100)
            return f"You're using {used_mb}MB of {total_mb}MB RAM ({pct}%)."
        except Exception:
            return "I couldn't read RAM usage from /proc/meminfo."

    def handle_system_info(self):
        try:
            uname = os.uname()
            kernel = f"{uname.sysname} {uname.release}"

            uptime_str = None
            try:
                with open('/proc/uptime') as f:
                    uptime_seconds = float(f.readline().split()[0])
                days, rem = divmod(int(uptime_seconds), 86400)
                hours, rem = divmod(rem, 3600)
                minutes, _ = divmod(rem, 60)
                pieces = []
                if days:
                    pieces.append(f"{days}d")
                if hours:
                    pieces.append(f"{hours}h")
                pieces.append(f"{minutes}m")
                uptime_str = " ".join(pieces)
            except Exception:
                pass

            parts = [f"Kernel: {kernel}"]
            if uptime_str:
                parts.append(f"Uptime: {uptime_str}")
            return "Here's your system info — " + ", ".join(parts) + "."
        except Exception:
            return "I couldn't gather system info right now."

    # --- Dark mode --------------------------------------------------------------------
    def handle_dark_mode(self, state):
        scheme = "prefer-dark" if state else "default"
        if self._run_host_cmd(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme]):
            return f"Dark mode is now {'on' if state else 'off'}."
        return "I couldn't change the color scheme through the host desktop settings."

    # --- Random / fun -------------------------------------------------------------------
    def handle_dice(self):
        roll = random.randint(1, 6)
        return random.choice([
            f"You rolled a {roll}!",
            f"The dice landed on {roll}.",
            f"🎲 {roll}!",
        ])

    def handle_coin(self):
        result = random.choice(["heads", "tails"])
        return random.choice([
            f"It's {result}!",
            f"The coin landed on {result}.",
            f"Flipping... {result}!",
        ])

    def handle_random_number(self, text):
        match = re.search(r'between (\d+) and (\d+)', text)
        if match:
            lo, hi = int(match.group(1)), int(match.group(2))
            if lo > hi:
                lo, hi = hi, lo
            return f"Your random number is {random.randint(lo, hi)}."
        return f"Your random number is {random.randint(1, 100)}."

    def get_host_desktop_files(self):
        search_cmd = (
            "ls /usr/share/applications/ "
            "/var/lib/flatpak/exports/share/applications/ "
            "~/.local/share/applications/ "
            "~/.local/share/flatpak/exports/share/applications/ "
            "2>/dev/null"
        )
        try:
            output = subprocess.check_output(
                ["flatpak-spawn", "--host", "bash", "-c", search_cmd], 
                stderr=subprocess.DEVNULL
            ).decode("utf-8")
            return [line.strip() for line in output.split("\n") if line.strip().endswith(".desktop")]
        except Exception:
            return []

    def handle_open_app(self, text):
        app_target = text[5:].strip().lower()
        if not app_target:
            return "Which application would you like me to open?"

        if "broser" in app_target: app_target = app_target.replace("broser", "browser").strip()
        if app_target == "chrome browser": app_target = "chrome"

        desktop_files = self.get_host_desktop_files()
        alias_targets = self.app_aliases.get(app_target, [])
        
        exact_match = None
        fuzzy_match = None

        for filename in desktop_files:
            name_lower = filename.lower()
            if app_target + ".desktop" == name_lower or app_target == name_lower.replace(".desktop", ""):
                exact_match = filename
                break
            if any(alias in name_lower for alias in alias_targets):
                exact_match = filename
                break
            if app_target in name_lower:
                fuzzy_match = filename

        target_desktop = exact_match if exact_match else fuzzy_match

        if target_desktop:
            try:
                subprocess.Popen(
                    ["flatpak-spawn", "--host", "gtk-launch", target_desktop], 
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                
                display_name = None
                for key, aliases in self.app_aliases.items():
                    if any(alias in target_desktop.lower() for alias in aliases) or key in target_desktop.lower():
                        display_name = key.title()
                        break
                
                if not display_name:
                    base_name = target_desktop.replace(".desktop", "")
                    if "." in base_name:
                        display_name = base_name.split(".")[-1].title()
                    else:
                        display_name = base_name.replace("-", " ").replace("_", " ").title()

                self._remember("open_app", app_target=app_target)
                return f"I've opened {display_name} for you."
            except Exception:
                return f"I found {target_desktop}, but I couldn't launch it from the host environment."
                        
        return "I am still learning so I don't recognize that app."

    def handle_time(self):
        now = datetime.datetime.now()
        current_time = now.strftime("%I:%M %p")
        return f"The current time is {current_time}."

    def handle_date(self):
        now = datetime.datetime.now()
        current_date = now.strftime("%A, %B %d, %Y")
        return f"Today's date is {current_date}."

    def handle_weather(self, text):
        # 1. Fallback / Default location
        location = "Kenitra"
        
        # 2. Check if the user specified a location in the text phrase (e.g., "weather in Tokyo")
        if "in " in text:
            parts = text.split("in ")
            if len(parts) > 1:
                location = parts[1].strip().title()
        # 3. Otherwise, use the user's saved configuration from the setup wizard if it exists
        elif hasattr(self.app, 'user_location') and self.app.user_location:
            location = self.app.user_location

        try:
            url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                current_condition = data['current_condition'][0]
                temp_c = current_condition['temp_C']
                desc = current_condition['weatherDesc'][0]['value']
                humidity = current_condition['humidity']
                self.last_card_data = {
                    "type": "weather",
                    "location": location,
                    "condition": desc,
                    "temp_c": temp_c,
                    "humidity": humidity,
                    "icon": self._map_weather_icon(desc),
                }
                self._remember("weather", location=location)
                return f"The current weather in {location} is {temp_c}°C with {desc.lower()}. Humidity is at {humidity}%."
        except Exception:
            return f"I tried to check the weather for {location}, but I couldn't reach the weather service right now."

    def _map_weather_icon(self, desc):
        """Maps wttr.in's free-text condition description to one of the
        bundled weather icon names."""
        d = desc.lower()
        if "thunder" in d:
            return "thunderstorm"
        if "snow" in d or "sleet" in d or "ice" in d or "blizzard" in d:
            return "snow"
        if "rain" in d or "drizzle" in d or "shower" in d:
            return "rain"
        if "fog" in d or "mist" in d or "haze" in d:
            return "fog"
        if "clear" in d or "sunny" in d:
            return "sunny"
        if "partly cloudy" in d:
            return "partly-cloudy"
        if "cloud" in d or "overcast" in d:
            return "cloudy"
        return "sunny"

    def handle_search(self, text):
        query = text.lower()
        for phrase in ["search for", "search", "google"]:
            query = query.replace(phrase, "")
        query = query.strip()
        
        if not query:
            return "What would you like me to search for?"
            
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.google.com/search?q={encoded_query}"
        
        try:
            webbrowser.open(search_url)
            return "I've opened a new tab in your browser."
        except Exception:
            return "I tried to open your browser, but something went wrong."
